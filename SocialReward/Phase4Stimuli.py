# Phase4Stimuli.py — 4-box weighted-presentation variant of the Social Reward task
#
# Like the Task, but with four independently weighted and rewarded boxes.
#
# Box → angle: box N = N × 90°  (box0=0°, box1=90°, box2=180°, box3=270°)
#
# Configuration:
#   box_config = {
#       0: {'freq': 40, 'rewarded': True},
#       1: {'freq':  5, 'rewarded': False},
#       2: {'freq':  5, 'rewarded': False},
#       3: {'freq': 40, 'rewarded': True},
#   }
#   Frequencies are relative weights (need not sum to 100).
#
# Trial sequence:
#   1. Turntable → presentation box (weighted random from planned_sequence)
#   2. LED A on → poke A (no deadline)
#   3. Door opens → sensory minimum (indefinite, retry loop)
#   4. Table sensor cleared
#   5. LED C on + 45° CCW (async)
#   6. Poke C within decision_window → outcome + reward if box is rewarded
#   7. Door closes; turntable makes a random 45–180° turn (multiple of 45°)
#   8. Log → ITI

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
    wait_for_table_stopped,
    turn_table_degrees,
    SharedSensorState,
    STOP_EVENT,
    DOOR_OPEN_TIMEOUT,
    DOOR_CLOSE_TIMEOUT,
)
from .base_session import BaseSocialSession


class Phase4StimuliSession(BaseSocialSession):

    _session_name = "4-Stimuli Session"
    ITI_MIN = 1.0
    ITI_MAX = 3.0

    def __init__(
        self,
        ser,
        shared: SharedSensorState,
        species: str,
        valve_time: float,
        box_config: dict,        # {box_num: {'freq': float, 'rewarded': bool}}
        sensory_minimum: float,
        decision_window: float,
        session_duration: float = None,
    ):
        super().__init__(ser, shared, species, valve_time, session_duration)
        self.box_config      = box_config
        self.sensory_minimum = sensory_minimum
        self.decision_window = decision_window

        self._current_angle   = 0
        self.planned_sequence: list = []   # list of presentation angles

        self.results_df = pd.DataFrame(columns=[
            "trial_num",
            "presentation_box",
            "presentation_angle",
            "reward_available",
            "trial_start",
            "trial_end",
            "trial_duration",       # LED A on → port C poke / decision window end
            "rt",                   # LED C on → port C poke
            "rt_dooropen",          # LED A on → port A poke
            "rt_tablehold",         # door open → sensory min met
            "rt_to_first_table",    # door open → first table contact
            "sampling_time",        # last bout that met sensory minimum
            "total_sampling_time",  # sum of all table contact bouts
            "return_angle",         # angle table returned to after trial
            "start_angle",
            "turn_direction",
            "reward_triggered",
            "outcome",              # hit / miss / false_alarm / correct_rejection
            "reward_count",
            "valve_time",
            "iti",
        ])

    # ── Session start: pre-generate weighted sequence ─────────────────────────

    def _run_session(self):
        n       = self.max_trials if self.max_trials is not None else 100
        boxes   = sorted(self.box_config.keys())
        weights = [self.box_config[b]["freq"] for b in boxes]
        angles  = [b * 90 for b in boxes]

        self.planned_sequence = random.choices(angles, weights=weights, k=n)
        freq_str = ", ".join(
            f"box{b}={self.box_config[b]['freq']}% "
            f"({'rewarded' if self.box_config[b]['rewarded'] else 'unrewarded'})"
            for b in boxes
        )
        print(f"[INFO] Pre-assigned {n}-trial sequence — {freq_str}")
        super()._run_session()

    # ── Trial logic ───────────────────────────────────────────────────────────

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

        # ── 1. Read presentation angle from pre-generated sequence ────────────
        idx = self.trial_counter - 1
        if idx >= len(self.planned_sequence):
            boxes   = sorted(self.box_config.keys())
            weights = [self.box_config[b]["freq"] for b in boxes]
            angles  = [b * 90 for b in boxes]
            self.planned_sequence.extend(random.choices(angles, weights=weights, k=10))

        presentation_angle = self.planned_sequence[idx]
        presentation_box   = presentation_angle // 90
        reward_available   = self.box_config[presentation_box]["rewarded"]
        print(f"Presentation: box {presentation_box} ({presentation_angle}°) "
              f"| reward_available={reward_available}")

        # ── 2. Move turntable to presentation position ────────────────────────
        start_angle    = self._current_angle
        turn_direction = self._turn_to(presentation_angle)
        print(f"Table: {turn_direction} from {start_angle}° → {presentation_angle}°")
        wait_for_table_stopped(self.shared, device=self.ser)

        # ── 3. LED A on → wait for port A poke (no deadline) ─────────────────
        set_led(self.ser, "A", True)
        print("Waiting for port A poke...")
        ledA_onset = time.time()

        while self.running and not STOP_EVENT.is_set():
            if self.shared.get_port("A")[0] == "cleared":
                break
            time.sleep(0.005)

        poked_a = self._wait_for_poke("A")
        if not poked_a:
            set_led(self.ser, "A", False)
            return  # session stopped

        rt_dooropen = time.time() - ledA_onset
        set_led(self.ser, "A", False)
        print(f"Port A poked (rt_dooropen={rt_dooropen:.3f} s)")

        # ── 4. Open door — wait fully open ────────────────────────────────────
        threading.Thread(target=open_door, args=(self.ser,), daemon=True).start()
        if not wait_for_door_state(self.shared, target_state="door opened",
                                   timeout=DOOR_OPEN_TIMEOUT, device=self.ser):
            if self.running and not STOP_EVENT.is_set():
                print("[ERROR] Door never confirmed open — abandoning this trial")
                # The door may well be physically open; close it before the
                # next trial moves the turntable.
                close_door_safe(self.ser, self.shared, timeout=DOOR_CLOSE_TIMEOUT)
            return
        door_open_time = time.time()
        print("Door opened — sensory timer started")

        # ── 5. Sensory minimum (indefinite — loops until met) ─────────────────
        print(f"Waiting for sensory minimum ({self.sensory_minimum:.3f} s)...")
        first_contact_time = None
        while self.running and not STOP_EVENT.is_set():
            s_time, contact_start = self._wait_for_table_contact()
            if s_time is None:
                return  # session stopped

            if first_contact_time is None:
                first_contact_time = contact_start
            total_sampling_time += s_time

            if s_time >= self.sensory_minimum:
                rt_tablehold      = time.time() - door_open_time
                rt_to_first_table = first_contact_time - door_open_time
                sampling_time     = s_time
                print(f"Sensory minimum met: {s_time:.3f} s")
                break

            print(f"Sensory minimum too short ({s_time:.3f} s), retrying...")

        if not self.running or STOP_EVENT.is_set():
            return

        # ── 6. Wait for table sensor clear ────────────────────────────────────
        print("Waiting for animal to clear table sensor...")
        wait_for_table_clear(self.shared)

        # ── 7. LED C on + 45° CCW turn ────────────────────────────────────────
        set_led(self.ser, self.port, True)
        print("LED C on — 45° CCW turn to remove stimulus (async)")
        threading.Thread(
            target=self._turn_ccw_partial, args=(45,), daemon=True
        ).start()

        while self.running and not STOP_EVENT.is_set():
            if self.shared.get_port(self.port)[0] == "cleared":
                break
            time.sleep(0.005)

        trial_start = time.time()
        deadline_c  = trial_start + self.decision_window

        # ── 8. Wait for poke C within decision window ─────────────────────────
        poked     = self._wait_for_poke(self.port, deadline=deadline_c)
        trial_end = time.time()
        set_led(self.ser, self.port, False)

        trial_duration = trial_end - ledA_onset

        if poked:
            rt = trial_end - trial_start
            if reward_available:
                rewarded        = True
                valve_time_used = self._deliver_reward()
                self.reward_count += 1
                print(f"Reward delivered "
                      f"(reward #{self.reward_count}, valve={valve_time_used:.3f} s)")

        if reward_available and poked:
            outcome = "hit"
        elif reward_available and not poked:
            outcome = "miss"
        elif not reward_available and poked:
            outcome = "false_alarm"
        else:
            outcome = "correct_rejection"
        print(f"Outcome: {outcome}")

        # ── 9. Close door ─────────────────────────────────────────────────────
        threading.Thread(
            target=close_door_safe, args=(self.ser, self.shared), daemon=True
        ).start()
        wait_for_door_state(self.shared, "door closed",
                            timeout=DOOR_CLOSE_TIMEOUT, device=self.ser)

        # ── 10. Return to random position (45–180° from current, mult. of 45°) ─
        wait_for_table_stopped(self.shared, device=self.ser)
        magnitude    = random.choice([45, 90, 135, 180])
        direction    = random.choice([1, -1])
        return_angle = (self._current_angle + direction * magnitude) % 360
        self._turn_to(return_angle)
        print(f"Table → {return_angle}° "
              f"(random {magnitude}° {'CW' if direction > 0 else 'CCW'})")
        wait_for_table_stopped(self.shared, device=self.ser)

        iti = random.uniform(self.ITI_MIN, self.ITI_MAX)
        self._log(
            trial_start, trial_end, trial_duration, rt, rt_dooropen, rt_tablehold,
            rt_to_first_table, sampling_time, total_sampling_time,
            presentation_box, presentation_angle, reward_available, return_angle,
            start_angle, turn_direction, rewarded, outcome, valve_time_used, iti,
        )
        self._run_iti(iti)
        print("Trial complete")

    # ── Table helpers ─────────────────────────────────────────────────────────

    def _turn_to(self, target_angle: int) -> str:
        delta = (target_angle - self._current_angle) % 360
        if delta > 180:
            delta -= 360
        if delta == 0:
            return "none"
        direction = "CW" if delta > 0 else "CCW"
        turn_table_degrees(self.ser, -delta)   # negate: firmware positive = physical CCW
        self._current_angle = target_angle % 360
        return direction

    def _turn_ccw_partial(self, degrees: int) -> None:
        """Turn CCW by degrees. Daemon thread use only."""
        turn_table_degrees(self.ser, degrees)   # positive: physical CCW (see _turn_to)
        self._current_angle = (self._current_angle - degrees) % 360

    def _log(self, trial_start, trial_end, trial_duration, rt, rt_dooropen,
             rt_tablehold, rt_to_first_table, sampling_time, total_sampling_time,
             presentation_box, presentation_angle, reward_available, return_angle,
             start_angle, turn_direction, reward_triggered, outcome,
             valve_time_used, iti):
        row = {
            "trial_num":            self.trial_counter,
            "presentation_box":     presentation_box,
            "presentation_angle":   presentation_angle,
            "reward_available":     reward_available,
            "trial_start":          trial_start,
            "trial_end":            trial_end,
            "trial_duration":       trial_duration,
            "rt":                   rt,
            "rt_dooropen":          rt_dooropen,
            "rt_tablehold":         rt_tablehold,
            "rt_to_first_table":    rt_to_first_table,
            "sampling_time":        sampling_time,
            "total_sampling_time":  total_sampling_time,
            "return_angle":         return_angle,
            "start_angle":          start_angle,
            "turn_direction":       turn_direction,
            "reward_triggered":     reward_triggered,
            "outcome":              outcome,
            "reward_count":         self.reward_count,
            "valve_time":           valve_time_used,
            "iti":                  iti,
        }
        with self._df_lock:
            self.results_df.loc[len(self.results_df)] = row
        print(self.results_df.iloc[-1].to_dict())
