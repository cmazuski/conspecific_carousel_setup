# Phase1.py — autoshaping phase for rat and mouse
#
# Port C LED lights → animal pokes → reward delivered
#
# Species differences:
#   rat   — reward volume increases incrementally (incremental_reward)
#   mouse — fixed reward volume (deliver_reward)

import random
import time
import numpy as np
import pandas as pd

from hardware import set_led, SharedSensorState, STOP_EVENT
from .base_session import BaseSocialSession


class Phase1Session(BaseSocialSession):

    _session_name = "Phase 1 Session"

    def __init__(
        self,
        ser,
        shared: SharedSensorState,
        species: str,
        valve_time: float,
        session_duration: float = None,
    ):
        super().__init__(ser, shared, species, valve_time, session_duration)

        self.results_df = pd.DataFrame(columns=[
            "trial_num", "port", "trial_start", "trial_end",
            "rt", "iti", "reward_triggered", "reward_count", "valve_time",
        ])

    def _run_trial(self):
        trial_start     = time.time()
        trial_end       = np.nan
        rt              = np.nan
        valve_time_used = np.nan

        set_led(self.ser, self.port, True)

        # Require port to be cleared before accepting a new poke
        while self.running and not STOP_EVENT.is_set():
            if self.shared.get_port(self.port)[0] == "cleared":
                break
            time.sleep(0.005)

        poked = self._wait_for_poke(self.port)
        trial_end = time.time()

        if poked:
            rt = trial_end - trial_start
            valve_time_used = self._deliver_reward()
            self.reward_count += 1
            print(f"Reward delivered (reward #{self.reward_count}, valve={valve_time_used:.3f}s)")

        set_led(self.ser, self.port, False)

        iti = random.uniform(self.ITI_MIN, self.ITI_MAX)
        row = {
            "trial_num":        self.trial_counter,
            "port":             self.port,
            "trial_start":      trial_start,
            "trial_end":        trial_end,
            "rt":               rt,
            "iti":              iti,
            "reward_triggered": poked,
            "reward_count":     self.reward_count,
            "valve_time":       valve_time_used,
        }
        with self._df_lock:
            self.results_df.loc[len(self.results_df)] = row

        self._run_iti(iti)
