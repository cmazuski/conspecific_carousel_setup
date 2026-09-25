# Phase3.py — Phases 3a and 3b social reward training (rat and mouse)
#
# Phase 3a sequence:
#   port A LED → animal pokes A → door opens → animal holds table sensor
#   >= sensory_minimum s → animal clears table → LED C on → poke C → reward
#
# Phase 3b sequence:
#   identical to 3a, but sensory_minimum increases gradually across trials.
#   Pass a callable as sensory_minimum that accepts the session instance and
#   returns the required hold time for that trial.
#
# Port A: 200 s deadline — door auto-opens if not poked in time (logged).
# Sensory minimum: 200 s timeout from door open — missed trial if not met.
# LED C: turns on once the animal clears the table sensor only.
# Door: closes safely after port C poke (or miss).
#
# Species differences:
#   rat   — reward volume scales incrementally (incremental_reward)
#   mouse — fixed reward volume (deliver_reward)

import random
import threading
import time
import numpy as np
import pandas as pd

from hardware import (
    set_led,
    open_door,
    close_door_safe,
    wait_for_door_state,
    wait_for_table_clear,
    SharedSensorState,
    STOP_EVENT,
    DOOR_OPEN_TIMEOUT,
    DOOR_CLOSE_TIMEOUT,
)
from .base_session import BaseSocialSession

PORT_A_TIMEOUT       = 200   # s — door auto-opens if animal doesn't poke A
TABLE_SENSOR_TIMEOUT = 200   # s — missed trial if sensory minimum not met


class Phase3Session(BaseSocialSession):

    _session_name = "Phase 3 Session"

    def __init__(
        self,
        ser,
        shared: SharedSensorState,
        species: str,
        sensory_minimum,            # float, or callable(session) → float for 3b
        valve_time: float,
        session_duration: float = None,
    ):
        super().__init__(ser, shared, species, valve_time, session_duration)
        self.sensory_minimum = sensory_minimum

        self.results_df = pd.DataFrame(columns=[
            "trial_num",
            "port",
            "trial_start",
            "trial_end",
            "rt",                        # LED C on → port C poke
            "rt_dooropen",               # LED A on → port A poke (or 200 if auto-opened)
            "rt_tablehold",              # door open → sensory minimum met
            "rt_to_first_table",         # door open → first table contact
            "sampling_time",             # last table contact bout that met sensory minimum
            "total_sampling_time",       # sum of all table contact bouts in trial
            "trial_duration",            # LED A on → port C poke
            "sensory_minimum_required",  # value used this trial (useful for 3b logging)
            "iti",
            "reward_triggered",
            "auto_dooropen",
            "reward_count",
            "valve_time",
            "outcome",                   # "rewarded" / "missed"
        ])

    def _run_trial(self):
        rt                  = np.nan
        rt_dooropen         = np.nan
        rt_tablehold        = np.nan
        rt_to_first_table   = np.nan
        sampling_time       = np.nan
        total_sampling_time = 0.0
        trial_start         = np.nan
        trial_end           = np.nan
        trial_duration      = np.nan
        valve_time_used     = np.nan
        rewarded            = False
        auto_dooropen       = False
        outcome             = "missed"

        required_sm = (
            self.sensory_minimum(self)
            if callable(self.sensory_minimum)
            else self.sensory_minimum
        )
        print(f"Sensory minimum this trial: {required_sm:.3f} s")

        # ── 1. Port A LED — wait for poke (200 s auto-open) ──────────────────
        set_led(self.ser, "A", True)
        print("Waiting for port A poke...")
        ledA_onset = time.time()
        deadlineA  = ledA_onset + PORT_A_TIMEOUT

        while self.running and not STOP_EVENT.is_set():
            if self.shared.get_port("A")[0] == "cleared":
                break
            time.sleep(0.005)

        pokedA = self._wait_for_poke("A", deadline=deadlineA)

        if pokedA:
            rt_dooropen = time.time() - ledA_onset
            print(f"Port A poked (rt_dooropen={rt_dooropen:.3f} s)")
        else:
            rt_dooropen   = PORT_A_TIMEOUT
            auto_dooropen = True
            print(f"Port A timeout ({PORT_A_TIMEOUT} s) — door auto-opens")

        set_led(self.ser, "A", False)

        # ── 2. Open door — async, wait for fully open ─────────────────────────
        threading.Thread(target=open_door, args=(self.ser,), daemon=True).start()
        door_opened = wait_for_door_state(self.shared, target_state="door opened",
                                          timeout=DOOR_OPEN_TIMEOUT, device=self.ser)
        door_open_time = time.time()
        if door_opened:
            print("Door opened")
        elif self.running and not STOP_EVENT.is_set():
            print("[ERROR] Door never confirmed open — scoring this trial as door_failed")

        # ── 3. Wait for sensory minimum (200 s timeout) ───────────────────────
        print(f"Waiting for sensory minimum ({required_sm:.3f} s)...")
        sm_met             = False
        first_contact_time = None

        deadline = door_open_time + TABLE_SENSOR_TIMEOUT
        while door_opened and self.running and not STOP_EVENT.is_set():
            if time.time() >= deadline:
                print(f"Sensory minimum not met within {TABLE_SENSOR_TIMEOUT} s → missed trial")
                break

            s_time, contact_start = self._wait_for_table_contact(deadline=deadline)
            if s_time is None:
                break

            if first_contact_time is None:
                first_contact_time = contact_start
            total_sampling_time += s_time

            if s_time >= required_sm:
                rt_tablehold  = time.time() - door_open_time
                sampling_time = s_time
                print(f"Sensory minimum met: {s_time:.3f} s")
                sm_met = True
                break

            print(f"Sensory minimum too short ({s_time:.3f} s), retrying...")

        rt_to_first_table = (first_contact_time - door_open_time
                             if first_contact_time is not None else np.nan)

        if not sm_met:
            iti = random.uniform(self.ITI_MIN, self.ITI_MAX)
            threading.Thread(
                target=close_door_safe, args=(self.ser, self.shared), daemon=True
            ).start()
            wait_for_door_state(self.shared, "door closed",
                                timeout=DOOR_CLOSE_TIMEOUT, device=self.ser)
            self._log(trial_start, trial_end, rt, rt_dooropen, rt_tablehold,
                      rt_to_first_table, sampling_time, total_sampling_time,
                      np.nan, required_sm, iti, False, auto_dooropen,
                      "missed" if door_opened else "door_failed", np.nan)
            self._run_iti(iti)
            print("Trial complete (missed)" if door_opened
                  else "Trial complete (door failed)")
            return

        # ── 4. Wait for animal to clear table sensor ──────────────────────────
        print("Waiting for animal to clear table sensor...")
        wait_for_table_clear(self.shared)

        # ── 5. LED C on ───────────────────────────────────────────────────────
        set_led(self.ser, self.port, True)
        print("LED C on — waiting for port C poke")

        while self.running and not STOP_EVENT.is_set():
            if self.shared.get_port(self.port)[0] == "cleared":
                break
            time.sleep(0.005)

        trial_start = time.time()

        # ── 6. Wait for poke at port C (no deadline) ──────────────────────────
        poked     = self._wait_for_poke(self.port)
        trial_end = time.time()
        set_led(self.ser, self.port, False)

        if poked:
            rt             = trial_end - trial_start
            rewarded       = True
            outcome        = "rewarded"
            trial_duration = trial_end - ledA_onset
            valve_time_used = self._deliver_reward()
            self.reward_count += 1
            print(f"Reward delivered "
                  f"(reward #{self.reward_count}, valve={valve_time_used:.3f} s)")

        iti = random.uniform(self.ITI_MIN, self.ITI_MAX)

        # ── 7. Close door; wait for fully closed before ITI ───────────────────
        threading.Thread(
            target=close_door_safe, args=(self.ser, self.shared), daemon=True
        ).start()
        wait_for_door_state(self.shared, "door closed",
                            timeout=DOOR_CLOSE_TIMEOUT, device=self.ser)
        print("Door closed, ready for next trial")

        self._log(trial_start, trial_end, rt, rt_dooropen, rt_tablehold,
                  rt_to_first_table, sampling_time, total_sampling_time,
                  trial_duration, required_sm, iti, rewarded, auto_dooropen,
                  outcome, valve_time_used)
        self._run_iti(iti)
        print("Trial complete")

    def _log(self, trial_start, trial_end, rt, rt_dooropen, rt_tablehold,
             rt_to_first_table, sampling_time, total_sampling_time,
             trial_duration, required_sm, iti, reward_triggered, auto_dooropen,
             outcome, valve_time_used):
        row = {
            "trial_num":               self.trial_counter,
            "port":                    self.port,
            "trial_start":             trial_start,
            "trial_end":               trial_end,
            "rt":                      rt,
            "rt_dooropen":             rt_dooropen,
            "rt_tablehold":            rt_tablehold,
            "rt_to_first_table":       rt_to_first_table,
            "sampling_time":           sampling_time,
            "total_sampling_time":     total_sampling_time,
            "trial_duration":          trial_duration,
            "sensory_minimum_required": required_sm,
            "iti":                     iti,
            "reward_triggered":        reward_triggered,
            "auto_dooropen":           auto_dooropen,
            "reward_count":            self.reward_count,
            "valve_time":              valve_time_used,
            "outcome":                 outcome,
        }
        with self._df_lock:
            self.results_df.loc[len(self.results_df)] = row
        print(self.results_df.iloc[-1].to_dict())
