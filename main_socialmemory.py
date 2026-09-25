# main_socialmemory.py
# Unified entry point for social memory training and task (rat and mouse).
# Run from the project root: python main_socialmemory.py
#
# A setup GUI opens first to collect all session parameters.
# Parameters are saved as metadata.json in the session output folder.
#
# Modes:
#   training    — classical conditioning (port A/B/C, led_on_time, ITI, reward prob)
#   task        — stimulus presentations (S1 + S2) with CC during each ITI
#   passivetest — pseudorandom presentations of all 4 boxes with CC during each ITI

import time
import signal
import os
import json
import shutil
import subprocess
import sys
import threading
from datetime import datetime

from serial_comm import DeviceConnection
from hardware import (
    SharedSensorState, EventLogger, CameraTriggerLogger, STOP_EVENT, shutdown_outputs,
    turn_table_degrees, apply_motor_speeds,
)
from SocialMemory.setup_dialog import SMSetupDialog

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


def _save_metadata(save_dir, params):
    meta = {k: v for k, v in params.items()}
    meta["timestamp"] = datetime.now().isoformat()
    path = os.path.join(save_dir, "metadata.json")
    with open(path, "w") as f:
        json.dump(meta, f, indent=2, default=str)
    print(f"[INFO] Metadata saved: {path}")


# How long to wait after launching cameracontrol before checking that it's
# still alive. Catches fast-fail startup errors (ffmpeg missing, no camera
# device found) that happen well before the first frame is grabbed — long
# enough to clear pypylon/ffmpeg init, short enough not to stall session start
# noticeably when the camera comes up fine.
CAMERA_STARTUP_CHECK_S = 2.0


def _start_camera_recording(session_start: float, save_dir: str,
                            camera: str = ""):
    """Launch cameracontrol as a background subprocess, sharing this session's
    clock (so its frame_timestamps.csv lines up with sensor_events.csv etc.)
    and writing directly into this session's save folder. Returns the Popen
    handle, or None if the camera couldn't be started (non-fatal — the
    behavioral session continues without video).

    `camera` is the serial number picked in the setup GUI; blank means "first
    camera found" (the only sensible choice with a single camera attached)."""
    if shutil.which("ffmpeg") is None:
        print("[WARN] Could not start camera recording: ffmpeg not found on PATH. "
              "cameracontrol now encodes video through ffmpeg (see its module "
              "docstring) — install it (e.g. `winget install ffmpeg`) and restart. "
              "Continuing session without video.")
        return None
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
        # cameracontrol prints its own errors to the inherited console, but a
        # Popen() call succeeding just means the process launched — it says
        # nothing about whether cameracontrol itself then failed (e.g. no
        # camera device found). Give it a moment and check it's still up
        # before reporting success, so a fast-fail doesn't silently look like
        # a running recording for the rest of the session.
        time.sleep(CAMERA_STARTUP_CHECK_S)
        if proc.poll() is not None:
            print(f"[WARN] Camera process exited immediately (code {proc.returncode}) "
                  f"— see console output above for the error. "
                  f"Continuing session without video.")
            return None
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


# The GUI loops tick every 50 ms: that keeps the live sensor display current
# and lets every window handle its events (move, resize, the door override
# key) promptly. The performance plots, though, are only replotted when a
# results table has gained rows — sessions only ever append rows, never edit
# them, so an unchanged row count means an unchanged figure. On other ticks
# the performance window just handles events (perf_gui.poll()).


def _run_loop_training(session, shared, sensor_gui, perf_gui,
                       session_duration_s=None):
    """Update GUIs every 50 ms while training session runs.
    sensor_gui is None when the live sensor display is turned off."""
    start = time.time()
    last_rows = None   # None forces the first draw
    while session.running and not STOP_EVENT.is_set():
        if session_duration_s and (time.time() - start) >= session_duration_s:
            print(f"[INFO] Session duration ({session_duration_s} s) reached")
            STOP_EVENT.set()
            break
        if sensor_gui is not None:
            sensor_gui.update(shared.get())
        rows = len(session.results_df)
        if rows != last_rows:
            perf_gui.update(session.snapshot(session.results_df))
            last_rows = rows
        else:
            perf_gui.poll()
        time.sleep(0.05)


def _run_loop_task(session, shared, sensor_gui, perf_gui):
    """Update GUIs every 50 ms while task session runs.
    sensor_gui is None when the live sensor display is turned off."""
    last_rows = None   # None forces the first draw (preloads the planned sequence)
    while session.running and not STOP_EVENT.is_set():
        if sensor_gui is not None:
            sensor_gui.update(shared.get())
        rows = (len(session.presentations_df), len(session.conditioning_df))
        if rows != last_rows:
            perf_gui.update(session.snapshot(session.presentations_df),
                             session.snapshot(session.conditioning_df))
            last_rows = rows
        else:
            perf_gui.poll()
        time.sleep(0.05)


def main():
    # ── Setup GUI (parameter GUI) ─────────────────────────────────────────────
    dialog = SMSetupDialog()
    params = dialog.run()
    # Drop the dialog's Tcl interpreter now that the params are out. Destroying
    # its root window doesn't tear down ttk's hidden theme-monitor window — only
    # releasing the interpreter does — and an orphaned monitor re-runs
    # ttk::ThemeChanged against a destroyed "." on every Windows theme
    # broadcast. The whole object has to go: every Var still holds a reference.
    del dialog

    if params is None:
        print("[INFO] Setup cancelled.")
        return

    mode      = params["mode"]
    species   = params["species"]
    animal    = params["animal"]
    session_n = params["session_n"]
    port      = params["port"]
    baud      = params["baud"]

    print(f"[INFO] Setup: {params.get('setup') or '(not set)'}  |  "
          f"Treatment: {params.get('treatment_details') or 'none'}")

    from SocialMemory.training     import ClassicalConditioningSession, AutoRewardSession
    from SocialMemory.task         import SocialMemoryTaskSession
    from SocialMemory.passive_test import PassiveTestSession, generate_box_sequence, label_sequence
    from SocialMemory.gui import SensorGUI, PerformanceGUI

    # ── Session clock + output directory ──────────────────────────────────────
    # Established now (right after the parameter GUI closes, before camera
    # recording or the task GUIs) so every timestamp from this session —
    # sensor events, camera sync pulses, and cameracontrol's own frame
    # timestamps — shares one t=0.
    session_start = time.time()
    timestamp_str = datetime.fromtimestamp(session_start).strftime("%Y%m%d_%H%M%S")
    save_root = params.get("save_root") or "SocialMemoryData" 
    BASE_SAVE_DIR = os.path.join(
        save_root,
        f"{animal}_{session_n}_{mode}_{species}_{timestamp_str}",
    )
    os.makedirs(BASE_SAVE_DIR, exist_ok=True)
    print(f"[INFO] Saving to: {BASE_SAVE_DIR}")

    params["date"]     = timestamp_str
    params["save_dir"] = BASE_SAVE_DIR
    if species == "rat":
        # Hard-coded, so recorded explicitly — sessions before these were
        # lowered used 0.033 s / 2.0 s.
        from SocialMemory.base_session import REWARD_INCREMENT_S, REWARD_MAX_VALVE_S
        params["reward_increment_s"] = REWARD_INCREMENT_S
        params["reward_max_valve_s"] = REWARD_MAX_VALVE_S
    _save_metadata(BASE_SAVE_DIR, params)

    sensor_log   = os.path.join(BASE_SAVE_DIR, "sensor_events.csv")
    camera_sync_log = os.path.join(BASE_SAVE_DIR, "camera_sync.csv")
    perf_fig     = os.path.join(BASE_SAVE_DIR, "performance.png")

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

    # ── Shared state + event logger ───────────────────────────────────────────
    shared = SharedSensorState()
    logger = EventLogger(
        shared,
        event_log_path=sensor_log,
        session_start=session_start,
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

    # ── GUIs (task-related GUIs) ──────────────────────────────────────────────
    # The live sensor window is optional (setup GUI checkbox) — it only
    # visualizes SharedSensorState; logging and the session's own state reads
    # don't depend on it.
    if params.get("show_sensor_display", True):
        sensor_gui = SensorGUI()
    else:
        sensor_gui = None
        print("[INFO] Live sensor display off (not checked in setup)")
    box_labels = None
    expected_periods = None
    passive_sequence = None
    if mode == "task":
        expected_periods = ([f"S1_{i + 1}" for i in range(params["s1_n"])]
                             + [f"S2_{i + 1}" for i in range(params["s2_n"])])
    elif mode == "passivetest":
        box_labels = {i: params["box_ids"][i] for i in range(4)}
        passive_sequence = generate_box_sequence(
            {i: params["box_n"][i] for i in range(4)}
        )
        expected_periods = label_sequence(passive_sequence)

    perf_gui = PerformanceGUI(animal_name=animal, mode=mode,
                               stim1_id=params.get("s1_id"), stim2_id=params.get("s2_id"),
                               box_labels=box_labels, expected_periods=expected_periods)

    session = None

    # ── Run session ───────────────────────────────────────────────────────────
    try:
        if mode == "training":
            if params.get("auto_reward"):
                session = AutoRewardSession(
                    ser=device,
                    shared=shared,
                    species=species,
                    valve_times=params["valve_times"],
                    ports=params["ports"],
                    iti_min=params["iti_min"],
                    iti_max=params["iti_max"],
                    reward_prob=params["reward_prob"],
                    session_duration=params.get("session_duration"),
                    session_start=session_start,
                )
            else:
                session = ClassicalConditioningSession(
                    ser=device,
                    shared=shared,
                    species=species,
                    valve_times=params["valve_times"],
                    ports=params["ports"],
                    led_on_time=params["led_on_time"],
                    iti_min=params["iti_min"],
                    iti_max=params["iti_max"],
                    reward_prob=params["reward_prob"],
                    session_duration=params.get("session_duration"),
                    session_start=session_start,
                )
            session.start()
            mode_desc = "Auto-reward (no LED)" if params.get("auto_reward") else "Training"
            print(f"[INFO] {mode_desc} started on ports {params['ports']} — "
                  f"Ctrl+C to stop")
            _run_loop_training(
                session, shared, sensor_gui, perf_gui,
                session_duration_s=params.get("session_duration"),
            )

        elif mode == "task":
            session = SocialMemoryTaskSession(
                ser=device,
                shared=shared,
                species=species,
                valve_times=params["valve_times"],
                n_s1=params["s1_n"],
                s1_duration=params["s1_duration"],
                s1_angle=params["s1_angle"],
                s1_iti_min=params["s1_iti_min"],
                s1_iti_max=params["s1_iti_max"],
                n_s2=params["s2_n"],
                s2_duration=params["s2_duration"],
                s2_angle=params["s2_angle"],
                s2_iti_min=params["s2_iti_min"],
                s2_iti_max=params["s2_iti_max"],
                cc_ports=params["cc_ports"],
                cc_led_on_time=params["cc_led_on_time"],
                cc_iti_min=params["cc_iti_min"],
                cc_iti_max=params["cc_iti_max"],
                cc_reward_prob=params["cc_reward_prob"],
                cc_delay=params.get("cc_delay", 0.0),
                session_start=session_start,
            )
            door_override = threading.Event()
            session.door_override = door_override
            _bind_door_override(door_override,
                                *[g for g in (sensor_gui, perf_gui) if g is not None])
            session.start()
            print(f"[INFO] Task started — Ctrl+C to stop "
                  f"(press '{DOOR_OVERRIDE_KEY}' with a GUI window focused to "
                  f"force the door open/closed on a jam)")
            _run_loop_task(session, shared, sensor_gui, perf_gui)

        elif mode == "passivetest":
            session = PassiveTestSession(
                ser=device,
                shared=shared,
                species=species,
                valve_times=params["valve_times"],
                box_ids=params["box_ids"],
                box_n=params["box_n"],
                presentation_duration=params["presentation_duration"],
                iti_min=params["iti_min"],
                iti_max=params["iti_max"],
                cc_ports=params["cc_ports"],
                cc_led_on_time=params["cc_led_on_time"],
                cc_iti_min=params["cc_iti_min"],
                cc_iti_max=params["cc_iti_max"],
                cc_reward_prob=params["cc_reward_prob"],
                cc_delay=params.get("cc_delay", 0.0),
                sequence=passive_sequence,
                session_start=session_start,
            )
            door_override = threading.Event()
            session.door_override = door_override
            _bind_door_override(door_override,
                                *[g for g in (sensor_gui, perf_gui) if g is not None])
            session.start()
            print(f"[INFO] Passive test started — Ctrl+C to stop "
                  f"(press '{DOOR_OVERRIDE_KEY}' with a GUI window focused to "
                  f"force the door open/closed on a jam)")
            _run_loop_task(session, shared, sensor_gui, perf_gui)

        else:
            print(f"[ERROR] Unknown mode: {mode}")

    finally:
        print("[INFO] Shutting down...")
        STOP_EVENT.set()

        if session is not None:
            session.stop_internal()

            if mode == "training":
                csv_path = os.path.join(BASE_SAVE_DIR, "trials.csv")
                session.results_df.to_csv(csv_path, index=False, float_format="%.3f")
                print(f"[INFO] Trials saved: {csv_path}")
                # Final GUI update
                perf_gui.update(session.snapshot(session.results_df))

            elif mode in ("task", "passivetest"):
                pres_path = os.path.join(BASE_SAVE_DIR, "presentations.csv")
                cc_path   = os.path.join(BASE_SAVE_DIR, "conditioning_trials.csv")
                session.presentations_df.to_csv(pres_path, index=False, float_format="%.3f")
                session.conditioning_df.to_csv(cc_path, index=False, float_format="%.3f")
                print(f"[INFO] Presentations saved: {pres_path}")
                print(f"[INFO] Conditioning trials saved: {cc_path}")
                perf_gui.update(session.snapshot(session.presentations_df),
                                 session.snapshot(session.conditioning_df))

        # Return turntable to home after task/passivetest (both use the turntable)
        if mode in ("task", "passivetest") and session is not None:
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

        if sensor_gui is not None:
            sensor_gui.update(shared.get())
        shutdown_outputs(device)
        device.disconnect()
        perf_gui.close(save_path=perf_fig)
        if sensor_gui is not None:
            sensor_gui.close()
        _stop_camera_recording(camera_proc)
        print("[INFO] Clean shutdown complete")


if __name__ == "__main__":
    main()
