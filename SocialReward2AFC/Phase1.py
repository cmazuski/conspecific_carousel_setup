# Phase1.py — 2AFC autoshaping (rat and mouse)
#
# Both port A and port B LEDs light up. Animal pokes either for reward.
# Anti-camping: if the same side is poked 3× in a row, only the neglected
# side LED is shown next trial (and only that poke is accepted) until the
# animal visits it.
#
# ITI: 10–15 s (configurable via ITI_MIN/ITI_MAX overrides)
#
# Species differences:
#   rat   — incremental_reward
#   mouse — fixed deliver_reward

import random
import time
import numpy as np
import pandas as pd

from hardware import set_led, STOP_EVENT
from .base_session import Base2AFCSession


class Phase1Session2AFC(Base2AFCSession):

    _session_name = "2AFC Phase 1 (Autoshaping)"

    def __init__(
        self,
        ser,
        shared,
        species: str,
        valve_time: float,
        iti_min: float = 10.0,
        iti_max: float = 15.0,
        session_duration: float = None,
    ):
        super().__init__(ser, shared, species, valve_time, session_duration)
        self.ITI_MIN = iti_min
        self.ITI_MAX = iti_max

        self.results_df = pd.DataFrame(columns=[
            "trial_num",
            "active_ports",
            "poked_port",
            "forced",
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

        active_ports = self._get_active_reward_ports()
        was_forced   = self._forced_port is not None

        # Ensure all reward ports are cleared before lighting up
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
        trial_end  = time.time()

        for p in active_ports:
            set_led(self.ser, p, False)

        if poked_port:
            rt = trial_end - trial_start
            valve_time_used = self._deliver_reward(poked_port)
            rewarded = True
            self.reward_count += 1
            print(f"Reward at port {poked_port} "
                  f"(#{self.reward_count}, valve={valve_time_used:.3f} s)")

        self._update_anti_camping(poked_port)

        iti = random.uniform(self.ITI_MIN, self.ITI_MAX)
        row = {
            "trial_num":       self.trial_counter,
            "active_ports":    str(active_ports),
            "poked_port":      poked_port,
            "forced":          was_forced,
            "reward_triggered":rewarded,
            "trial_start":     trial_start,
            "trial_end":       trial_end,
            "rt":              rt,
            "iti":             iti,
            "reward_count":    self.reward_count,
            "valve_time":      valve_time_used,
        }
        with self._df_lock:
            self.results_df.loc[len(self.results_df)] = row
        print(self.results_df.iloc[-1].to_dict())
        self._run_iti(iti)
