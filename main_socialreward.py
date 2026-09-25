# main_socialreward.py
# Unified entry point for rat and mouse social reward training.
# Run from the project root: python main_socialreward.py
#
# A setup GUI will open first to collect all session parameters.
# Parameters are saved as metadata.json in the session output folder.
#
# Phases:
#   1    — autoshaping (port C LED, poke, reward)
#   2    — door opens automatically, sensory minimum, port C, poke
#   3a   — port A LED → poke → door opens → sensory minimum → port C → poke
#   3b   — port A LED → poke → door opens → gradual sensory minimum → port C → poke
#   4    — port A LED → poke → door opens → sensory minimum → port C within decision window
#   task — full social-reward task (rewarded / unrewarded table positions)

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
from SocialReward.setup_dialog import SetupDialog


def handle_sigint(_sig, _frame):
    STOP_EVENT.set()
    print("[INFO] Stopping...")


signal.signal(signal.SIGINT, handle_sigint)


def _build_gradual_hold(thresholds, holds):
    """Return a callable that maps trial count → sensory minimum for phase 3b."""
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
        json.dump(meta, f, indent=2)
    print(f"[INFO] Metadata saved: {path}")


def _run_loop(session, shared, sensor_gui, perf_gui):
    """Poll GUIs while the session runs. Trial/time limits are enforced inside the session."""
    while session.running and not STOP_EVENT.is_set():
        snap = shared.get()
        sensor_gui.update(snap)
        planned = getattr(session, "planned_sequence", None)
        perf_gui.update(session.snapshot(session.results_df),
                        current_trial_port="C",
                        planned_sequence=planned)
        time.sleep(0.05)


def main():
    # ── Setup GUI ─────────────────────────────────────────────────────────────
    dialog = SetupDialog()
    params = dialog.run()

    if params is None:
        print("[INFO] Setup cancelled.")
        return

    species = params["species"]
    animal = params["animal"]
    session_n = params["session_n"]
    phase = params["phase"]
    port = params["port"]
    baud = params["baud"]
    valve_time = params["valve_time"]
    session_duration_s = params["session_duration_s"]        # may be None
    session_duration_trials = params["session_duration_trials"]  # may be None
    sensory_minimum_simple = params.get("sensory_minimum", 0.100)  # phases 2 and 3a
    phase4_sensory_min = params["phase4_sensory_min"]
    phase4_decision_window = params["phase4_decision_window"]
    task_sensory_min      = params["task_sensory_min"]
    task_decision_window  = params["task_decision_window"]
    task_rewarded_angle   = params["task_rewarded_angle"]
    task_unrewarded_angle = params["task_unrewarded_angle"]
    stim4_config          = params.get("stim4_config")
    phase3b_thresholds = params["phase3b_thresholds"]
    phase3b_holds = params["phase3b_holds"]

    # ── Output directory + metadata ───────────────────────────────────────────
    date_str = datetime.now().strftime("%Y-%m-%d")
    BASE_SAVE_DIR = os.path.join(
        "SocialRewardData",
        f"{animal}_{session_n}_{phase}_{date_str}_{species}",
    )
    os.makedirs(BASE_SAVE_DIR, exist_ok=True)
    print(f"[INFO] Saving all files to: {BASE_SAVE_DIR}")

    params["date"] = date_str
    params["save_dir"] = BASE_SAVE_DIR
    _save_metadata(BASE_SAVE_DIR, params)

    trial_csv = os.path.join(BASE_SAVE_DIR, "trials.csv")
    sensor_log = os.path.join(BASE_SAVE_DIR, "sensor_events.csv")
    perf_fig_path = os.path.join(BASE_SAVE_DIR, "performance.png")

    # ── Imports ───────────────────────────────────────────────────────────────
    from SocialReward.Phase1 import Phase1Session
    from SocialReward.Phase2 import Phase2Session
    from SocialReward.Phase3 import Phase3Session
    from SocialReward.Phase4 import Phase4Session
    from SocialReward.Task import SocialTaskSession
    from SocialReward.gui import SensorGUI, PerformanceGUI

    # ── Connect to device ─────────────────────────────────────────────────────
    try:
        device = DeviceConnection(port, baudrate=baud)
        device.connect()
        time.sleep(2)
    except Exception as e:
        print(f"Cannot open serial port: {e}")
        return

    apply_motor_speeds(
        device,
        door_open_speed=params.get("door_open_speed"),
        door_close_speed=params.get("door_close_speed"),
        table_speed=params.get("table_speed"),
    )

    # ── Shared state + event logger ───────────────────────────────────────────
    shared = SharedSensorState()
    session_start = time.time()

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

    # ── GUIs ──────────────────────────────────────────────────────────────────
    sensor_gui = SensorGUI()
    perf_gui = PerformanceGUI(animal_name=animal, phase_selection=phase)

    session = None

    # ── Run trials ────────────────────────────────────────────────────────────
    try:
        if phase == "1":
            session = Phase1Session(
                device,
                shared,
                species=species,
                valve_time=valve_time,
                session_duration=session_duration_s,
            )
            session.max_trials = session_duration_trials
            session.start()
            print("[INFO] Phase 1 running — press Ctrl+C to stop")
            _run_loop(session, shared, sensor_gui, perf_gui)
            print("[INFO] Phase 1 session finished")

        elif phase == "2":
            session = Phase2Session(
                device,
                shared,
                species=species,
                sensory_minimum=sensory_minimum_simple,
                valve_time=valve_time,
                session_duration=session_duration_s,
            )
            session.max_trials = session_duration_trials
            print("[INFO] Phase 2 running — press Ctrl+C to stop")
            session.start()
            _run_loop(session, shared, sensor_gui, perf_gui)

        elif phase in ("3a", "3b"):
            sensory_minimum = (
                _build_gradual_hold(phase3b_thresholds, phase3b_holds)
                if phase == "3b"
                else sensory_minimum_simple
            )
            session = Phase3Session(
                device,
                shared,
                species=species,
                sensory_minimum=sensory_minimum,
                valve_time=valve_time,
                session_duration=session_duration_s,
            )
            session.max_trials = session_duration_trials
            print(f"[INFO] Phase {phase} running — press Ctrl+C to stop")
            session.start()
            _run_loop(session, shared, sensor_gui, perf_gui)

        elif phase == "4":
            session = Phase4Session(
                device,
                shared,
                species=species,
                sensory_minimum=phase4_sensory_min,
                decision_window=phase4_decision_window,
                valve_time=valve_time,
                session_duration=session_duration_s,
            )
            session.max_trials = session_duration_trials
            print("[INFO] Phase 4 running — press Ctrl+C to stop")
            session.start()
            _run_loop(session, shared, sensor_gui, perf_gui)

        elif phase == "task":
            session = SocialTaskSession(
                device,
                shared,
                species=species,
                valve_time=valve_time,
                sensory_minimum=task_sensory_min,
                decision_window=task_decision_window,
                rewarded_angle=task_rewarded_angle,
                unrewarded_angle=task_unrewarded_angle,
                session_duration=session_duration_s,
            )
            session.max_trials = session_duration_trials
            session.start()
            # Wait briefly for _run_session to pre-generate planned_sequence
            time.sleep(0.1)
            perf_gui.draw_plan(session.planned_sequence,
                               rewarded_angle=task_rewarded_angle)
            _run_loop(session, shared, sensor_gui, perf_gui)

        elif phase == "4stimuli":
            if stim4_config is None:
                print("[ERROR] 4stimuli config missing — check setup GUI.")
                return
            from SocialReward.Phase4Stimuli import Phase4StimuliSession
            session = Phase4StimuliSession(
                device,
                shared,
                species=species,
                valve_time=valve_time,
                box_config=stim4_config["box_config"],
                sensory_minimum=stim4_config["sensory_min"],
                decision_window=stim4_config["decision_win"],
                session_duration=session_duration_s,
            )
            session.max_trials = session_duration_trials
            session.start()
            time.sleep(0.1)
            # Build rewarded_angle from the first rewarded box for label colouring
            rewarded_boxes = [b for b, cfg in stim4_config["box_config"].items()
                              if cfg["rewarded"]]
            rewarded_angle = rewarded_boxes[0] * 90 if rewarded_boxes else None
            perf_gui.draw_plan(session.planned_sequence, rewarded_angle=rewarded_angle)
            _run_loop(session, shared, sensor_gui, perf_gui)

        else:
            print(f"[ERROR] Unknown phase: {phase}")

    finally:
        print("Shutting down...")
        STOP_EVENT.set()
        if session is not None:
            session.stop()
            session.results_df.to_csv(trial_csv, index=False)
            print(f"[INFO] Trials saved: {trial_csv}")

        # Return turntable to box 0 (home) before powering down.
        # Raw polling loops are used throughout so STOP_EVENT doesn't abort the moves.
        if phase == "task" and session is not None:
            try:
                def _poll_until_stopped(timeout=20.0):
                    time.sleep(1.0)   # grace period for motor-start firmware event
                    deadline = time.time() + timeout
                    while time.time() < deadline:
                        state, _ = shared.get_port("table_motor")
                        if state != "table moving":
                            return
                        time.sleep(0.05)

                # Step 1: let any in-progress move (e.g. killed mid-turn) finish
                print("[INFO] Waiting for any in-progress table move to complete...")
                _poll_until_stopped()

                # Step 2: send home command (negate delta — firmware positive = physical CCW)
                current = getattr(session, "_current_angle", 0)
                delta = (0 - current) % 360
                if delta > 180:
                    delta -= 360
                if delta != 0:
                    print(f"[INFO] Returning turntable to box 0 from logical {current}°...")
                    turn_table_degrees(device, -delta)
                    _poll_until_stopped()
                print("[INFO] Turntable at home")
            except Exception as e:
                print(f"[WARN] Home return failed: {e}")

        shutdown_outputs(device)
        device.disconnect()
        perf_gui.close(save_path=perf_fig_path)
        sensor_gui.close()
        print("[INFO] Clean shutdown complete")


if __name__ == "__main__":
    main()
