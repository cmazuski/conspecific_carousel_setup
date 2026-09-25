# Phase2.py — 2AFC Phase 2 (rat and mouse)
#
# Door opens automatically → animal holds table sensor >= sensory_minimum s
# → animal clears table sensor → port A and B LEDs on → poke A or B → reward
#
# Sensory minimum: 200 s timeout from door open — missed trial if not met.
# Anti-camping: if animal pokes same reward port 3× in a row, only the other
#               port LED lights up next trial until that port is poked.
#
# Species differences:
#   rat   — incremental_reward
#   mouse — fixed deliver_reward

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
    SharedSensorState,
    STOP_EVENT,
    DOOR_OPEN_TIMEOUT,
    DOOR_CLOSE_TIMEOUT,
)
from .base_session import Base2AFCSession

TABLE_SENSOR_TIMEOUT = 200


class Phase2Session2AFC(Base2AFCSession):

    _session_name = "2AFC Phase 2"
    ITI_MIN = 5.0
    ITI_MAX = 10.0

    def __init__(
        self,
        ser,
        shared: SharedSensorState,
        species: str,
        sensory_minimum: float,
        valve_time: float,
        session_duration: float = None,
    ):
        super().__init__(ser, shared, species, valve_time, session_duration)
        self.sensory_minimum = sensory_minimum

        self.results_df = pd.DataFrame(columns=[
            "trial_num",
            "active_ports",
            "poked_port",
            "forced",
            "reward_triggered",
            "trial_start",
            "trial_end",
            "rt",
            "rt_tablehold",
            "rt_to_first_table",
            "sampling_time",
            "total_sampling_time",
            "iti",
            "outcome",
            "reward_count",
            "valve_time",
        ])

    def _run_trial(self):
        rt                  = np.nan
        rt_tablehold        = np.nan
        rt_to_first_table   = np.nan
        sampling_time       = np.nan
        total_sampling_time = 0.0
        trial_start         = np.nan
        trial_end           = np.nan
        valve_time_used     = np.nan
        rewarded            = False
        outcome             = "missed"

        # 1. Open door automatically
        threading.Thread(target=open_door, args=(self.ser,), daemon=True).start()
        door_opened = wait_for_door_state(self.shared, "door opened",
                                          timeout=DOOR_OPEN_TIMEOUT, device=self.ser)
        door_open_time = time.time()
        if door_opened:
            print("Door opened")
        elif self.running and not STOP_EVENT.is_set():
            print("[ERROR] Door never confirmed open — scoring this trial as door_failed")

        # 2. Sensory minimum (200 s timeout)
        print(f"Waiting for sensory minimum ({self.sensory_minimum:.3f} s)...")
        sm_met             = False
        first_contact_time = None
        deadline           = door_open_time + TABLE_SENSOR_TIMEOUT

        while door_opened and self.running and not STOP_EVENT.is_set():
            if time.time() >= deadline:
                print(f"Sensory minimum not met within {TABLE_SENSOR_TIMEOUT} s")
                break

            s_time, contact_start = self._wait_for_table_contact(deadline=deadline)
            if s_time is None:
                break

            if first_contact_time is None:
                first_contact_time = contact_start
            total_sampling_time += s_time

            if s_time >= self.sensory_minimum:
                rt_tablehold  = time.time() - door_open_time
                sampling_time = s_time
                sm_met = True
                print(f"Sensory minimum met: {s_time:.3f} s")
                break

            print(f"Sensory minimum too short ({s_time:.3f} s), retrying...")

        rt_to_first_table = (first_contact_time - door_open_time
                             if first_contact_time is not None else np.nan)

        if not sm_met:
            iti = random.uniform(self.ITI_MIN, self.ITI_MAX)
            threading.Thread(
                target=close_door_safe, args=(self.ser, self.shared), daemon=True
            ).start()
            wait_for_door_state(self.shared, "door closed",
                                timeout=DOOR_CLOSE_TIMEOUT, device=self.ser)
            self._log(np.nan, None, False, np.nan, rt_tablehold, rt_to_first_table,
                      sampling_time, total_sampling_time, iti,
                      "missed" if door_opened else "door_failed", np.nan)
            self._run_iti(iti)
            print("Trial complete (missed)" if door_opened
                  else "Trial complete (door failed)")
            return

        # 3. Wait for table clear
        print("Waiting for animal to clear table sensor...")
        wait_for_table_clear(self.shared)

        # 4. LED A and B on (or only forced side)
        active_ports = self._get_active_reward_ports()
        was_forced   = self._forced_port is not None
        for p in active_ports:
            set_led(self.ser, p, True)
        print(f"Ports {active_ports} lit (forced={was_forced})")

        # Ensure ports cleared before recording poke
        self._wait_for_ports_cleared(["A", "B"])

        trial_start = time.time()

        # 5. Wait for poke (no deadline in phase 2)
        poked_port = self._wait_for_any_poke(["A", "B"])
        trial_end  = time.time()

        for p in active_ports:
            set_led(self.ser, p, False)

        if poked_port:
            rt = trial_end - trial_start
            if poked_port in active_ports:
                rewarded = True
                outcome  = "rewarded"
                valve_time_used = self._deliver_reward(poked_port)
                self.reward_count += 1
                print(f"Reward at port {poked_port} "
                      f"(#{self.reward_count}, valve={valve_time_used:.3f} s)")
            else:
                outcome = "wrong_port"
                print(f"Wrong port {poked_port} poked during forced trial")

        self._update_anti_camping(poked_port)

        iti = random.uniform(self.ITI_MIN, self.ITI_MAX)

        threading.Thread(
            target=close_door_safe, args=(self.ser, self.shared), daemon=True
        ).start()
        wait_for_door_state(self.shared, "door closed",
                            timeout=DOOR_CLOSE_TIMEOUT, device=self.ser)

        self._log(trial_start, poked_port, rewarded, rt, rt_tablehold,
                  rt_to_first_table, sampling_time, total_sampling_time,
                  iti, outcome, valve_time_used)
        self._run_iti(iti)
        print("Trial complete")

    def _log(self, trial_start, poked_port, rewarded, rt, rt_tablehold,
             rt_to_first_table, sampling_time, total_sampling_time,
             iti, outcome, valve_time_used):
        row = {
            "trial_num":           self.trial_counter,
            "active_ports":        str(self._get_active_reward_ports()),
            "poked_port":          poked_port,
            "forced":              self._forced_port is not None,
            "reward_triggered":    rewarded,
            "trial_start":         trial_start,
            "trial_end":           time.time(),
            "rt":                  rt,
            "rt_tablehold":        rt_tablehold,
            "rt_to_first_table":   rt_to_first_table,
            "sampling_time":       sampling_time,
            "total_sampling_time": total_sampling_time,
            "iti":                 iti,
            "outcome":             outcome,
            "reward_count":        self.reward_count,
            "valve_time":          valve_time_used,
        }
        with self._df_lock:
            self.results_df.loc[len(self.results_df)] = row
        print(self.results_df.iloc[-1].to_dict())
