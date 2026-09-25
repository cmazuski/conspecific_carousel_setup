# training.py — Classical conditioning training for social memory (rat and mouse)
#
# Trial sequence:
#   Random port LED on (from selected ports) → animal pokes within led_on_time → reward
#
# Port selection rules (applied in priority order):
#   1. Anti-camping forced port — if animal camps at one port (last 3 hits there,
#      last 3 misses at another), the neglected port is forced next.
#   2. Anti-3-in-a-row — if last 3 picks were the same port, exclude it.
#   3. Random from remaining valid ports.
#
# Reward probability:
#   reward_prob < 1.0 enables partial reinforcement — reward delivered only on a
#   proportion of pokes (e.g., 0.75 → 75 % of pokes rewarded).
#
# Species differences:
#   rat   — incremental_reward (volume scales with reward count)
#   mouse — fixed deliver_reward

import random
import time
import numpy as np
import pandas as pd

from hardware import (
    set_led, sensor_held, deliver_reward, incremental_reward, STOP_EVENT,
)
from .base_session import BaseSMSession


class ClassicalConditioningSession(BaseSMSession):

    _session_name = "Classical Conditioning"

    def __init__(
        self,
        ser,
        shared,
        species: str,
        valve_times: dict,
        ports,               # list — e.g. ["A", "B"] or ["A", "B", "C"]
        led_on_time: float,
        iti_min: float,
        iti_max: float,
        reward_prob: float = 1.0,
        session_duration: float = None,
        session_start: float = None,
    ):
        super().__init__(ser, shared, species, valve_times, session_duration, session_start)
        self.ports = list(ports)
        self.led_on_time = led_on_time
        self.ITI_MIN = iti_min
        self.ITI_MAX = iti_max
        self.reward_prob = reward_prob

        self._presentation_history = []          # last 3 port choices
        self._reward_history = {p: [] for p in self.ports}  # last 3 outcomes per port
        self._forced_port = None

        # Rat incremental reward gating: the valve time only starts growing once
        # the animal has poked every selected port at least once. Until then the
        # base valve time is used. _increment_count counts rewards delivered
        # after that point (the increment index passed to incremental_reward).
        self._poked_ports = set()
        self._increment_count = 0

        self.results_df = pd.DataFrame(columns=[
            "trial_num",
            "port",
            "forced",
            "reward_triggered",
            "reward_prob_applied",  # True if reward was withheld by probability
            "trial_start",
            "trial_end",
            "rt",
            "iti",
            "valve_time",
        ])

    # ── Reward ─────────────────────────────────────────────────────────────────

    def _deliver_reward(self, port: str) -> float:
        """Deliver reward at `port`. For rats the valve time grows incrementally,
        but the incremental increase only begins once the animal has poked every
        selected port at least once (until then the base valve time is used); the
        valve time is capped inside incremental_reward. Mice always get the fixed
        base valve time."""
        if self.species == "rat":
            vt = self.valve_times[port]
            if self._poked_ports.issuperset(self.ports):
                valve_time = incremental_reward(
                    self.ser, port, vt, self._increment_count)
                self._increment_count += 1
                return valve_time
            deliver_reward(self.ser, port, vt)
            return vt
        return super()._deliver_reward(port)

    # ── Port selection ────────────────────────────────────────────────────────

    def _pick_port(self):
        if self._forced_port is not None:
            return self._forced_port, True

        # anti-3-in-a-row: if last 3 picks were the same, exclude that port
        excluded = set()
        if len(self._presentation_history) >= 3:
            last3 = self._presentation_history[-3:]
            if len(set(last3)) == 1:
                excluded.add(last3[0])

        choices = [p for p in self.ports if p not in excluded]
        if not choices:
            choices = list(self.ports)  # fallback (only one port available)

        return random.choice(choices), False

    def _update_anti_camping(self, port: str, rewarded: bool) -> None:
        self._reward_history[port].append(rewarded)
        if len(self._reward_history[port]) > 3:
            self._reward_history[port].pop(0)

        # Clear forced port once animal pokes it successfully
        if rewarded and port == self._forced_port:
            self._forced_port = None
            for p in self.ports:
                self._reward_history[p] = []
            return

        if self._forced_port is not None:
            return

        # Check for camping: one port always missed, another always hit
        always_miss = [
            p for p in self.ports
            if len(self._reward_history[p]) == 3 and not any(self._reward_history[p])
        ]
        always_hit = [
            p for p in self.ports
            if len(self._reward_history[p]) == 3 and all(self._reward_history[p])
        ]

        if always_miss and always_hit:
            self._forced_port = always_miss[0]
            print(f"[INFO] Anti-camping: forcing port {self._forced_port}")

    # ── Trial ─────────────────────────────────────────────────────────────────

    def _run_trial(self):
        port, was_forced = self._pick_port()

        self._presentation_history.append(port)
        if len(self._presentation_history) > 3:
            self._presentation_history.pop(0)

        trial_start = time.time()
        deadline = trial_start + self.led_on_time
        set_led(self.ser, port, True)
        print(f"Port {port} LED on (forced={was_forced})")

        # require_new_trigger: only a new trigger signal while the LED is on counts —
        # if the port was already 'triggered' at cue onset (electrical glitch or
        # residual liquid on the beam), the animal must produce a fresh poke.
        poked = self._wait_for_poke(port, deadline=deadline,
                                    require_new_trigger=True)
        trial_end = time.time()
        set_led(self.ser, port, False)

        rewarded = False
        prob_withheld = False
        valve_time_used = np.nan
        rt = np.nan

        if poked:
            self._poked_ports.add(port)
            rt = trial_end - trial_start
            if random.random() < self.reward_prob:
                rewarded = True
                valve_time_used = self._deliver_reward(port)
                self.reward_count += 1
                print(f"Reward at port {port} (#{self.reward_count}, "
                      f"valve={valve_time_used:.3f} s)")
            else:
                prob_withheld = True
                print(f"Poked port {port} — reward withheld (prob={self.reward_prob:.2f})")
        else:
            print(f"Port {port} — no poke within {self.led_on_time:.1f} s")

        self._update_anti_camping(port, rewarded)

        iti = random.uniform(self.ITI_MIN, self.ITI_MAX)
        self._log(port, was_forced, rewarded, prob_withheld,
                  trial_start, trial_end, rt, iti, valve_time_used)
        self._run_iti(iti)

    def _log(self, port, forced, reward_triggered, reward_prob_applied,
             trial_start, trial_end, rt, iti, valve_time_used):
        # trial_start/trial_end relative to session_start (s), matching every
        # other output file from the same run.
        with self._df_lock:
            self.results_df.loc[len(self.results_df)] = {
                "trial_num":           self.trial_counter,
                "port":                port,
                "forced":              forced,
                "reward_triggered":    reward_triggered,
                "reward_prob_applied": reward_prob_applied,
                "trial_start":         trial_start - self.session_start,
                "trial_end":           trial_end - self.session_start,
                "rt":                  rt,
                "iti":                 iti,
                "valve_time":          valve_time_used,
            }


class AutoRewardSession(ClassicalConditioningSession):
    """No-LED free-reward session.

    No cue LED is ever presented. Whenever the animal pokes ANY of the selected
    ports, a reward is delivered at that port — the animal, not the program,
    chooses the port. After each poke an ITI elapses before the next poke can be
    rewarded (a refractory period so a camping animal isn't rewarded continuously).

    Reuses ClassicalConditioningSession's results_df / _log so the training
    performance GUI works unchanged; `forced` is always False and `rt` is the
    latency from the start of the wait to the poke. Anti-camping / port-picking
    logic does not apply here (there is no cue to place).
    """

    _session_name = "Auto Reward (no LED)"

    def __init__(
        self,
        ser,
        shared,
        species: str,
        valve_times: dict,
        ports,
        iti_min: float,
        iti_max: float,
        reward_prob: float = 1.0,
        session_duration: float = None,
        session_start: float = None,
    ):
        # led_on_time is irrelevant here (no LED) — pass 0.0 so the parent sets
        # up results_df / port state without a cue-timing parameter.
        super().__init__(
            ser=ser,
            shared=shared,
            species=species,
            valve_times=valve_times,
            ports=ports,
            led_on_time=0.0,
            iti_min=iti_min,
            iti_max=iti_max,
            reward_prob=reward_prob,
            session_duration=session_duration,
            session_start=session_start,
        )

    def _wait_for_any_poke(self):
        """Block until any selected port is triggered and held. Returns the port
        label that was poked, or None if the session stopped first."""
        while self.running and not STOP_EVENT.is_set():
            for port in self.ports:
                state, _ = self.shared.get_port(port)
                if state == "triggered" and sensor_held(self.shared, port):
                    return port
            time.sleep(0.001)
        return None

    def _run_trial(self):
        trial_start = time.time()
        port = self._wait_for_any_poke()
        trial_end = time.time()
        if port is None:
            return

        self._poked_ports.add(port)
        rt = trial_end - trial_start
        rewarded = False
        prob_withheld = False
        valve_time_used = np.nan

        if random.random() < self.reward_prob:
            rewarded = True
            valve_time_used = self._deliver_reward(port)
            self.reward_count += 1
            print(f"Auto-reward at port {port} (#{self.reward_count}, "
                  f"valve={valve_time_used:.3f} s)")
        else:
            prob_withheld = True
            print(f"Poked port {port} — reward withheld (prob={self.reward_prob:.2f})")

        iti = random.uniform(self.ITI_MIN, self.ITI_MAX)
        self._log(port, False, rewarded, prob_withheld,
                  trial_start, trial_end, rt, iti, valve_time_used)
        self._run_iti(iti)
