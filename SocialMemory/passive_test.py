# passive_test.py — Passive Test: pseudorandom presentation of 4 boxes (rat and mouse)
#
# Session sequence:
#   A fixed number of presentations per box (0–3) is pseudorandomly ordered so
#   that no two consecutive presentations use the same box
#   (e.g. 1, 3, 1, 4 is acceptable; 1, 1, 3, 4 is not).
#   All presentations share one duration and are separated by a CC-filled ITI
#   drawn from one shared [iti_min, iti_max] range.
#
# Each presentation (same sequence as SocialMemoryTaskSession, see base_session):
#   1. Turntable rotates to the box's angle (box index × 90°)
#   2. Door opens
#   3. Sampling timer starts when table beam first broken
#   4. After presentation_duration s: turntable rotates 45° CCW (removes stimulus)
#   5. Door closes safely (pauses if table or door proximity sensors are active)
#   6. Wait for door fully closed and table motor stopped
#   7. Turntable returns to home (0°) for the ITI
#
# Between presentations (CC ITI):
#   Classical conditioning runs for a duration drawn from [iti_min, iti_max].
#
# Two DataFrames are recorded:
#   presentations_df — one row per stimulus presentation (includes box + label)
#   conditioning_df  — one row per CC trial across all ITIs

import heapq
import random

import pandas as pd

from hardware import SharedSensorState, STOP_EVENT
from .base_session import BaseSMSession

N_BOXES = 4


def generate_box_sequence(counts: dict) -> list:
    """Pseudorandomly order presentations so no two consecutive entries use the
    same box. `counts` maps box index -> number of presentations.

    Uses a randomized greedy (always place a box with the most remaining
    presentations that isn't equal to the previous one, breaking ties
    randomly) — the standard approach for the "rearrange so no two adjacent
    are equal" problem. Raises ValueError if no valid ordering exists (i.e.
    one box's count exceeds ceil(total / 2))."""
    total = sum(counts.values())
    if total == 0:
        return []

    max_count = max(counts.values())
    if max_count > (total + 1) // 2:
        raise ValueError(
            "Cannot arrange presentations without consecutive repeats: "
            f"one box has {max_count} of {total} total presentations "
            f"(max allowed is {(total + 1) // 2})."
        )

    heap = [(-n, random.random(), box) for box, n in counts.items() if n > 0]
    heapq.heapify(heap)

    result = []
    prev_box = None
    while heap:
        negcount, _, box = heapq.heappop(heap)
        if box == prev_box:
            # Most-remaining box repeats the previous one — place the next
            # most-remaining box instead, and push this one back for later.
            negcount2, _, box2 = heapq.heappop(heap)
            result.append(box2)
            prev_box = box2
            negcount2 += 1
            if negcount2 < 0:
                heapq.heappush(heap, (negcount2, random.random(), box2))
            heapq.heappush(heap, (negcount, random.random(), box))
        else:
            result.append(box)
            prev_box = box
            negcount += 1
            if negcount < 0:
                heapq.heappush(heap, (negcount, random.random(), box))

    return result


def label_sequence(sequence: list) -> list:
    """Convert a box-index sequence into period labels (Box0_1, Box2_1, Box0_2, ...),
    matching the labels SocialMemoryTaskSession uses (S1_1, S2_1, ...)."""
    occurrence = {i: 0 for i in range(N_BOXES)}
    labels = []
    for box in sequence:
        occurrence[box] += 1
        labels.append(f"Box{box}_{occurrence[box]}")
    return labels


class PassiveTestSession(BaseSMSession):

    _session_name = "Passive Test"

    def __init__(
        self,
        ser,
        shared: SharedSensorState,
        species: str,
        valve_times: dict,
        box_ids,          # 4 labels, e.g. ["CagemateA", "Novel1", "CagemateB", "Novel2"]
        box_n,            # 4 presentation counts, indexed the same way as box_ids
        presentation_duration: float,
        iti_min: float,
        iti_max: float,
        # Classical conditioning parameters (applied during all ITIs)
        cc_ports,
        cc_led_on_time: float,
        cc_iti_min: float,
        cc_iti_max: float,
        cc_reward_prob: float = 1.0,
        cc_delay: float = 0.0,
        sequence: list = None,
        session_start: float = None,
    ):
        super().__init__(ser, shared, species, valve_times, session_start=session_start)
        if len(box_ids) != N_BOXES or len(box_n) != N_BOXES:
            raise ValueError(f"box_ids and box_n must each have {N_BOXES} entries")

        self.box_ids = list(box_ids)
        self.box_n = [int(n) for n in box_n]
        self.presentation_duration = presentation_duration
        self.iti_min = iti_min
        self.iti_max = iti_max

        # Generated up front (rather than at session start) so callers — e.g. the
        # GUI, to preload the expected sequence — can read it before .start().
        if sequence is not None:
            self.sequence = list(sequence)
        else:
            self.sequence = generate_box_sequence({i: self.box_n[i] for i in range(N_BOXES)})

        self.cc_ports = list(cc_ports)
        self.cc_led_on_time = cc_led_on_time
        self.cc_iti_min = cc_iti_min
        self.cc_iti_max = cc_iti_max
        self.cc_reward_prob = cc_reward_prob
        self.cc_delay = cc_delay

        self.presentations_df = pd.DataFrame(columns=[
            "presentation_num",
            "period",            # Box0_1, Box2_1, Box0_2, ...
            "box",
            "label",
            "angle",
            "presentation_duration",   # configured duration (s)
            "door_open_time",          # time.time() when door reached "door opened"
            "time_to_engage",          # presentation_start - door_open_time (s)
            "sampling_time",           # actual time beam was triggered (s)
            "bout_count",              # number of non-triggered → triggered transitions
            "presentation_start",      # time.time() when timer started (beam first broken)
            "presentation_end",        # time.time() when presentation duration elapsed
        ])

        self.conditioning_df = pd.DataFrame(columns=[
            "trial_num",
            "iti_period",        # CC_pre2, CC_pre3, ...
            "port",
            "forced",
            "reward_triggered",
            "reward_prob_applied",
            "trial_start",
            "trial_end",
            "rt",
            "iti",
            "valve_time",
        ])

    # ── Session loop (override — fixed pseudorandom sequence) ─────────────────

    def _run_session(self):
        periods = label_sequence(self.sequence)

        print(f"[INFO] {self._session_name} started")
        print("[INFO] Boxes: " + ", ".join(
            f"{i}={self.box_ids[i] or '(unlabeled)'}×{self.box_n[i]}"
            for i in range(N_BOXES)
        ))
        print(f"[INFO] Sequence: {periods}")
        print(f"[INFO] Duration {self.presentation_duration} s, "
              f"ITI {self.iti_min}–{self.iti_max} s")

        try:
            for i, (box, period) in enumerate(zip(self.sequence, periods)):
                if not self.running or STOP_EVENT.is_set():
                    break
                if i > 0:
                    self._run_cc_iti(self.iti_min, self.iti_max, f"CC_pre{i + 1}")
                if not self.running or STOP_EVENT.is_set():
                    break
                self._run_presentation(
                    box * 90, self.presentation_duration, period,
                    extra_fields={"box": box, "label": self.box_ids[box]},
                )
        finally:
            self.running = False
            print(f"[INFO] {self._session_name} ended")

    # Required by base but not used (sequence is managed above)
    def _run_trial(self):
        raise NotImplementedError
