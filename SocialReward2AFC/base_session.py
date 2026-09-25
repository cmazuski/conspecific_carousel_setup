# base_session.py — shared base for all SocialReward2AFC sessions
#
# Port roles in 2AFC:
#   Port C  = trial initiation port (poke to trigger door)
#   Port A  = reward port for stimulus assigned to angle_a
#   Port B  = reward port for stimulus assigned to angle_b
#
# Anti-camping (phases 1–4):
#   _get_active_reward_ports() → ["A","B"] normally, or [forced_port] when camping
#   _update_anti_camping(poked_port) → updates history, sets/clears forced_port
#   Rule: if animal pokes same side 3× in a row → force the other side next trial
#         forced mode clears once the forced side is rewarded

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
    WaitHeartbeat,
)


class Base2AFCSession:

    _session_name = "2AFC Session"
    ITI_MIN = 10.0
    ITI_MAX = 15.0

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
        self.reward_count = 0
        self.max_trials = None
        self.running = False
        self.thread = None

        # Anti-camping state (phases 1–4)
        self._poke_history = []   # last 3 reward-port choices (A or B)
        self._forced_port = None  # None = both active; "A"/"B" = camping correction

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

    # ── Reward delivery ───────────────────────────────────────────────────────

    def _deliver_reward(self, port: str) -> float:
        """Deliver species-appropriate reward at port. Returns valve time used."""
        if self.species == "rat":
            return incremental_reward(
                self.ser, port, self.valve_time, self.reward_count
            )
        deliver_reward(self.ser, port, self.valve_time)
        return self.valve_time

    # ── Anti-camping (phases 1–4) ─────────────────────────────────────────────

    def _get_active_reward_ports(self):
        """Return the reward port(s) for this trial."""
        if self._forced_port is not None:
            return [self._forced_port]
        return ["A", "B"]

    def _update_anti_camping(self, poked_port):
        """Update side-bias history; set or clear forced_port."""
        if poked_port is None:
            return

        if self._forced_port is not None:
            if poked_port == self._forced_port:
                # Animal visited the forced side — clear forced mode
                self._forced_port = None
                self._poke_history = []
            # Wrong side poke during forced mode — forced_port stays
            return

        self._poke_history.append(poked_port)
        if len(self._poke_history) > 3:
            self._poke_history.pop(0)

        # 3 in a row on same side → force the other
        if len(self._poke_history) == 3 and len(set(self._poke_history)) == 1:
            camped = self._poke_history[0]
            self._forced_port = "B" if camped == "A" else "A"
            self._poke_history = []
            print(f"[INFO] Anti-camping: forcing port {self._forced_port}")

    # ── Sensor helpers ────────────────────────────────────────────────────────

    def _wait_for_poke(self, port: str, deadline: float = None) -> bool:
        """Block until port triggered+held. Returns True on poke, False on stop/deadline."""
        heartbeat = WaitHeartbeat(f"poke at port {port}")
        while True:
            if not self.running or STOP_EVENT.is_set():
                return False
            if deadline is not None and time.time() >= deadline:
                return False
            state, _ = self.shared.get_port(port)
            if state == "triggered" and sensor_held(self.shared, port):
                return True
            heartbeat.tick()
            time.sleep(0.001)

    def _wait_for_any_poke(self, ports, deadline: float = None):
        """Block until any listed port is triggered+held.
        Returns port name, or None on stop/deadline."""
        heartbeat = WaitHeartbeat(f"poke at port(s) {'/'.join(ports)}")
        while True:
            if not self.running or STOP_EVENT.is_set():
                return None
            if deadline is not None and time.time() >= deadline:
                return None
            for port in ports:
                state, _ = self.shared.get_port(port)
                if state == "triggered" and sensor_held(self.shared, port):
                    return port
            heartbeat.tick()
            time.sleep(0.001)

    def _wait_for_table_contact(self, deadline: float = None):
        """Wait for table sensor trigger; measure hold.
        Returns (seconds_held, contact_start), or (None, None) if stopped or deadline exceeded."""
        heartbeat = WaitHeartbeat("table sensor contact")
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
            heartbeat.tick()
            time.sleep(0.001)
        return None, None

    def _wait_for_ports_cleared(self, ports) -> bool:
        """Block until every listed port reads 'cleared'.

        A port left reading 'triggered' — a dropped clear event, or an animal
        parked in the port — would otherwise hang the trial here silently.
        Returns False on stop.
        """
        heartbeat = WaitHeartbeat(f"port(s) {'/'.join(ports)} to clear")
        while self.running and not STOP_EVENT.is_set():
            if all(self.shared.get_port(p)[0] == "cleared" for p in ports):
                return True
            heartbeat.tick()
            time.sleep(0.005)
        return False

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
