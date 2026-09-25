# one_choice.py — Social Choice: port A → port C → reward
#
# Trial sequence:
#   1. Port A LED on → poke A (no deadline)
#   2. Port A LED off → port C LED on
#   3. Poke C within decision_window → hit (reward)
#      Deadline expires              → miss
#   4. ITI

import random
import time
import numpy as np
import pandas as pd

from hardware import set_led, STOP_EVENT
from .base_session import BaseSCSession


class OneChoiceSession(BaseSCSession):

    _session_name = "Social Choice One-Choice"

    def __init__(
        self,
        ser,
        shared,
        species: str,
        valve_time: float,
        decision_window: float,
        iti_min: float = 5.0,
        iti_max: float = 10.0,
        session_duration: float = None,
    ):
        super().__init__(ser, shared, species, valve_time, session_duration)
        self.decision_window = decision_window
        self.ITI_MIN = iti_min
        self.ITI_MAX = iti_max

        self.results_df = pd.DataFrame(columns=[
            "trial_num",
            "outcome",
            "rt_a",
            "rt_c",
            "reward_triggered",
            "trial_start",
            "trial_end",
            "iti",
            "reward_count",
            "valve_time",
        ])

    def _run_trial(self):
        rt_a            = np.nan
        rt_c            = np.nan
        valve_time_used = np.nan
        rewarded        = False
        outcome         = "miss"

        # Wait for port A to clear before lighting
        while self.running and not STOP_EVENT.is_set():
            if self.shared.get_port("A")[0] == "cleared":
                break
            time.sleep(0.005)

        # Step 1: Port A LED on → wait for poke (no deadline)
        trial_start = time.time()
        set_led(self.ser, "A", True)
        print("Port A LED on — waiting for poke")

        pokedA = self._wait_for_poke("A")
        t_a_poked = time.time()
        set_led(self.ser, "A", False)

        if not pokedA:
            return  # session stopped

        rt_a = t_a_poked - trial_start
        print(f"Port A poked (rt_a={rt_a:.3f} s)")

        # Step 2: Port C LED on → decision window
        while self.running and not STOP_EVENT.is_set():
            if self.shared.get_port("C")[0] == "cleared":
                break
            time.sleep(0.005)

        set_led(self.ser, "C", True)
        c_onset    = time.time()
        deadline_c = c_onset + self.decision_window
        print(f"Port C LED on (window={self.decision_window:.1f} s)")

        pokedC = self._wait_for_poke("C", deadline=deadline_c)
        trial_end = time.time()
        set_led(self.ser, "C", False)

        if not self.running and not pokedC:
            return  # session stopped mid-trial

        if pokedC:
            rt_c            = trial_end - c_onset
            outcome         = "hit"
            valve_time_used = self._deliver_reward("C")
            rewarded        = True
            self.reward_count += 1
            print(f"Hit! Reward at C (#{self.reward_count}, valve={valve_time_used:.3f} s)")
        else:
            outcome = "miss"
            print("Miss: port C window expired")

        iti = random.uniform(self.ITI_MIN, self.ITI_MAX)
        row = {
            "trial_num":        self.trial_counter,
            "outcome":          outcome,
            "rt_a":             rt_a,
            "rt_c":             rt_c,
            "reward_triggered": rewarded,
            "trial_start":      trial_start,
            "trial_end":        trial_end,
            "iti":              iti,
            "reward_count":     self.reward_count,
            "valve_time":       valve_time_used,
        }
        with self._df_lock:
            self.results_df.loc[len(self.results_df)] = row
        print(self.results_df.iloc[-1].to_dict())
        self._run_iti(iti)
