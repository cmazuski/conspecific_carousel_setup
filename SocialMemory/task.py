# task.py — Social Memory Task (rat and mouse)
#
# Session sequence:
#   n_s1 presentations of S1 (at s1_angle) with CC during each ITI
#   → CC ITI (transition)
#   → n_s2 presentations of S2 (at s2_angle) with CC during each ITI
#
# Presentation behavior (turntable move → door open → sampling timer → stimulus
# removal → door close → return home) and CC-ITI behavior are shared across
# session types — see base_session.py's _run_presentation / _run_cc_iti.
#
# Two DataFrames are recorded:
#   presentations_df — one row per stimulus presentation
#   conditioning_df  — one row per CC trial across all ITIs

import pandas as pd

from hardware import SharedSensorState, STOP_EVENT
from .base_session import BaseSMSession


class SocialMemoryTaskSession(BaseSMSession):

    _session_name = "Social Memory Task"

    def __init__(
        self,
        ser,
        shared: SharedSensorState,
        species: str,
        valve_times: dict,
        # S1 parameters
        n_s1: int,
        s1_duration: float,
        s1_angle: int,
        s1_iti_min: float,
        s1_iti_max: float,
        # S2 parameters
        n_s2: int,
        s2_duration: float,
        s2_angle: int,
        s2_iti_min: float,
        s2_iti_max: float,
        # Classical conditioning parameters (applied during all ITIs)
        cc_ports,
        cc_led_on_time: float,
        cc_iti_min: float,
        cc_iti_max: float,
        cc_reward_prob: float = 1.0,
        cc_delay: float = 0.0,
        session_start: float = None,
    ):
        super().__init__(ser, shared, species, valve_times, session_start=session_start)
        self.n_s1 = n_s1
        self.s1_duration = s1_duration
        self.s1_angle = s1_angle
        self.s1_iti_min = s1_iti_min
        self.s1_iti_max = s1_iti_max

        self.n_s2 = n_s2
        self.s2_duration = s2_duration
        self.s2_angle = s2_angle
        self.s2_iti_min = s2_iti_min
        self.s2_iti_max = s2_iti_max

        self.cc_ports = list(cc_ports)
        self.cc_led_on_time = cc_led_on_time
        self.cc_iti_min = cc_iti_min
        self.cc_iti_max = cc_iti_max
        self.cc_reward_prob = cc_reward_prob
        self.cc_delay = cc_delay

        self.presentations_df = pd.DataFrame(columns=[
            "presentation_num",
            "period",            # S1_1, S1_2, S2_1, ...
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
            "iti_period",        # CC_S1_1, CC_transition, CC_S2_1, ...
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

    # ── Session loop (override — fixed sequence, not open-ended trials) ───────

    def _run_session(self):
        print(f"[INFO] {self._session_name} started")
        print(f"[INFO] S1: {self.n_s1}× {self.s1_duration} s at {self.s1_angle}°, "
              f"ITI {self.s1_iti_min}–{self.s1_iti_max} s")
        print(f"[INFO] S2: {self.n_s2}× {self.s2_duration} s at {self.s2_angle}°, "
              f"ITI {self.s2_iti_min}–{self.s2_iti_max} s")

        try:
            # ── S1 presentations ─────────────────────────────────────────────
            for i in range(self.n_s1):
                if not self.running or STOP_EVENT.is_set():
                    break
                if i > 0:
                    self._run_cc_iti(
                        self.s1_iti_min, self.s1_iti_max,
                        f"CC_S1_pre{i + 1}"
                    )
                if not self.running or STOP_EVENT.is_set():
                    break
                self._run_presentation(self.s1_angle, self.s1_duration, f"S1_{i + 1}")

            # ── Transition ITI (last S1 → first S2) ──────────────────────────
            if self.n_s2 > 0 and self.running and not STOP_EVENT.is_set():
                self._run_cc_iti(
                    self.s1_iti_min, self.s1_iti_max,
                    "CC_transition"
                )

            # ── S2 presentations ─────────────────────────────────────────────
            for i in range(self.n_s2):
                if not self.running or STOP_EVENT.is_set():
                    break
                if i > 0:
                    self._run_cc_iti(
                        self.s2_iti_min, self.s2_iti_max,
                        f"CC_S2_pre{i + 1}"
                    )
                if not self.running or STOP_EVENT.is_set():
                    break
                self._run_presentation(self.s2_angle, self.s2_duration, f"S2_{i + 1}")

        finally:
            self.running = False
            print(f"[INFO] {self._session_name} ended")

    # Required by base but not used (sequence is managed above)
    def _run_trial(self):
        raise NotImplementedError
