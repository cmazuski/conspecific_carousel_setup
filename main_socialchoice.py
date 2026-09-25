# main_socialchoice.py
# Entry point for Social Choice training and task (rat and mouse).
# Run: python main_socialchoice.py
#
# Phases:
#   learning   — port C LED → poke → reward (autoshaping)
#   one_choice — port A LED → port C LED → poke C within window → reward
#   two_choice — port A (sucrose) or port B (social stimulus 10s); anti-bias

import time
import signal
import os
import json
from datetime import datetime

from serial_comm import DeviceConnection
from hardware import (
    SharedSensorState, EventLogger, STOP_EVENT, shutdown_outputs,
    turn_table_degrees, apply_motor_speeds,
)
from SocialChoice.setup_dialog import SCSetupDialog


def handle_sigint(_sig, _frame):
    STOP_EVENT.set()
    print("[INFO] Stopping...")


signal.signal(signal.SIGINT, handle_sigint)


def _save_metadata(save_dir, params):
    meta = dict(params)
    meta["timestamp"] = datetime.now().isoformat()
    path = os.path.join(save_dir, "metadata.json")
    with open(path, "w") as f:
        json.dump(meta, f, indent=2, default=str)
    print(f"[INFO] Metadata saved: {path}")


def _run_loop(session, shared, sensor_gui, perf_gui):
    while session.running and not STOP_EVENT.is_set():
        snap = shared.get()
        sensor_gui.update(snap)
        perf_gui.update(session.snapshot(session.results_df))
        time.sleep(0.05)


def main():
    dialog = SCSetupDialog()
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

    from SocialChoice.learning   import LearningSession
    from SocialChoice.one_choice import OneChoiceSession
    from SocialChoice.two_choice import TwoChoiceSession
    from SocialChoice.gui import SensorGUI, PerformanceGUI

    # Output directory
    date_str      = datetime.now().strftime("%Y-%m-%d")
    BASE_SAVE_DIR = os.path.join(
        "SocialChoiceData",
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

    # Connect
    try:
        device = DeviceConnection(port, baudrate=baud)
        device.connect()
        time.sleep(2)
    except Exception as e:
        print(f"[ERROR] Cannot open serial port: {e}")
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

    sensor_gui = SensorGUI()
    perf_gui   = PerformanceGUI(animal_name=animal, phase_selection=phase)

    session    = None
    valve_time = params["valve_time"]
    iti_min    = params["iti_min"]
    iti_max    = params["iti_max"]
    dur_s      = params["session_duration_s"]
    dur_t      = params["session_duration_t"]

    try:
        if phase == "learning":
            session = LearningSession(
                device, shared, species=species,
                valve_time=valve_time,
                iti_min=iti_min, iti_max=iti_max,
                session_duration=dur_s,
            )

        elif phase == "one_choice":
            session = OneChoiceSession(
                device, shared, species=species,
                valve_time=valve_time,
                decision_window=params["decision_window"],
                iti_min=iti_min, iti_max=iti_max,
                session_duration=dur_s,
            )

        elif phase == "two_choice":
            session = TwoChoiceSession(
                device, shared, species=species,
                valve_time=valve_time,
                decision_window=params["decision_window"],
                social_angle=params["social_angle"],
                social_duration=params["social_duration"],
                iti_min=iti_min, iti_max=iti_max,
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

        # Return turntable to home for two_choice (has turntable)
        if phase == "two_choice" and session is not None:
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
        print("[INFO] Clean shutdown complete")


if __name__ == "__main__":
    main()
