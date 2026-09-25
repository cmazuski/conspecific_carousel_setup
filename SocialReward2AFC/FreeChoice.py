# FreeChoice.py — 2AFC Free Choice phase (rat and mouse)
#
# Fully free-choice: stimulus presented, both port A and B LEDs on after sampling.
# Animal chooses either port; only the correct one delivers reward.
#
# Outcomes: hit (correct port) / miss (deadline) / error (wrong port)
# Balanced block of angle_a / angle_b (default block_size=10).

import random
import numpy as np
import pandas as pd

from hardware import SharedSensorState
from .task_base import TaskBase2AFC


class FreeChoiceSession(TaskBase2AFC):

    _session_name = "2AFC Free Choice"

    def __init__(
        self,
        ser,
        shared: SharedSensorState,
        species: str,
        valve_time: float,
        sensory_minimum: float,
        decision_window: float,
        angle_a: int = 90,
        angle_b: int = 270,
        block_size: int = 10,
        session_duration: float = None,
    ):
        super().__init__(
            ser, shared, species, valve_time,
            sensory_minimum, decision_window,
            angle_a, angle_b, block_size, session_duration,
        )

        self.results_df = pd.DataFrame(columns=[
            "trial_num",
            "trial_type",
            "presentation_angle",
            "correct_port",
            "poked_port",
            "outcome",
            "rt",
            "rt_dooropen",
            "rt_tablehold",
            "rt_to_first_table",
            "sampling_time",
            "total_sampling_time",
            "start_angle",
            "turn_direction",
            "reward_triggered",
            "reward_count",
            "valve_time",
            "iti",
            "trial_start",
            "trial_end",
        ])

    def _run_trial(self):
        data = self._run_task_trial(trial_type="free")
        if not data:
            return

        iti = random.uniform(self.ITI_MIN, self.ITI_MAX)
        row = {
            "trial_num":          self.trial_counter,
            "trial_type":         "free",
            "presentation_angle": data["presentation_angle"],
            "correct_port":       data["correct_port"],
            "poked_port":         data["poked_port"],
            "outcome":            data["outcome"],
            "rt":                 data["rt"],
            "rt_dooropen":        data["rt_dooropen"],
            "rt_tablehold":        data["rt_tablehold"],
            "rt_to_first_table":   data["rt_to_first_table"],
            "sampling_time":       data["sampling_time"],
            "total_sampling_time": data["total_sampling_time"],
            "start_angle":         data["start_angle"],
            "turn_direction":     data["turn_direction"],
            "reward_triggered":   data["reward_triggered"],
            "reward_count":       self.reward_count,
            "valve_time":         data["valve_time"],
            "iti":                iti,
            "trial_start":        data["trial_start"],
            "trial_end":          data["trial_end"],
        }
        with self._df_lock:
            self.results_df.loc[len(self.results_df)] = row
        print(self.results_df.iloc[-1].to_dict())
        self._run_iti(iti)
        print("Trial complete")
