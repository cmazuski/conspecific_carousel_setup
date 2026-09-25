# MixedChoice.py — 2AFC Mixed / Probe phase (rat and mouse)
#
# Adaptive mixture of forced (one correct LED) and free (both LEDs) trials.
# Block size = 20 trials. Forced ratio starts at 0.75 and advances when the
# animal exceeds 75 % correct on the FREE trials within a block:
#
#   0.75 → 0.50 → 0.25 → 0.00  (0.00 = all free; session can continue)
#
# Within each block:
#   - Stimulus angles balanced across all 20 trials (10 of each).
#   - Forced/free assignment randomised subject to the ratio constraint.
#
# Outcomes: hit / miss / error  (same as Forced/Free phases)
# Recorded: trial_type (forced/free), forced_ratio (ratio active that block),
#           block_num, free_hit_rate_prev_block (performance that triggered advance)

import random
import numpy as np
import pandas as pd

from hardware import SharedSensorState, STOP_EVENT
from .task_base import TaskBase2AFC

BLOCK_SIZE      = 20     # trials per block (ratio arithmetic assumes this)
ADVANCE_RATIOS  = [0.75, 0.50, 0.25, 0.00]
ADVANCE_CRIT    = 0.75   # free-trial hit rate threshold to advance ratio
MIN_FREE_TRIALS = 3      # minimum free trials in a block before evaluating


class MixedChoiceSession(TaskBase2AFC):

    _session_name = "2AFC Mixed Choice"

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
        start_forced_ratio: float = 0.75,
        session_duration: float = None,
    ):
        # block_size fixed at BLOCK_SIZE for mixed (needed for ratio arithmetic)
        super().__init__(
            ser, shared, species, valve_time,
            sensory_minimum, decision_window,
            angle_a, angle_b, block_size=BLOCK_SIZE,
            session_duration=session_duration,
        )
        self.forced_ratio = start_forced_ratio

        self._block_num       = 0
        self._block_queue     = []   # list of trial_type strings for current block
        self._block_results   = []   # outcome dicts for current block

        self.results_df = pd.DataFrame(columns=[
            "trial_num",
            "block_num",
            "trial_type",
            "forced_ratio",
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
            "free_hit_rate_prev_block",
        ])

        self._free_hit_rate_prev = np.nan

    # ── Block management ──────────────────────────────────────────────────────

    def _refill_block(self):
        """Build a balanced block of 20 (angle, trial_type) pairs and shuffle."""
        n = self.block_size
        n_forced = round(n * self.forced_ratio)
        n_free   = n - n_forced

        # Balance stimulus angles within each trial type
        def _angle_list(count):
            half = count // 2
            lst  = [self.angle_a] * half + [self.angle_b] * (count - half)
            random.shuffle(lst)
            return lst

        forced_angles = _angle_list(n_forced)
        free_angles   = _angle_list(n_free)

        pairs = (
            [("forced", a) for a in forced_angles] +
            [("free",   a) for a in free_angles]
        )
        random.shuffle(pairs)

        # Restore parent's _position_block from the angle component
        self._position_block = [a for _, a in pairs]
        self._block_queue    = [t for t, _ in pairs]

        self._block_num   += 1
        self._block_results = []
        print(f"[INFO] Block {self._block_num}: "
              f"forced_ratio={self.forced_ratio:.2f} "
              f"({n_forced} forced / {n_free} free)")

    def _evaluate_block(self):
        """Check free-trial performance; advance ratio if criterion met.

        'door_failed' trials are excluded from both numerator and denominator:
        the animal never got to choose, so counting them would penalise it for
        a hardware fault.
        """
        free_outcomes = [
            r["outcome"] for r in self._block_results
            if r["trial_type"] == "free" and r["outcome"] != "door_failed"
        ]
        if len(free_outcomes) < MIN_FREE_TRIALS:
            return

        hit_rate = free_outcomes.count("hit") / len(free_outcomes)
        self._free_hit_rate_prev = hit_rate
        print(f"[INFO] Block {self._block_num} free-trial hit rate: {hit_rate:.2%}")

        if hit_rate >= ADVANCE_CRIT:
            try:
                idx = ADVANCE_RATIOS.index(self.forced_ratio)
                if idx < len(ADVANCE_RATIOS) - 1:
                    self.forced_ratio = ADVANCE_RATIOS[idx + 1]
                    print(f"[INFO] Criterion met — advancing forced_ratio "
                          f"to {self.forced_ratio:.2f}")
            except ValueError:
                pass

    # ── Trial ─────────────────────────────────────────────────────────────────

    def _run_trial(self):
        # New block?
        if not self._block_queue:
            if self._block_results:
                self._evaluate_block()
            self._refill_block()

        trial_type = self._block_queue.pop(0)
        data = self._run_task_trial(trial_type=trial_type)
        if not data:
            return

        self._block_results.append({
            "trial_type": trial_type,
            "outcome":    data["outcome"],
        })

        iti = random.uniform(self.ITI_MIN, self.ITI_MAX)
        row = {
            "trial_num":               self.trial_counter,
            "block_num":               self._block_num,
            "trial_type":              trial_type,
            "forced_ratio":            self.forced_ratio,
            "presentation_angle":      data["presentation_angle"],
            "correct_port":            data["correct_port"],
            "poked_port":              data["poked_port"],
            "outcome":                 data["outcome"],
            "rt":                      data["rt"],
            "rt_dooropen":             data["rt_dooropen"],
            "rt_tablehold":            data["rt_tablehold"],
            "rt_to_first_table":       data["rt_to_first_table"],
            "sampling_time":           data["sampling_time"],
            "total_sampling_time":     data["total_sampling_time"],
            "start_angle":             data["start_angle"],
            "turn_direction":          data["turn_direction"],
            "reward_triggered":        data["reward_triggered"],
            "reward_count":            self.reward_count,
            "valve_time":              data["valve_time"],
            "iti":                     iti,
            "trial_start":             data["trial_start"],
            "trial_end":               data["trial_end"],
            "free_hit_rate_prev_block":self._free_hit_rate_prev,
        }
        with self._df_lock:
            self.results_df.loc[len(self.results_df)] = row
        print(self.results_df.iloc[-1].to_dict())
        self._run_iti(iti)
        print("Trial complete")
