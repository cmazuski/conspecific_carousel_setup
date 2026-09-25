# hardware.py  –  new-firmware version
#
# Communication model (new firmware):
#   WRITE  →  [0xCC, register, 0x01, value]   →  ACK [0xCC, register, 0x02, value]
#   READ   →  [0xCC, register, 0x02, 0x00]    →  ACK [0xCC, register, 0x02, value]
#   EVENT  ←  [0xCC, register, 0x03, value]   (unsolicited, sent by firmware)
#
# The DeviceConnection class (serial_comm.py) handles the threading, ACK
# waiting and retry logic.  Session scripts pass a DeviceConnection instance
# wherever the old code accepted a serial.Serial object.

import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple

from protocol import (
    REG_PA_LED, REG_PA_VALVE, REG_PA_IR,
    REG_PB_LED, REG_PB_VALVE, REG_PB_IR,
    REG_PC_LED, REG_PC_VALVE, REG_PC_IR,
    REG_DOOR_SENSOR, REG_TABLE_SENSOR,
    REG_DOOR_STATUS, REG_DOOR_CMD,
    REG_DOOR_OPN_SPD, REG_DOOR_CLS_SPD,
    REG_TABLE_STATUS, REG_TABLE_CMD, REG_TABLE_SPD,
    REG_CAM_A, REG_CAM_B,
    build_table_command,
)
from serial_comm import DeviceConnection
from utils import now

# ── Port register map ─────────────────────────────────────────────────────────

PORT_REGS = {
    "A": {"led": REG_PA_LED, "valve": REG_PA_VALVE, "ir": REG_PA_IR},
    "B": {"led": REG_PB_LED, "valve": REG_PB_VALVE, "ir": REG_PB_IR},
    "C": {"led": REG_PC_LED, "valve": REG_PC_VALVE, "ir": REG_PC_IR},
}

# IR register → port label
_IR_PORT_MAP = {REG_PA_IR: "A", REG_PB_IR: "B", REG_PC_IR: "C"}

# Door status register value → human-readable string (matches old firmware strings)
DOOR_STATUS_STR = {
    0: "door closed",
    1: "door opened",
    2: "door moving",
    3: "door paused",
}

# ── Table positioning ─────────────────────────────────────────────────────────

DEFAULT_TABLE_POSITION = 0

# Position index → angle in degrees
TABLE_POSITIONS = {
    0: 0,    # home
    1: 90,
    2: 180,
    3: 270,
}

current_table_position = DEFAULT_TABLE_POSITION

# ── Globals ───────────────────────────────────────────────────────────────────

SENSOR_HOLD_TIME = 0.1   # seconds a sensor must stay triggered to count as a poke
STOP_EVENT = threading.Event()

# ── Blocking-wait safety ──────────────────────────────────────────────────────
#
# The firmware reports door/table status as one-shot EVENT packets sent only
# when the status *changes*.  A single dropped packet therefore leaves shared
# state permanently stale, and any wait for the state that packet would have
# announced blocks forever — silently, since nothing else in the session prints
# while it waits.  (Observed in ~1 session in 4: the door physically opens, the
# 'door opened' event never arrives, and the trial loop parks on it for the rest
# of the session while the animal keeps working the sensors.)
#
# Two defences, applied to every wait that can block on device state:
#   • resync — re-read the status register directly and correct shared state,
#     so a lost event costs a couple of seconds instead of the session
#   • timeout + heartbeat — never wait unbounded, and say so while waiting

DOOR_OPEN_TIMEOUT = 20.0    # s; opening takes ~2 s in practice
DOOR_CLOSE_TIMEOUT = 30.0   # s; closing takes ~8 s, plus pauses while the animal
                            # blocks the door sensor.  Reaching this is a warning,
                            # not a fault: the trial carries on and the background
                            # close_door_safe keeps working on the door.
DOOR_RESYNC_EVERY = 2.5     # s between direct status re-reads while waiting
HEARTBEAT_EVERY = 30.0      # s between "still waiting" progress lines


class WaitHeartbeat:
    """Prints a periodic progress line while a blocking wait drags on.

    Without this a stalled wait is indistinguishable from a frozen program:
    the session simply stops printing and there is nothing to tell an operator
    which step it died on.
    """

    def __init__(self, what: str, every: float = HEARTBEAT_EVERY):
        self._what = what
        self._every = every
        self._start = time.time()
        self._next = self._start + every

    def tick(self) -> None:
        if self._every <= 0:
            return
        t = time.time()
        if t >= self._next:
            self._next = t + self._every
            # ASCII only — like the resync warning, this must never be the thing
            # that raises on a cp1252 console.
            print(f"[WAITING] {self._what} - {t - self._start:.0f} s so far")

# ── Thread-safe sensor state ──────────────────────────────────────────────────

@dataclass
class SensorSnapshot:
    A: str
    B: str
    C: str
    tA: Optional[datetime]
    tB: Optional[datetime]
    tC: Optional[datetime]
    doorsensor: Optional[str] = None
    tDoorsensor: Optional[datetime] = None
    door: Optional[str] = None
    tDoor: Optional[datetime] = None
    table: Optional[str] = None
    tTable: Optional[datetime] = None
    table_motor: Optional[str] = None
    tTableMotor: Optional[datetime] = None


class SharedSensorState:
    """
    Holds the most recent decoded state for each port/sensor.
    Updated by EventLogger via DeviceConnection.on_event() callbacks.

    State strings mirror the old firmware so that session-task scripts that
    compare e.g.  state == "triggered"  or  state == "door opened"  keep
    working without modification.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.state = {
            "A": "cleared",
            "B": "cleared",
            "C": "cleared",
            "doorsensor": "cleared",
            "table": "cleared",
            "door": "door closed",
            "table_motor": "table stopped",
        }
        self.last_change: dict[str, Optional[datetime]] = {k: None for k in self.state}

    def update(self, port: str, state: str, ts: datetime) -> None:
        with self._lock:
            self.state[port] = state
            self.last_change[port] = ts

    def get(self) -> SensorSnapshot:
        with self._lock:
            return SensorSnapshot(
                A=self.state["A"],
                B=self.state["B"],
                C=self.state["C"],
                tA=self.last_change["A"],
                tB=self.last_change["B"],
                tC=self.last_change["C"],
                doorsensor=self.state["doorsensor"],
                tDoorsensor=self.last_change["doorsensor"],
                table=self.state["table"],
                tTable=self.last_change["table"],
                door=self.state["door"],
                tDoor=self.last_change["door"],
                table_motor=self.state["table_motor"],
                tTableMotor=self.last_change["table_motor"],
            )

    def get_port(self, port: str) -> Tuple[str, Optional[datetime]]:
        with self._lock:
            return self.state[port], self.last_change[port]


# ── Event logger ──────────────────────────────────────────────────────────────

class EventLogger:
    """
    Replaces the old SerialReader + SerialProcessor pair.

    Register this with DeviceConnection.on_event() and it will:
      • translate register/value events into SharedSensorState updates
      • write timestamped CSV event lines to event_log_path
      • optionally write per-interval CSVs for doorsensor and table sensor

    Usage in main script:
        device = DeviceConnection(port, baudrate=115200)
        shared = SharedSensorState()
        logger = EventLogger(shared, event_log_path=sensor_log,
                             session_start=time.time())
        device.on_event(logger)
        device.connect()
    """

    def __init__(
        self,
        shared: SharedSensorState,
        event_log_path: str,
        session_start: float,
        doorsensor_csv_path: Optional[str] = None,
        table_csv_path: Optional[str] = None,
        door_csv_path: Optional[str] = None,
    ):
        self.shared = shared
        self.event_log_path = event_log_path
        self.session_start = session_start
        self.doorsensor_csv_path = doorsensor_csv_path
        self.table_csv_path = table_csv_path
        self.door_csv_path = door_csv_path
        self._doorsensor_event_start: Optional[float] = None
        self._table_event_start: Optional[float] = None

    # Called by DeviceConnection reader thread for every MSG_EVENT packet
    def __call__(self, register: int, value: int) -> None:
        ts = now()
        t = time.time() - self.session_start

        # ── IR beam-break sensors (ports A / B / C) ───────────────────────────
        if register in _IR_PORT_MAP:
            port = _IR_PORT_MAP[register]
            state = "triggered" if value else "cleared"
            prev, _ = self.shared.get_port(port)
            if prev == state:
                print(f"[WARNING] Duplicate state for {port}: {state}")
            self.shared.update(port, state, ts)
            self._log(ts, t, port, state)

        # ── Door proximity sensor ─────────────────────────────────────────────
        elif register == REG_DOOR_SENSOR:
            state = "triggered" if value else "cleared"
            self.shared.update("doorsensor", state, ts)
            self._log(ts, t, "doorsensor", state)
            if self.doorsensor_csv_path:
                self._interval_csv("doorsensor", self.doorsensor_csv_path, state, t)

        # ── Table proximity sensor ────────────────────────────────────────────
        elif register == REG_TABLE_SENSOR:
            state = "triggered" if value else "cleared"
            self.shared.update("table", state, ts)
            self._log(ts, t, "table", state)
            if self.table_csv_path:
                self._interval_csv("table", self.table_csv_path, state, t)

        # ── Mechanical door status ────────────────────────────────────────────
        elif register == REG_DOOR_STATUS:
            state = DOOR_STATUS_STR.get(value, f"door unknown(0x{value:02X})")
            prev, _ = self.shared.get_port("door")
            if prev != state:
                self.shared.update("door", state, ts)
                self._log(ts, t, "door", state)

        # ── Table motor status (moving / stopped) ─────────────────────────────
        elif register == REG_TABLE_STATUS:
            state = "table moving" if value else "table stopped"
            self.shared.update("table_motor", state, ts)
            self._log(ts, t, "table_motor", state)

        # ── Camera sync pulse (captured separately by CameraTriggerLogger) ────
        elif register in (REG_CAM_A, REG_CAM_B):
            pass

        else:
            print(f"[WARNING] Unhandled event: register=0x{register:02X} value={value}")

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _log(self, ts: datetime, t: float, port: str, state: str) -> None:
        with open(self.event_log_path, "a", encoding="utf-8") as f:
            f.write(f"{ts.strftime('%H:%M:%S.%f')[:-3]},{t:.3f},{port},{state}\n")

    def _interval_csv(self, key: str, path: str, state: str, t: float) -> None:
        attr = f"_{key}_event_start"
        if state == "triggered":
            setattr(self, attr, t)
        elif state == "cleared":
            start = getattr(self, attr, None)
            if start is not None:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(f"{start:.3f},{t:.3f}\n")
                setattr(self, attr, None)


# ── Camera trigger capture ─────────────────────────────────────────────────────

class CameraTriggerLogger:
    """
    Logs camera sync-pulse timestamps (register REG_CAM_A by default) for the
    whole session, independent of mode, presentations, or anything the
    animal does in the box — it runs continuously for as long as the camera
    and device connection are up, the same way EventLogger logs door/table/IR
    events regardless of task state.

    The pulse comes from cameracontrol's UserOutput1/Line2 — a brief TTL fired
    once every PULSE_EVERY_N_FRAMES video frames (~1 Hz at 30 fps), not a
    per-frame strobe. It's a checkpoint for relating video frame count to
    wall-clock time (e.g. detecting dropped frames if the interval between
    pulses drifts from the expected N/frame_rate seconds), not a per-frame
    timestamp list.

    Only rising-edge events (value=1) count as a pulse; value=0 is ignored.
    This isn't really "edge detection" in the level-sensor sense (no previous-
    value comparison) — it's a plain value filter, because the firmware (see
    conspecific-carousel/firmware main.py + utility.py's EventPin) fires its
    hard IRQ on both edges, but each pin has a single asyncio.Event flag
    processed one at a time; with cameracontrol's ~1 ms pulse width, the
    falling edge's interrupt routinely fires before the firmware finishes
    processing the rising edge and clears that flag, so the second .set() is
    silently absorbed. In practice the firmware almost never reports a
    matching value=0 for a given pulse — but filtering on value here means we
    don't rely on that being guaranteed.

    Each pulse is written immediately, one line per event, directly to
    log_path (same pattern as EventLogger._log) so pulses already captured
    are safe on disk even if the program is interrupted before a clean
    shutdown.

    Usage:
        camera_logger = CameraTriggerLogger(log_path=camera_sync_path,
                                             session_start=session_start)
        device.on_event(camera_logger)
    """

    def __init__(
        self,
        log_path: str,
        session_start: float,
        register: int = REG_CAM_A,
    ):
        self._register = register
        self._session_start = session_start
        self._log_path = log_path
        self._pulse_count = 0
        with open(self._log_path, "w", encoding="utf-8") as f:
            f.write("pulse_num,t_rel_s\n")

    # Called by DeviceConnection's reader thread for every MSG_EVENT packet
    def __call__(self, register: int, value: int) -> None:
        if register != self._register or value != 1:
            return
        t = time.time() - self._session_start
        self._pulse_count += 1
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(f"{self._pulse_count},{t:.3f}\n")


# ── Hardware control functions ────────────────────────────────────────────────
#
# All functions accept a DeviceConnection as their first argument.
# Call sites that previously passed a serial.Serial object just need to pass
# the DeviceConnection instead — the rest of the call signature is unchanged.

def deliver_reward(device: DeviceConnection, port: str, valve_time: float = 0.15) -> None:
    """Open valve for valve_time seconds then close."""
    reg = PORT_REGS[port]["valve"]
    device.write_register(reg, 1)
    time.sleep(valve_time)
    device.write_register(reg, 0)


def incremental_reward(
    device: DeviceConnection,
    port: str,
    valve_start: float,
    reward_count: int,
    increment: float = 0.033,
    max_valve_time: float = 2.0,
) -> float:
    """Open valve for an incrementally longer time on each reward, capped at
    max_valve_time seconds. Returns actual valve time."""
    valve_time = min(valve_start + (reward_count * increment), max_valve_time)
    reg = PORT_REGS[port]["valve"]
    device.write_register(reg, 1)
    time.sleep(valve_time)
    device.write_register(reg, 0)
    return valve_time


def set_led(device: DeviceConnection, port: str, on: bool) -> None:
    device.write_register(PORT_REGS[port]["led"], 1 if on else 0)


def sensor_held(shared: SharedSensorState, port: str) -> bool:
    """Return True if port sensor stays triggered for SENSOR_HOLD_TIME seconds."""
    start = time.time()
    while time.time() - start < SENSOR_HOLD_TIME:
        if STOP_EVENT.is_set():
            return False
        st, _ = shared.get_port(port)
        if st != "triggered":
            return False
        time.sleep(0.005)
    return True


def shutdown_outputs(device: DeviceConnection) -> None:
    """Turn off all LEDs and valves on ports A, B, C."""
    for p in ("A", "B", "C"):
        device.write_register(PORT_REGS[p]["led"], 0)
        device.write_register(PORT_REGS[p]["valve"], 0)


# ── Motor speed control ────────────────────────────────────────────────────────
#
# Speed values are single bytes (0-255) written to the firmware's speed
# registers. They persist on the device until changed again, so setting them
# once before a session (or once per move) is sufficient.

def set_door_open_speed(device: DeviceConnection, speed: int) -> None:
    device.write_register(REG_DOOR_OPN_SPD, int(speed))


def set_door_close_speed(device: DeviceConnection, speed: int) -> None:
    device.write_register(REG_DOOR_CLS_SPD, int(speed))


def set_table_speed(device: DeviceConnection, speed: int) -> None:
    device.write_register(REG_TABLE_SPD, int(speed))


def apply_motor_speeds(
    device: DeviceConnection,
    door_open_speed: Optional[int] = None,
    door_close_speed: Optional[int] = None,
    table_speed: Optional[int] = None,
) -> None:
    """Write any of the provided motor speeds to the device. None values are skipped."""
    if door_open_speed is not None:
        set_door_open_speed(device, door_open_speed)
    if door_close_speed is not None:
        set_door_close_speed(device, door_close_speed)
    if table_speed is not None:
        set_table_speed(device, table_speed)


# ── Table movement ────────────────────────────────────────────────────────────

def turn_table_degrees(device: DeviceConnection, delta_degrees: int) -> None:
    """
    Turn the table by delta_degrees.

    The new firmware encodes direction + angle in a single byte:
      bit 7 = direction (0 = CW, 1 = CCW)
      bits 6:0 = number of 1/8-turns (45° each)
    """
    if delta_degrees == 0:
        return

    delta = delta_degrees % 360
    if delta > 180:
        delta -= 360

    direction = 0 if delta > 0 else 1   # 0 = CW, 1 = CCW
    angle = abs(delta)
    eighths = angle // 45               # 90° → 2, 180° → 4, 270° → 6

    if eighths == 0:
        raise ValueError(f"Unsupported rotation angle: {angle}° (must be a multiple of 45°)")

    device.write_register(REG_TABLE_CMD, build_table_command(direction, eighths))


def move_table_to_position(device: DeviceConnection, target_position: int) -> None:
    global current_table_position

    if target_position not in TABLE_POSITIONS:
        raise ValueError(f"Unknown table position {target_position}")

    if target_position == current_table_position:
        print(f"Table already at position {target_position}")
        return

    delta = TABLE_POSITIONS[target_position] - TABLE_POSITIONS[current_table_position]
    turn_table_degrees(device, delta)
    current_table_position = target_position


def reset_table_to_default(device: DeviceConnection) -> None:
    move_table_to_position(device, DEFAULT_TABLE_POSITION)


# ── Door control ──────────────────────────────────────────────────────────────

def open_door(device: DeviceConnection) -> None:
    device.write_register(REG_DOOR_CMD, 0x00)


def close_door(
    device: DeviceConnection,
    shared: Optional[SharedSensorState] = None,
    timeout: float = 5,
) -> None:
    """
    Send close command.  If shared state is provided, waits until the door
    has fully opened before issuing the close (same safety logic as old code).
    """
    if shared is not None:
        state, _ = shared.get_port("door")
        if state != "door opened":
            print("[INFO] Waiting for door to reach opened state before closing...")
            success = wait_for_door_state(shared, "door opened", timeout, device=device)
            if not success:
                print("[ERROR] Door failed to open; aborting close.")
                return
    device.write_register(REG_DOOR_CMD, 0x01)


def disable_door_interlock(device: DeviceConnection) -> None:
    """
    Stop the door motor (closest equivalent to the old disable-interlock byte).
    The new firmware does not have a dedicated interlock command; stopping the
    door mid-travel is the safe fallback.
    """
    device.write_register(REG_DOOR_CMD, 0x02)


def close_door_safe(
    device: DeviceConnection,
    shared: SharedSensorState,
    poll_interval: float = 0.02,
    override: Optional[threading.Event] = None,
    timeout: Optional[float] = None,
    held_open: Optional[threading.Event] = None,
) -> None:
    """Close the door with active sensor monitoring.

    Sends the close command then continuously monitors the door proximity
    sensor and the table sensor.  If either triggers during closing the door
    is stopped immediately.  Once both sensors are clear again closing
    resumes automatically.  Blocks until the door reaches 'door closed',
    `timeout` seconds elapse, or STOP_EVENT is set.

    `timeout` (None = wait indefinitely, the historical behavior) bounds how
    long to keep polling for a 'door closed' status that may never arrive —
    e.g. the event was dropped, or the device stopped reporting altogether.
    Without it this loop, and any caller waiting on the same state, would spin
    for the rest of the session.  Time spent held open by `override` does not
    count against it (see below).

    Intended to be called inside a daemon thread so the trial loop is not
    blocked:
        threading.Thread(target=close_door_safe, args=(ser, shared), daemon=True).start()

    Manual override (mechanical-failure recovery)
    ---------------------------------------------
    If `override` (a threading.Event toggled by an operator key/button) is
    supplied, each toggle flips the door between two states while this function
    is closing:
      • 1st toggle → force the door fully OPEN and hold it (halting the close),
                     so the operator can clear an obstruction/jam.
      • 2nd toggle → resume the safe close.
    This can repeat as many times as needed. Any override press that arrives
    before this close starts is discarded (the event is cleared on entry). When
    no override is supplied — or it is never toggled — the door closes exactly
    as before. Because this function still only returns once the door reaches
    'door closed', a caller waiting on that state resumes gracefully after the
    operator finishes.

    `held_open` is an output signal, not an input: it is set while the operator
    is holding the door open and cleared when the close resumes. Pass the same
    event to wait_for_door_state(pause_event=...) so a caller waiting on
    'door closed' with a timeout doesn't expire — and carry on with the door
    still open — while the operator works on the door.
    """
    paused = False       # auto-paused because a proximity sensor is triggered
    forced_open = False  # operator override is currently holding the door open
    if override is not None:
        override.clear()  # ignore any stale press from before this close began
    if held_open is not None:
        held_open.clear()  # this close starts with the door not held by anyone
    device.write_register(REG_DOOR_CMD, 0x01)  # initial close command

    deadline = None if timeout is None else time.time() + timeout
    last_resync = time.time()
    heartbeat = WaitHeartbeat("door to close")

    while not STOP_EVENT.is_set():
        # ── Operator override toggle ──────────────────────────────────────────
        if override is not None and override.is_set():
            override.clear()
            forced_open = not forced_open
            if forced_open:
                device.write_register(REG_DOOR_CMD, 0x00)  # force open
                paused = False
                if held_open is not None:
                    held_open.set()
                print("[OVERRIDE] Door forced OPEN — clear the obstruction, "
                      "then press the override key again to close and resume")
            else:
                device.write_register(REG_DOOR_CMD, 0x01)  # resume close
                if held_open is not None:
                    held_open.clear()
                print("[OVERRIDE] Override released — door closing, resuming program")

        if forced_open:
            # Hold the door open until the operator toggles again; skip the
            # normal sensor/close logic so nothing re-closes it underneath them.
            # The operator is deliberately holding it, so restart the timeout
            # clock rather than expiring while they clear the obstruction.
            if deadline is not None:
                deadline = time.time() + timeout
            time.sleep(poll_interval)
            continue

        door_state, _ = shared.get_port("door")
        if door_state == "door closed":
            return

        # A dropped 'door closed' event would otherwise keep this loop (and the
        # trial waiting on the same state) running against a door that is
        # already shut — re-read the register periodically to catch that.
        if time.time() - last_resync >= DOOR_RESYNC_EVERY:
            last_resync = time.time()
            if resync_door_state(device, shared) == "door closed":
                return

        if deadline is not None and time.time() > deadline:
            print(f"[WARNING] Door not confirmed closed within {timeout:.0f} s "
                  f"(last known state: '{door_state}') — giving up on this close")
            return

        doorsensor_state, _ = shared.get_port("doorsensor")
        table_state, _ = shared.get_port("table")
        sensors_clear = (doorsensor_state == "cleared" and table_state == "cleared")

        if not sensors_clear and not paused:
            device.write_register(REG_DOOR_CMD, 0x02)  # stop
            paused = True
            print("[INFO] Door paused — sensor triggered during closing")

        elif sensors_clear and paused:
            device.write_register(REG_DOOR_CMD, 0x01)  # resume close
            paused = False
            print("[INFO] Door resuming close — sensors cleared")

        heartbeat.tick()
        time.sleep(poll_interval)


# ── Waiting helpers ───────────────────────────────────────────────────────────

RESYNC_FAILURE_LIMIT = 2   # give up on a register after this many failed reads

# Registers whose READ path this device does not answer.  A failed read costs a
# full ACK timeout (retries x timeout) with the device lock held, which would
# stall LED/valve writes on every wait — so once a register has proved
# unreadable, stop asking and fall back to the timeout backstop alone.
_resync_failures: dict = {}
_resync_unsupported: set = set()


def _resync_status(
    device: DeviceConnection,
    shared: SharedSensorState,
    key: str,
    register: int,
    decode,
    label: str,
) -> Optional[str]:
    """Read a status register directly and reconcile it into shared state.

    The firmware only emits a status EVENT when the status *changes*, so a
    dropped or corrupted event packet leaves shared state stale with no second
    announcement ever coming to correct it — e.g. stuck at 'door moving' after
    the door has physically finished opening.  Reading the register asks the
    device what it is doing right now instead of waiting for a message that
    already went missing.

    Returns the freshly read state, or None if the read failed.
    """
    if register in _resync_unsupported:
        return None

    try:
        ack = device.read_register(register)
    except (TimeoutError, AttributeError, OSError) as e:
        print(f"[WARNING] Could not read {label} status register: {e}")
        ack = None

    if not ack or ack[0] != register:
        _resync_failures[register] = _resync_failures.get(register, 0) + 1
        if _resync_failures[register] >= RESYNC_FAILURE_LIMIT:
            _resync_unsupported.add(register)
            print(f"[WARNING] This device does not answer reads of the {label} "
                  f"status register - disabling {label} resync for the rest of "
                  f"the session. Lost {label} events can no longer self-correct; "
                  f"waits will fall back to their timeout.")
        return None

    _resync_failures.pop(register, None)

    state = decode(ack[1])
    prev, _ = shared.get_port(key)
    if prev != state:
        # ASCII arrow on purpose: this line prints from inside a recovery path,
        # and a UnicodeEncodeError on a cp1252 console would turn the recovery
        # into the very crash it exists to prevent.
        print(f"[WARNING] {label.capitalize()} status event was lost - resynced "
              f"by direct read: '{prev}' -> '{state}'")
        shared.update(key, state, now())
    return state


def resync_door_state(
    device: DeviceConnection,
    shared: SharedSensorState,
) -> Optional[str]:
    """Re-read REG_DOOR_STATUS and correct shared state.  See _resync_status."""
    return _resync_status(
        device, shared, "door", REG_DOOR_STATUS,
        lambda v: DOOR_STATUS_STR.get(v, f"door unknown(0x{v:02X})"),
        "door",
    )


def resync_table_motor_state(
    device: DeviceConnection,
    shared: SharedSensorState,
) -> Optional[str]:
    """Re-read REG_TABLE_STATUS and correct shared state.  See _resync_status.

    A dropped 'table stopped' event does not hang the session (the wait is
    bounded), but it does leave shared state reading 'table moving' forever
    after, which costs a full timeout on every subsequent table move.
    """
    return _resync_status(
        device, shared, "table_motor", REG_TABLE_STATUS,
        lambda v: "table moving" if v else "table stopped",
        "table motor",
    )


def wait_for_door_state(
    shared: SharedSensorState,
    target_state: str,
    timeout: Optional[float] = None,
    device: Optional[DeviceConnection] = None,
    resync_every: float = DOOR_RESYNC_EVERY,
    pause_event: Optional[threading.Event] = None,
) -> bool:
    """Block until mechanical door reaches target_state ('door opened', 'door closed', …).

    Pass `device` whenever one is available: it lets this wait recover from a
    lost door-status event by re-reading the status register every
    `resync_every` seconds (see resync_door_state).  Without it, one missing
    event blocks this call — and the session thread behind it — indefinitely.

    `pause_event` (close_door_safe's `held_open`) suspends the timeout while it
    is set.  An operator holding the door open to clear a jam must not be timed
    out: the caller would carry on — and, in a stimulus task, move the turntable
    — with the door still open and someone's hands in it.

    Returns True if the state was reached, False on STOP_EVENT or timeout.
    """
    start_time = time.time()
    last_resync = start_time
    heartbeat = WaitHeartbeat(f"door to reach '{target_state}'")
    while True:
        if STOP_EVENT.is_set():
            return False
        state, _ = shared.get_port("door")
        if state == target_state:
            return True

        t = time.time()
        if pause_event is not None and pause_event.is_set():
            start_time = t   # operator is on the door — restart the clock

        if device is not None and resync_every and (t - last_resync) >= resync_every:
            last_resync = t
            if resync_door_state(device, shared) == target_state:
                return True

        if timeout and (t - start_time) > timeout:
            print(f"[WARNING] Door did not reach '{target_state}' within {timeout}s "
                  f"(last known state: '{state}')")
            return False
        heartbeat.tick()
        time.sleep(0.01)


def wait_for_door_clear(shared: SharedSensorState) -> bool:
    """Block until the door proximity sensor is clear for at least 100 ms."""
    clear_start = None
    heartbeat = WaitHeartbeat("door sensor to clear")
    while True:
        if STOP_EVENT.is_set():
            return False
        state, _ = shared.get_port("doorsensor")
        if state == "cleared":
            if clear_start is None:
                clear_start = time.time()
            if time.time() - clear_start >= 0.1:
                return True
        else:
            clear_start = None
        heartbeat.tick()
        time.sleep(0.01)


def wait_for_table_clear(shared: SharedSensorState) -> bool:
    """Block until the table proximity sensor is clear for at least 100 ms."""
    clear_start = None
    heartbeat = WaitHeartbeat("table sensor to clear")
    while True:
        if STOP_EVENT.is_set():
            return False
        state, _ = shared.get_port("table")
        if state == "cleared":
            if clear_start is None:
                clear_start = time.time()
            if time.time() - clear_start >= 0.1:
                return True
        else:
            clear_start = None
        heartbeat.tick()
        time.sleep(0.01)


def wait_for_table_stopped(
    shared: SharedSensorState,
    timeout: float = 30.0,
    device: Optional[DeviceConnection] = None,
    resync_every: float = DOOR_RESYNC_EVERY,
) -> bool:
    """Block until the table motor stops after a move command.

    Waits up to 0.5 s for the motor to start (firmware latency grace period),
    then blocks until 'table stopped' is reported.  Returns True when stopped,
    False on STOP_EVENT or overall timeout.

    Pass `device` to recover from a lost table-status event by re-reading the
    status register (see resync_table_motor_state); without it a dropped
    'table stopped' burns the full timeout here and on every later move.
    """
    deadline = time.time() + timeout

    # Wait briefly for the motor to start (covers firmware event latency)
    move_seen = False
    grace_end = time.time() + 0.5
    while time.time() < grace_end and not STOP_EVENT.is_set():
        state, _ = shared.get_port("table_motor")
        if state == "table moving":
            move_seen = True
            break
        time.sleep(0.01)

    if not move_seen:
        # Motor never started — zero-distance move or already complete
        return True

    last_resync = time.time()
    heartbeat = WaitHeartbeat("table motor to stop")
    while not STOP_EVENT.is_set():
        if time.time() > deadline:
            print("[WARNING] Table did not stop within timeout")
            return False
        state, _ = shared.get_port("table_motor")
        if state == "table stopped":
            return True

        if device is not None and resync_every and (time.time() - last_resync) >= resync_every:
            last_resync = time.time()
            if resync_table_motor_state(device, shared) == "table stopped":
                return True

        heartbeat.tick()
        time.sleep(0.01)

    return False


def wait_for_door_and_table_clear(shared: SharedSensorState) -> bool:
    """Block until BOTH door and table proximity sensors are clear for at least 100 ms."""
    clear_start = None
    heartbeat = WaitHeartbeat("door and table sensors to clear")
    while True:
        if STOP_EVENT.is_set():
            return False
        door_state, _ = shared.get_port("doorsensor")
        table_state, _ = shared.get_port("table")
        if door_state == "cleared" and table_state == "cleared":
            if clear_start is None:
                clear_start = time.time()
            if time.time() - clear_start >= 0.1:
                return True
        else:
            clear_start = None
        heartbeat.tick()
        time.sleep(0.01)
