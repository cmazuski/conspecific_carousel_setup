# learning.py — Social Choice: port C autoshaping (rat and mouse)
#
# Port C LED lights up. Animal pokes → reward.
# No anti-camping (single port).
#
# Species differences:
#   rat   — incremental_reward
#   mouse — fixed deliver_reward

import random
import time
import numpy as np
import pandas as pd

from hardware import set_led, STOP_EVENT
from .base_session import BaseSCSession


class LearningSession(BaseSCSession):

    _session_name = "Social Choice Learning"

    def __init__(
        self,
        ser,
        shared,
        species: str,
        valve_time: float,
        iti_min: float = 5.0,
        iti_max: float = 10.0,
        session_duration: float = None,
    ):
        super().__init__(ser, shared, species, valve_time, session_duration)
        self.ITI_MIN = iti_min
        self.ITI_MAX = iti_max

        self.results_df = pd.DataFrame(columns=[
            "trial_num",
            "reward_triggered",
            "trial_start",
            "trial_end",
            "rt",
            "iti",
            "reward_count",
            "valve_time",
        ])

    def _run_trial(self):
        rt              = np.nan
        valve_time_used = np.nan
        rewarded        = False

        # Wait for port C to clear before lighting
        while self.running and not STOP_EVENT.is_set():
            if self.shared.get_port("C")[0] == "cleared":
                break
            time.sleep(0.005)

        trial_start = time.time()
        set_led(self.ser, "C", True)
        print("Port C LED on")

        poked = self._wait_for_poke("C")
        trial_end = time.time()
        set_led(self.ser, "C", False)

        if not poked:
            return

        rt = trial_end - trial_start
        valve_time_used = self._deliver_reward("C")
        rewarded = True
        self.reward_count += 1
        print(f"Reward at port C (#{self.reward_count}, valve={valve_time_used:.3f} s)")

        iti = random.uniform(self.ITI_MIN, self.ITI_MAX)
        row = {
            "trial_num":        self.trial_counter,
            "reward_triggered": rewarded,
            "trial_start":      trial_start,
            "trial_end":        trial_end,
            "rt":               rt,
            "iti":              iti,
            "reward_count":     self.reward_count,
            "valve_time":       valve_time_used,
        }
        with self._df_lock:
            self.results_df.loc[len(self.results_df)] = row
        print(self.results_df.iloc[-1].to_dict())
        self._run_iti(iti)
