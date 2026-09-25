# two_choice.py — Social Choice: port A (sucrose) vs port B (social)
#
# Trial sequence:
#   Port A + B LEDs on (or only forced port if anti-bias correction active)
#
#   Animal pokes A  (sucrose pathway):
#       Port C LED on → decision_window → hit (reward) or miss
#
#   Animal pokes B  (social pathway):
#       Wait sensors clear → rotate to social_angle → social_duration s
#       → wait sensors clear → rotate back to 0°
#
# Anti-bias: 10 consecutive same choices → force other port for 1 trial.

import random
import time
import numpy as np
import pandas as pd

from hardware import set_led, wait_for_table_stopped, STOP_EVENT
from .base_session import BaseSCSession

BIAS_THRESHOLD = 10


class TwoChoiceSession(BaseSCSession):

    _session_name = "Social Choice Two-Choice"

    def __init__(
        self,
        ser,
        shared,
        species: str,
        valve_time: float,
        decision_window: float,
        social_angle: int = 90,
        social_duration: float = 10.0,
        iti_min: float = 5.0,
        iti_max: float = 10.0,
        session_duration: float = None,
    ):
        super().__init__(ser, shared, species, valve_time, session_duration)
        self.decision_window = decision_window
        self.social_angle    = social_angle
        self.social_duration = social_duration
        self.ITI_MIN         = iti_min
        self.ITI_MAX         = iti_max

        self._streak_port  = None
        self._streak_count = 0
        self._forced_port  = None  # not None → only this LED is shown next trial

        self.results_df = pd.DataFrame(columns=[
            "trial_num",
            "active_ports",
            "poked_port",
            "forced",
            "choice_type",
            "outcome",
            "rt_ab",
            "rt_c",
            "reward_triggered",
            "social_duration",
            "trial_start",
            "trial_end",
            "iti",
            "reward_count",
            "valve_time",
        ])

    # ── Anti-bias ─────────────────────────────────────────────────────────────

    def _get_active_ports(self):
        if self._forced_port is not None:
            return [self._forced_port]
        return ["A", "B"]

    def _update_anti_bias(self, poked_port):
        if self._forced_port is not None:
            # Reset unconditionally after forced trial
            self._forced_port  = None
            self._streak_port  = None
            self._streak_count = 0
            return

        if poked_port == self._streak_port:
            self._streak_count += 1
        else:
            self._streak_port  = poked_port
            self._streak_count = 1

        if self._streak_count >= BIAS_THRESHOLD:
            other = "B" if poked_port == "A" else "A"
            self._forced_port  = other
            self._streak_count = 0
            self._streak_port  = None
            print(f"[Anti-bias] {BIAS_THRESHOLD} consecutive {poked_port} choices "
                  f"→ forcing {other}")

    # ── Trial ─────────────────────────────────────────────────────────────────

    def _run_trial(self):
        rt_ab           = np.nan
        rt_c            = np.nan
        valve_time_used = np.nan
        rewarded        = False
        outcome         = np.nan
        choice_type     = np.nan
        social_dur_rec  = np.nan
        trial_end       = np.nan

        active_ports = self._get_active_ports()
        was_forced   = self._forced_port is not None

        # Ensure A and B clear before lighting
        for p in ["A", "B"]:
            while self.running and not STOP_EVENT.is_set():
                if self.shared.get_port(p)[0] == "cleared":
                    break
                time.sleep(0.005)

        trial_start = time.time()
        for p in active_ports:
            set_led(self.ser, p, True)
        print(f"Ports {active_ports} lit (forced={was_forced})")

        poked_port = self._wait_for_any_poke(active_ports)
        t_ab_poked = time.time()

        for p in active_ports:
            set_led(self.ser, p, False)

        if not poked_port:
            return  # session stopped

        rt_ab = t_ab_poked - trial_start
        print(f"Port {poked_port} poked (rt_ab={rt_ab:.3f} s)")

        if poked_port == "A":
            # ── Sucrose pathway ───────────────────────────────────────────────
            choice_type = "sucrose"
            outcome     = "miss"

            while self.running and not STOP_EVENT.is_set():
                if self.shared.get_port("C")[0] == "cleared":
                    break
                time.sleep(0.005)

            set_led(self.ser, "C", True)
            c_onset    = time.time()
            deadline_c = c_onset + self.decision_window
            print(f"Port C LED on (window={self.decision_window:.1f} s)")

            pokedC    = self._wait_for_poke("C", deadline=deadline_c)
            trial_end = time.time()
            set_led(self.ser, "C", False)

            if not self.running and not pokedC:
                return

            if pokedC:
                rt_c            = trial_end - c_onset
                outcome         = "hit"
                valve_time_used = self._deliver_reward("C")
                rewarded        = True
                self.reward_count += 1
                print(f"Hit! Reward at C "
                      f"(#{self.reward_count}, valve={valve_time_used:.3f} s)")
            else:
                print("Miss: port C window expired")

        else:
            # ── Social pathway ────────────────────────────────────────────────
            choice_type = "social"
            outcome     = "social"

            # Wait for table + door sensors clear before rotating
            if not self._wait_for_sensors_clear():
                return

            print(f"Rotating to social angle {self.social_angle}°")
            self._turn_to(self.social_angle)
            wait_for_table_stopped(self.shared, device=self.ser)
            print(f"Social stimulus visible — {self.social_duration:.1f} s timer started")

            social_start = time.time()
            while self.running and not STOP_EVENT.is_set():
                elapsed        = time.time() - social_start
                table_state, _ = self.shared.get_port("table")
                snap           = self.shared.get()
                door_clear     = (snap.doorsensor != "triggered")
                if elapsed >= self.social_duration and table_state == "cleared" and door_clear:
                    break
                time.sleep(0.05)

            social_dur_rec = time.time() - social_start
            trial_end      = time.time()

            if not self.running or STOP_EVENT.is_set():
                return

            # Rotate back to default position
            print("Rotating back to 0°")
            self._turn_to(0)
            wait_for_table_stopped(self.shared, device=self.ser)
            print("Table returned to default")

        self._update_anti_bias(poked_port)

        iti = random.uniform(self.ITI_MIN, self.ITI_MAX)
        row = {
            "trial_num":        self.trial_counter,
            "active_ports":     str(active_ports),
            "poked_port":       poked_port,
            "forced":           was_forced,
            "choice_type":      choice_type,
            "outcome":          outcome,
            "rt_ab":            rt_ab,
            "rt_c":             rt_c,
            "reward_triggered": rewarded,
            "social_duration":  social_dur_rec,
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
