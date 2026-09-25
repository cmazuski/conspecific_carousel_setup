# main_socialreward2AFC.py
# Entry point for 2AFC Social Reward training and task (rat and mouse).
# Run: python main_socialreward2AFC.py
#
# Phases:
#   1      — autoshaping (A+B LEDs, anti-camping)
#   2      — auto-door, sensory min, A/B choice
#   3      — port C init, door, sensory min, A/B choice
#   3b     — port C init, door, gradual sensory min, A/B choice
#   4      — port C init, door, sensory min, A/B with decision window
#   forced — turntable, port C init, sensory min, single correct LED
#   mixed  — adaptive forced/free mixture (75/25 → 50/50 → 25/75)
#   free   — fully free choice (both LEDs, reward only correct port)

import time
import signal
import os  
import json
import subprocess
import sys
from datetime import datetime

from serial_comm import DeviceConnection
from hardware import (
    SharedSensorState, EventLogger, CameraTriggerLogger, STOP_EVENT, shutdown_outputs,
    turn_table_degrees, apply_motor_speeds,
)
from SocialReward2AFC.setup_dialog import SetupDialog2AFC

# cameracontrol lives next to this file, at the repo root.
CAMERACONTROL_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "cameracontrol.py")

# Keyboard key the operator presses (with a GUI window focused) to toggle the
# manual door override during task/passivetest sessions: press once to force the
# door open on a mechanical failure/obstruction, press again to close it and
# resume. See _bind_door_override and hardware.close_door_safe.
DOOR_OVERRIDE_KEY = "d"


def _bind_door_override(override_event, *guis):
    """Let the operator toggle `override_event` by pressing DOOR_OVERRIDE_KEY on
    any of the given matplotlib GUI windows (whichever currently has focus).

    Also removes DOOR_OVERRIDE_KEY from matplotlib's default keyboard shortcuts
    so pressing it doesn't also trigger a built-in figure action.
    """
    import matplotlib.pyplot as plt
    for param, keys in list(plt.rcParams.items()):
        if param.startswith("keymap.") and DOOR_OVERRIDE_KEY in keys:
            keys.remove(DOOR_OVERRIDE_KEY)

    def _handler(event):
        if event.key == DOOR_OVERRIDE_KEY:
            override_event.set()
            print(f"[OVERRIDE] Door override key '{DOOR_OVERRIDE_KEY}' pressed")

    for gui in guis:
        try:
            gui.fig.canvas.mpl_connect("key_press_event", _handler)
        except Exception as e:
            print(f"[WARN] Could not bind door override key on a GUI: {e}")



def handle_sigint(_sig, _frame):
    STOP_EVENT.set()
    print("[INFO] Stopping...")


signal.signal(signal.SIGINT, handle_sigint)


def _build_gradual_hold(thresholds, holds):
    def gradual_hold(session):
        trial = session.trial_counter
        for threshold, hold in zip(thresholds, holds[:-1]):
            if trial < threshold:
                return hold
        return holds[-1]
    return gradual_hold


def _save_metadata(save_dir, params):
    meta = dict(params)
    meta["timestamp"] = datetime.now().isoformat()
    path = os.path.join(save_dir, "metadata.json")
    with open(path, "w") as f:
        json.dump(meta, f, indent=2, default=str)
    print(f"[INFO] Metadata saved: {path}")

def _start_camera_recording(session_start: float, save_dir: str,
                            camera: str = ""):
    """Launch cameracontrol as a background subprocess, sharing this session's
    clock (so its frame_timestamps.csv lines up with sensor_events.csv etc.)
    and writing directly into this session's save folder. Returns the Popen
    handle, or None if the camera couldn't be started (non-fatal — the
    behavioral session continues without video).

    `camera` is the serial number picked in the setup GUI; blank means "first
    camera found" (the only sensible choice with a single camera attached)."""
    try:
        # On Windows, a child process shares the parent's console by default,
        # so Ctrl+C would hit cameracontrol directly too (bypassing its own
        # cleanup). Putting it in its own process group means it only ever
        # stops via the explicit stdin signal below.
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        cmd = [sys.executable, CAMERACONTROL_PATH,
               "--session-start", str(session_start),
               "--save-dir", save_dir,
               # "reuse" loads the saved crop (or full frame if none saved yet)
               # with no prompt — cameracontrol's interactive "ask" default would
               # block forever here since stdin is a pipe we only write to on stop.
               "--crop", "reuse"]
        if camera:
            cmd += ["--camera", camera]
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            creationflags=creationflags,
        )
        print(f"[INFO] Camera recording started (PID {proc.pid}, "
              f"camera {camera or 'first found'}) -> {save_dir}")
        return proc
    except Exception as e:
        print(f"[WARN] Could not start camera recording: {e}")
        return None


def _stop_camera_recording(proc, timeout: float = 15.0):
    """Signal cameracontrol to stop (as if ENTER were pressed) and wait for a
    clean exit; escalates to terminate/kill if it doesn't stop in time."""
    if proc is None:
        return
    print("[INFO] Stopping camera recording...")
    try:
        if proc.stdin is not None and not proc.stdin.closed:
            proc.stdin.write(b"\n")
            proc.stdin.flush()
    except Exception:
        pass  # pipe may already be broken if the process already exited
    finally:
        try:
            if proc.stdin is not None and not proc.stdin.closed:
                proc.stdin.close()
        except Exception:
            pass

    try:
        proc.wait(timeout=timeout)
        print("[INFO] Camera recording stopped cleanly")
    except subprocess.TimeoutExpired:
        print("[WARN] Camera process did not stop in time — terminating")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()



def _run_loop(session, shared, sensor_gui, perf_gui):
    while session.running and not STOP_EVENT.is_set():
        snap = shared.get()
        sensor_gui.update(snap)
        perf_gui.update(session.snapshot(session.results_df))
        time.sleep(0.05)


def main():
    dialog = SetupDialog2AFC()
    params = dialog.run()

    if params is None:
        print("[INFO] Setup cancelled.")
        return

    phase     = params["phase"]
    species   = params["species"]
    animal    = params["animal"]
    session_n = params["session_n"]
    port      = params["port"]
    baud      = params["baud"]

    from SocialReward2AFC.Phase1       import Phase1Session2AFC
    from SocialReward2AFC.Phase2       import Phase2Session2AFC
    from SocialReward2AFC.Phase3       import Phase3Session2AFC
    from SocialReward2AFC.Phase4       import Phase4Session2AFC
    from SocialReward2AFC.ForcedChoice import ForcedChoiceSession
    from SocialReward2AFC.MixedChoice  import MixedChoiceSession
    from SocialReward2AFC.FreeChoice   import FreeChoiceSession
    from SocialReward2AFC.gui import SensorGUI, PerformanceGUI

    # Output directory
    session_start = time.time()
    date_str      = datetime.now().strftime("%Y-%m-%d")
    save_root     = params.get("save_root") or "SocialReward2AFCData"
    BASE_SAVE_DIR = os.path.join(
        save_root,
        f"{animal}_{session_n}_{phase}_{date_str}_{species}",
    )
    os.makedirs(BASE_SAVE_DIR, exist_ok=True)
    print(f"[INFO] Saving to: {BASE_SAVE_DIR}")

    params["date"]     = date_str
    params["save_dir"] = BASE_SAVE_DIR
    _save_metadata(BASE_SAVE_DIR, params)

    sensor_log = os.path.join(BASE_SAVE_DIR, "sensor_events.csv")
    trial_csv  = os.path.join(BASE_SAVE_DIR, "trials.csv")
    perf_fig   = os.path.join(BASE_SAVE_DIR, "performance.png")
    camera_sync_log = os.path.join(BASE_SAVE_DIR, "camera_sync.csv")

    # ── Start camera recording (before device connect / task GUIs) ───────────
    # Optional — only started if the operator checked "Record camera" in the
    # setup GUI (_stop_camera_recording is a no-op on a None handle).
    camera_proc = None
    if params.get("record_camera", True):
        camera_proc = _start_camera_recording(
            session_start, BASE_SAVE_DIR, params.get("camera_serial", ""))
    else:
        print("[INFO] Camera recording disabled (not checked in setup)")

        
    # ── Connect to device ─────────────────────────────────────────────────────
    try:
        device = DeviceConnection(port, baudrate=baud)
        device.connect()
        time.sleep(2)
    except Exception as e:
        print(f"[ERROR] Cannot open serial port: {e}")
        _stop_camera_recording(camera_proc)
        return

    apply_motor_speeds(
        device,
        door_open_speed=params.get("door_open_speed"),
        door_close_speed=params.get("door_close_speed"),
        table_speed=params.get("table_speed"),
    )

    shared = SharedSensorState()
    logger = EventLogger(
        shared,
        event_log_path=sensor_log,
        session_start=time.time(),
    )
    device.on_event(logger)

    # Surface serial faults. Without this every ACK timeout and every reader-thread
    # death is discarded silently — and a dead reader means no sensor events ever
    # arrive again, so the session blocks on device state with nothing printed.
    def _on_serial_error(msg):
        print(f"[ERROR] Serial: {msg}")

    device.on_error(_on_serial_error)

    # Camera sync-pulse timestamps (~1 Hz heartbeat from cameracontrol, not a
    # per-frame strobe) — logged continuously for the whole session, the same
    # way EventLogger logs sensor events, independent of mode/presentations/
    # animal behavior.
    camera_logger = CameraTriggerLogger(
        log_path=camera_sync_log,
        session_start=session_start,
    )
    device.on_event(camera_logger)
    print(f"[INFO] Camera sync-pulse log: {camera_sync_log}")

    # GUI
    sensor_gui = SensorGUI()
    perf_gui   = PerformanceGUI(animal_name=animal, phase_selection=phase)

    session    = None
    valve_time = params["valve_time"]
    iti_min    = params["iti_min"]
    iti_max    = params["iti_max"]
    dur_s      = params["session_duration_s"]
    dur_t      = params["session_duration_t"]

    try:
        if phase == "1":
            session = Phase1Session2AFC(
                device, shared, species=species,
                valve_time=valve_time,
                iti_min=iti_min, iti_max=iti_max,
                session_duration=dur_s,
            )

        elif phase == "2":
            session = Phase2Session2AFC(
                device, shared, species=species,
                sensory_minimum=params["sensory_minimum"],
                valve_time=valve_time,
                session_duration=dur_s,
            )

        elif phase == "3":
            session = Phase3Session2AFC(
                device, shared, species=species,
                sensory_minimum=params["sensory_minimum"],
                valve_time=valve_time,
                session_duration=dur_s,
            )

        elif phase == "3b":
            sensory_min_fn = _build_gradual_hold(
                params["phase3b_thresholds"],
                params["phase3b_holds"],
            )
            session = Phase3Session2AFC(
                device, shared, species=species,
                sensory_minimum=sensory_min_fn,
                valve_time=valve_time,
                session_duration=dur_s,
            )

        elif phase == "4":
            session = Phase4Session2AFC(
                device, shared, species=species,
                sensory_minimum=params["phase4_sensory_min"],
                decision_window=params["phase4_decision_win"],
                valve_time=valve_time,
                session_duration=dur_s,
            )

        elif phase == "forced":
            session = ForcedChoiceSession(
                device, shared, species=species,
                valve_time=valve_time,
                sensory_minimum=params["task_sensory_min"],
                decision_window=params["task_decision_win"],
                angle_a=params["angle_a"],
                angle_b=params["angle_b"],
                session_duration=dur_s,
            )

        elif phase == "mixed":
            session = MixedChoiceSession(
                device, shared, species=species,
                valve_time=valve_time,
                sensory_minimum=params["task_sensory_min"],
                decision_window=params["task_decision_win"],
                angle_a=params["angle_a"],
                angle_b=params["angle_b"],
                start_forced_ratio=params["mixed_start_ratio"],
                session_duration=dur_s,
            )

        elif phase == "free":
            session = FreeChoiceSession(
                device, shared, species=species,
                valve_time=valve_time,
                sensory_minimum=params["task_sensory_min"],
                decision_window=params["task_decision_win"],
                angle_a=params["angle_a"],
                angle_b=params["angle_b"],
                session_duration=dur_s,
            )

        else:
            print(f"[ERROR] Unknown phase: {phase}")
            return

        session.max_trials = dur_t
        session.start()
        print(f"[INFO] Phase {phase} running — press Ctrl+C to stop")
        _run_loop(session, shared, sensor_gui, perf_gui)

    finally:
        print("[INFO] Shutting down...")
        STOP_EVENT.set()

        if session is not None:
            session.stop()
            session.results_df.to_csv(trial_csv, index=False)
            print(f"[INFO] Trials saved: {trial_csv}")
            perf_gui.update(session.results_df)

        # Return turntable to home (box 0) for task phases
        if phase in ("forced", "mixed", "free") and session is not None:
            try:
                def _poll_stopped(timeout=20.0):
                    time.sleep(1.0)
                    deadline = time.time() + timeout
                    while time.time() < deadline:
                        state, _ = shared.get_port("table_motor")
                        if state != "table moving":
                            return
                        time.sleep(0.05)

                print("[INFO] Waiting for any in-progress table move...")
                _poll_stopped()
                current = getattr(session, "_current_angle", 0)
                delta = (0 - current) % 360
                if delta > 180:
                    delta -= 360
                if delta != 0:
                    print(f"[INFO] Returning turntable to home from {current}°...")
                    turn_table_degrees(device, -delta)
                    _poll_stopped()
                print("[INFO] Turntable at home")
            except Exception as e:
                print(f"[WARN] Home return failed: {e}")

        sensor_gui.update(shared.get())
        shutdown_outputs(device)
        device.disconnect()
        perf_gui.close(save_path=perf_fig)
        sensor_gui.close()
        _stop_camera_recording(camera_proc)
        print("[INFO] Clean shutdown complete")


if __name__ == "__main__":
    main()
