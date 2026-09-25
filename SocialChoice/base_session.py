# base_session.py — shared base for all Social Choice sessions
#
# Key hardware context:
#   Door is ALWAYS OPEN — no open_door / close_door calls anywhere.
#   Turntable is used in two_choice to show / hide the social stimulus.
#   Port C = sucrose delivery port.
#   Ports A and B = preference choice ports.
#
# Turntable safety:
#   _wait_for_sensors_clear() blocks until both the table proximity sensor
#   and the door proximity sensor are clear before rotating the turntable,
#   so the motor never moves while the animal is in the way.

import random
import threading
import time
import traceback

import pandas as pd

from hardware import (
    deliver_reward,
    incremental_reward,
    sensor_held,
    wait_for_table_stopped,
    turn_table_degrees,
    shutdown_outputs,
    SharedSensorState,
    STOP_EVENT,
)


class BaseSCSession:

    _session_name = "Social Choice Session"
    ITI_MIN = 5.0
    ITI_MAX = 10.0

    def __init__(
        self,
        ser,
        shared: SharedSensorState,
        species: str,
        valve_time: float,
        session_duration: float = None,
    ):
        self.ser = ser
        self.shared = shared
        self.species = species
        self.valve_time = valve_time
        self.session_duration = session_duration

        self.trial_counter = 0
        self.reward_count  = 0
        self.max_trials    = None
        self.running       = False
        self.thread        = None

        self._current_angle = 0  # local turntable angle tracking

        # Guards reads/writes of results dataframes shared between the
        # session thread (appends rows) and the main thread (polls for the GUI).
        self._df_lock = threading.Lock()

    # ── Session control ───────────────────────────────────────────────────────

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread  = threading.Thread(target=self._run_session, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        STOP_EVENT.set()
        if self.thread is not None:
            self.thread.join()

    def _run_session(self):
        start_time = time.time()
        print(f"[INFO] {self._session_name} started")

        while self.running and not STOP_EVENT.is_set():
            if self.session_duration is not None:
                if time.time() - start_time >= self.session_duration:
                    print("[INFO] Session duration reached")
                    break
            self.trial_counter += 1
            print(f"\n=== Trial {self.trial_counter} ===")
            try:
                self._run_trial()
            except TimeoutError as e:
                print(f"[ERROR] Trial {self.trial_counter} aborted — serial timeout: {e}")
                try:
                    shutdown_outputs(self.ser)
                except TimeoutError:
                    print("[ERROR] Device unresponsive — could not confirm outputs off")
            except Exception:
                # Any other exception would otherwise kill this thread while
                # self.running stayed True, leaving the main GUI loop spinning
                # against a session that has silently stopped running trials.
                # End the session loudly instead.
                print(f"[ERROR] Trial {self.trial_counter} crashed — ending session")
                traceback.print_exc()
                break
            if self.max_trials is not None and self.trial_counter >= self.max_trials:
                print(f"[INFO] Trial limit ({self.max_trials}) reached")
                break

        self.running = False
        print(f"[INFO] {self._session_name} ended")

    def _run_trial(self):
        raise NotImplementedError

    def snapshot(self, df: pd.DataFrame) -> pd.DataFrame:
        """Thread-safe copy of a results dataframe, for reading from the main thread
        while the session thread may be appending rows to it."""
        with self._df_lock:
            return df.copy()

    # ── Reward ────────────────────────────────────────────────────────────────

    def _deliver_reward(self, port: str) -> float:
        if self.species == "rat":
            return incremental_reward(
                self.ser, port, self.valve_time, self.reward_count
            )
        deliver_reward(self.ser, port, self.valve_time)
        return self.valve_time

    # ── Sensor helpers ────────────────────────────────────────────────────────

    def _wait_for_poke(self, port: str, deadline: float = None) -> bool:
        while True:
            if not self.running or STOP_EVENT.is_set():
                return False
            if deadline is not None and time.time() >= deadline:
                return False
            state, _ = self.shared.get_port(port)
            if state == "triggered" and sensor_held(self.shared, port):
                return True
            time.sleep(0.001)

    def _wait_for_any_poke(self, ports, deadline: float = None):
        """Returns port name on first poke, or None on stop/deadline."""
        while True:
            if not self.running or STOP_EVENT.is_set():
                return None
            if deadline is not None and time.time() >= deadline:
                return None
            for port in ports:
                state, _ = self.shared.get_port(port)
                if state == "triggered" and sensor_held(self.shared, port):
                    return port
            time.sleep(0.001)

    def _wait_for_sensors_clear(self) -> bool:
        """Block until the table sensor and door proximity sensor are both clear.
        Safe to call before rotating the turntable.
        Returns False if session stopped."""
        while self.running and not STOP_EVENT.is_set():
            table_state, _  = self.shared.get_port("table")
            snap            = self.shared.get()
            door_prox_clear = (snap.doorsensor != "triggered")
            if table_state == "cleared" and door_prox_clear:
                return True
            time.sleep(0.05)
        return False

    # ── Turntable ─────────────────────────────────────────────────────────────

    def _turn_to(self, target_angle: int) -> str:
        """Rotate turntable to target_angle. Returns 'CW', 'CCW', or 'none'."""
        delta = (target_angle - self._current_angle) % 360
        if delta > 180:
            delta -= 360
        if delta == 0:
            return "none"
        direction = "CW" if delta > 0 else "CCW"
        turn_table_degrees(self.ser, -delta)   # negate: firmware positive = physical CCW
        self._current_angle = target_angle % 360
        return direction

    # ── Timing ───────────────────────────────────────────────────────────────

    def _wait(self, duration: float) -> None:
        start = time.time()
        while self.running and not STOP_EVENT.is_set() and (time.time() - start < duration):
            time.sleep(0.02)

    def _run_iti(self, iti: float = None) -> None:
        if iti is None:
            iti = random.uniform(self.ITI_MIN, self.ITI_MAX)
        start = time.time()
        while self.running and not STOP_EVENT.is_set() and (time.time() - start < iti):
            time.sleep(0.05)
