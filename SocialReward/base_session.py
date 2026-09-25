# base_session.py — shared base class for all social reward training sessions
#
# Subclasses must implement _run_trial(self).
# Subclasses set class-level attributes to customise behaviour:
#   _session_name  (str)  — printed at session start/end
#   ITI_MIN, ITI_MAX      — override for non-default ITI range (default 5–10 s)

import random
import threading
import time
import traceback

import pandas as pd

from hardware import (
    deliver_reward,
    incremental_reward,
    sensor_held,
    shutdown_outputs,
    SharedSensorState,
    STOP_EVENT,
)


class BaseSocialSession:

    _session_name = "Session"
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

        self.port = "C"
        self.trial_counter = 0
        self.reward_count = 0
        self.max_trials = None
        self.running = False
        self.thread = None
        # Subclasses define self.results_df in their own __init__

        # Guards reads/writes of results dataframes shared between the
        # session thread (appends rows) and the main thread (polls for the GUI).
        self._df_lock = threading.Lock()

    # ── Session control ───────────────────────────────────────────────────────

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._run_session, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        STOP_EVENT.set()
        if self.thread is not None:
            self.thread.join()

    # ── Session loop ──────────────────────────────────────────────────────────

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

    # ── Shared helpers ────────────────────────────────────────────────────────

    def _deliver_reward(self) -> float:
        """Deliver a species-appropriate reward. Returns valve time used."""
        if self.species == "rat":
            return incremental_reward(
                self.ser, self.port, self.valve_time, self.reward_count
            )
        deliver_reward(self.ser, self.port, self.valve_time)
        return self.valve_time

    def _wait_for_table_contact(self, deadline: float = None):
        """Wait for table sensor to trigger and measure hold duration.
        Returns (seconds_held, contact_start), or (None, None) if stopped or deadline exceeded."""
        while self.running and not STOP_EVENT.is_set():
            if deadline is not None and time.time() >= deadline:
                return None, None
            state, _ = self.shared.get_port("table")
            if state == "triggered":
                start = time.time()
                while self.running and not STOP_EVENT.is_set():
                    if self.shared.get_port("table")[0] != "triggered":
                        break
                    time.sleep(0.001)
                return time.time() - start, start
            time.sleep(0.001)
        return None, None

    def _wait_for_poke(self, port: str, deadline: float = None) -> bool:
        """Block until port sensor is triggered and held. Returns True/False."""
        while True:
            if not self.running or STOP_EVENT.is_set():
                return False
            if deadline is not None and time.time() >= deadline:
                return False
            state, _ = self.shared.get_port(port)
            if state == "triggered" and sensor_held(self.shared, port):
                return True
            time.sleep(0.001)

    def _wait(self, duration: float) -> None:
        """Block for duration seconds, honouring STOP_EVENT."""
        start = time.time()
        while self.running and not STOP_EVENT.is_set() and (time.time() - start < duration):
            time.sleep(0.02)

    def _run_iti(self, iti: float = None) -> None:
        """Wait for ITI; draws a random value from [ITI_MIN, ITI_MAX] if not given."""
        if iti is None:
            iti = random.uniform(self.ITI_MIN, self.ITI_MAX)
        start = time.time()
        while self.running and not STOP_EVENT.is_set() and (time.time() - start < iti):
            time.sleep(0.05)
