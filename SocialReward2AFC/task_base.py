# task_base.py — shared trial logic for Forced, Mixed, and Free 2AFC phases
#
# Trial sequence (common to all three):
#   1. Turntable moves to presentation_angle (balanced block)
#   2. Port C LED on → poke C (no deadline) → rt_dooropen recorded
#   3. Door opens → wait for fully open
#   4. Animal holds table sensor >= sensory_minimum (indefinite, retry if short)
#   5. Animal clears table sensor
#   6. 45° CCW in parallel with LED activation:
#         Forced trial → only correct_port LED on
#         Free trial   → both A and B LEDs on
#   7. Animal pokes within decision_window:
#         correct_port poked → hit + reward
#         other port poked  → error (trial ends immediately, no reward)
#         deadline elapsed  → miss
#   8. LED(s) off → close_door_safe → turntable returns to 0° or 180° (random),
#      measured from the exact physical position the table had at session start
#   9. Log → ITI (2–7 s)
#
# Correct port assignment:
#   angle_a_port = "A"  means 90° (or angle_a) → reward at port A
#   angle_b_port = "B"  means 270° (or angle_b) → reward at port B
#
# Outcomes: hit / miss / error

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
from .base_session import Base2AFCSession


class TaskBase2AFC(Base2AFCSession):

    _session_name = "2AFC Task"
    ITI_MIN = 2.0
    ITI_MAX = 7.0

    def __init__(
        self,
        ser,
        shared: SharedSensorState,
        species: str,
        valve_time: float,
        sensory_minimum: float,
        decision_window: float,
        angle_a: int = 90,    # turntable angle rewarded by port A
        angle_b: int = 270,   # turntable angle rewarded by port B
        block_size: int = 10,
        session_duration: float = None,
    ):
        super().__init__(ser, shared, species, valve_time, session_duration)
        self.sensory_minimum = sensory_minimum
        self.decision_window = decision_window
        self.angle_a   = angle_a
        self.angle_b   = angle_b
        self.block_size = block_size

        self._current_angle = 0
        self._position_block = []

        # Subclasses define self.results_df

    # ── Stimulus block ────────────────────────────────────────────────────────

    def _refill_block(self):
        half  = self.block_size // 2
        block = [self.angle_a] * half + [self.angle_b] * (self.block_size - half)
        random.shuffle(block)
        self._position_block = block

    def _correct_port(self, angle: int) -> str:
        return "A" if angle == self.angle_a else "B"

    # ── Common task trial ─────────────────────────────────────────────────────

    def _run_task_trial(self, trial_type: str) -> dict:
        """Execute one task trial.
        trial_type: 'forced' (only correct LED) or 'free' (both LEDs).
        Returns dict of trial data.
        """
        if not self._position_block:
            self._refill_block()

        presentation_angle = self._position_block.pop()
        correct_port       = self._correct_port(presentation_angle)

        rt                  = np.nan
        rt_dooropen         = np.nan
        rt_tablehold        = np.nan
        rt_to_first_table   = np.nan
        sampling_time       = np.nan
        total_sampling_time = 0.0
        trial_start         = np.nan
        trial_end           = np.nan
        valve_time_used     = np.nan
        poked_port          = None
        rewarded            = False

        # 1. Turntable to presentation angle
        start_angle    = self._current_angle
        turn_direction = self._turn_to(presentation_angle)
        print(f"Table: {turn_direction} → {presentation_angle}° "
              f"(correct port: {correct_port})")
        wait_for_table_stopped(self.shared, device=self.ser)

        # 2. Port C LED on → poke C (no deadline)
        set_led(self.ser, "C", True)
        print("Waiting for port C poke...")
        ledC_onset = time.time()

        self._wait_for_ports_cleared(["C"])

        pokedC = self._wait_for_poke("C")
        if not pokedC:
            set_led(self.ser, "C", False)
            return {}   # session stopped mid-trial

        rt_dooropen = time.time() - ledC_onset
        set_led(self.ser, "C", False)
        print(f"Port C poked (rt_dooropen={rt_dooropen:.3f} s)")

        # 3. Open door → wait for fully open
        threading.Thread(target=open_door, args=(self.ser,), daemon=True).start()
        if not wait_for_door_state(self.shared, "door opened",
                                   timeout=DOOR_OPEN_TIMEOUT, device=self.ser):
            if not self.running or STOP_EVENT.is_set():
                return {}   # session stopped mid-trial — nothing to log
            print("[ERROR] Door never confirmed open — trial scored as door_failed")
            # The door may well be physically open; close it before the next
            # trial rotates the turntable.
            close_door_safe(self.ser, self.shared, timeout=DOOR_CLOSE_TIMEOUT)
            # Log the trial rather than dropping it: a silently skipped row
            # leaves a gap in trial_num that is indistinguishable from a session
            # stop, and hides how often the door is failing.
            return {
                "presentation_angle": presentation_angle,
                "correct_port":       correct_port,
                "poked_port":         None,
                "trial_type":         trial_type,
                "outcome":            "door_failed",
                "rt":                 np.nan,
                "rt_dooropen":        rt_dooropen,
                "rt_tablehold":       np.nan,
                "rt_to_first_table":  np.nan,
                "sampling_time":      np.nan,
                "total_sampling_time": 0.0,
                "start_angle":        start_angle,
                "turn_direction":     turn_direction,
                "reward_triggered":   False,
                "valve_time":         np.nan,
                "trial_start":        np.nan,
                "trial_end":          time.time(),
            }
        door_open_time = time.time()
        print("Door opened — sensory timer started")

        # 4. Sensory minimum (indefinite, retry loop)
        print(f"Waiting for sensory minimum ({self.sensory_minimum:.3f} s)...")
        first_contact_time = None
        while self.running and not STOP_EVENT.is_set():
            s_time, contact_start = self._wait_for_table_contact()
            if s_time is None:
                return {}

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
            return {}

        # 5. Wait for table clear
        print("Waiting for animal to clear table sensor...")
        wait_for_table_clear(self.shared)

        # 6. 45° CCW (async) + reward port LED(s)
        if trial_type == "forced":
            active_ports = [correct_port]
        else:
            active_ports = ["A", "B"]

        for p in active_ports:
            set_led(self.ser, p, True)
        print(f"LEDs on: {active_ports} | 45° CCW starting")
        threading.Thread(
            target=self._turn_ccw_partial, args=(45,), daemon=True
        ).start()

        # Ensure ports cleared before accepting poke
        self._wait_for_ports_cleared(["A", "B"])

        trial_start = time.time()
        deadline_ab = trial_start + self.decision_window

        # 7. Wait for poke A or B within decision window
        poked_port = self._wait_for_any_poke(["A", "B"], deadline=deadline_ab)
        trial_end  = time.time()

        for p in active_ports:
            set_led(self.ser, p, False)

        # Classify outcome
        if poked_port == correct_port:
            rt       = trial_end - trial_start
            rewarded = True
            outcome  = "hit"
            valve_time_used = self._deliver_reward(correct_port)
            self.reward_count += 1
            print(f"Hit! Reward at port {correct_port} "
                  f"(#{self.reward_count}, valve={valve_time_used:.3f} s)")
        elif poked_port is not None:
            rt      = trial_end - trial_start
            outcome = "error"
            print(f"Error: poked port {poked_port}, correct was {correct_port}")
        else:
            outcome = "miss"
            print("Miss: decision window expired")

        # 8. Close door safely + return turntable
        threading.Thread(
            target=close_door_safe, args=(self.ser, self.shared), daemon=True
        ).start()
        door_closed = wait_for_door_state(self.shared, "door closed",
                                          timeout=DOOR_CLOSE_TIMEOUT, device=self.ser)

        if not door_closed and self.running and not STOP_EVENT.is_set():
            # Warn and carry on: the close_door_safe thread spawned above is
            # still working the door in the background, and the next trial has
            # to rotate the turntable regardless, so stopping here would only
            # delay the same movement. Check the door if you see this repeatedly.
            print(f"[WARNING] Door not confirmed closed within {DOOR_CLOSE_TIMEOUT:.0f} s "
                  f"— continuing anyway; turntable is about to move")

        if self.running and not STOP_EVENT.is_set():
            # Wait for the 45° partial move to finish before computing the return
            # turn, so _current_angle is up to date (it is written by that thread).
            wait_for_table_stopped(self.shared, device=self.ser)
            return_angle = random.choice([0, 180])
            self._turn_to(return_angle)
            wait_for_table_stopped(self.shared, device=self.ser)
            print(f"Table returned to {return_angle}°")

        return {
            "presentation_angle": presentation_angle,
            "correct_port":       correct_port,
            "poked_port":         poked_port,
            "trial_type":         trial_type,
            "outcome":            outcome,
            "rt":                 rt,
            "rt_dooropen":        rt_dooropen,
            "rt_tablehold":       rt_tablehold,
            "rt_to_first_table":  rt_to_first_table,
            "sampling_time":      sampling_time,
            "total_sampling_time": total_sampling_time,
            "start_angle":        start_angle,
            "turn_direction":     turn_direction,
            "reward_triggered":   rewarded,
            "valve_time":         valve_time_used,
            "trial_start":        trial_start,
            "trial_end":          trial_end,
        }

    # ── Turntable helpers ─────────────────────────────────────────────────────

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
        """Turn CCW by degrees. Called in a daemon thread."""
        turn_table_degrees(self.ser, degrees)   # positive: physical CCW (see _turn_to)
        self._current_angle = (self._current_angle - degrees) % 360
