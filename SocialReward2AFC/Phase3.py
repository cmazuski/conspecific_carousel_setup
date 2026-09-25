# Phase3.py — 2AFC Phases 3a and 3b (rat and mouse)
#
# Port C LED → poke C (200 s deadline; auto-opens if missed, logged)
# → door opens → animal holds table sensor >= sensory_minimum s
# → animal clears table sensor → ports A and B LEDs on → poke A or B → reward
#
# Phase 3b: pass a callable as sensory_minimum that accepts the session and
#           returns the required hold time for that trial.
#
# Anti-camping: if same reward port poked 3× → force other side next trial.
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

PORT_C_TIMEOUT       = 200
TABLE_SENSOR_TIMEOUT = 200


class Phase3Session2AFC(Base2AFCSession):

    _session_name = "2AFC Phase 3"
    ITI_MIN = 5.0
    ITI_MAX = 10.0

    def __init__(
        self,
        ser,
        shared: SharedSensorState,
        species: str,
        sensory_minimum,       # float, or callable(session) → float for 3b
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
            "rt_dooropen",
            "rt_tablehold",
            "rt_to_first_table",
            "sampling_time",
            "total_sampling_time",
            "sensory_minimum_required",
            "iti",
            "outcome",
            "auto_dooropen",
            "reward_count",
            "valve_time",
        ])

    def _run_trial(self):
        rt                  = np.nan
        rt_dooropen         = np.nan
        rt_tablehold        = np.nan
        rt_to_first_table   = np.nan
        sampling_time       = np.nan
        total_sampling_time = 0.0
        trial_start         = np.nan
        trial_end           = np.nan
        valve_time_used     = np.nan
        rewarded            = False
        auto_dooropen       = False
        poked_port          = None
        outcome             = "missed"

        required_sm = (
            self.sensory_minimum(self)
            if callable(self.sensory_minimum)
            else self.sensory_minimum
        )
        print(f"Sensory minimum this trial: {required_sm:.3f} s")

        # 1. Port C LED — wait for poke (200 s auto-open)
        set_led(self.ser, "C", True)
        print("Waiting for port C poke...")
        ledC_onset = time.time()
        deadlineC  = ledC_onset + PORT_C_TIMEOUT

        self._wait_for_ports_cleared(["C"])

        pokedC = self._wait_for_poke("C", deadline=deadlineC)

        if pokedC:
            rt_dooropen = time.time() - ledC_onset
            print(f"Port C poked (rt_dooropen={rt_dooropen:.3f} s)")
        else:
            rt_dooropen   = PORT_C_TIMEOUT
            auto_dooropen = True
            print(f"Port C timeout ({PORT_C_TIMEOUT} s) — door auto-opens")

        set_led(self.ser, "C", False)

        # 2. Open door
        threading.Thread(target=open_door, args=(self.ser,), daemon=True).start()
        door_opened = wait_for_door_state(self.shared, "door opened",
                                          timeout=DOOR_OPEN_TIMEOUT, device=self.ser)
        door_open_time = time.time()
        if door_opened:
            print("Door opened")
        elif self.running and not STOP_EVENT.is_set():
            print("[ERROR] Door never confirmed open — scoring this trial as door_failed")

        # 3. Sensory minimum (200 s timeout)
        print(f"Waiting for sensory minimum ({required_sm:.3f} s)...")
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

            if s_time >= required_sm:
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
            self._log(np.nan, None, False, np.nan, rt_dooropen, rt_tablehold,
                      rt_to_first_table, sampling_time, total_sampling_time,
                      required_sm, iti,
                      "missed" if door_opened else "door_failed",
                      auto_dooropen, np.nan)
            self._run_iti(iti)
            print("Trial complete (missed)" if door_opened
                  else "Trial complete (door failed)")
            return

        # 4. Wait for table clear
        print("Waiting for animal to clear table sensor...")
        wait_for_table_clear(self.shared)

        # 5. Ports A and B LEDs on (or only forced side)
        active_ports = self._get_active_reward_ports()
        was_forced   = self._forced_port is not None
        for p in active_ports:
            set_led(self.ser, p, True)
        print(f"Ports {active_ports} lit (forced={was_forced})")

        self._wait_for_ports_cleared(["A", "B"])

        trial_start = time.time()

        # 6. Wait for poke A or B (no deadline in phase 3)
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
                print(f"Wrong port {poked_port} during forced trial")

        self._update_anti_camping(poked_port)

        iti = random.uniform(self.ITI_MIN, self.ITI_MAX)

        threading.Thread(
            target=close_door_safe, args=(self.ser, self.shared), daemon=True
        ).start()
        wait_for_door_state(self.shared, "door closed",
                            timeout=DOOR_CLOSE_TIMEOUT, device=self.ser)

        self._log(trial_start, poked_port, rewarded, rt, rt_dooropen, rt_tablehold,
                  rt_to_first_table, sampling_time, total_sampling_time,
                  required_sm, iti, outcome, auto_dooropen, valve_time_used)
        self._run_iti(iti)
        print("Trial complete")

    def _log(self, trial_start, poked_port, rewarded, rt, rt_dooropen,
             rt_tablehold, rt_to_first_table, sampling_time, total_sampling_time,
             required_sm, iti, outcome, auto_dooropen, valve_time_used):
        row = {
            "trial_num":                self.trial_counter,
            "active_ports":             str(self._get_active_reward_ports()),
            "poked_port":               poked_port,
            "forced":                   self._forced_port is not None,
            "reward_triggered":         rewarded,
            "trial_start":              trial_start,
            "trial_end":                time.time(),
            "rt":                       rt,
            "rt_dooropen":              rt_dooropen,
            "rt_tablehold":             rt_tablehold,
            "rt_to_first_table":        rt_to_first_table,
            "sampling_time":            sampling_time,
            "total_sampling_time":      total_sampling_time,
            "sensory_minimum_required": required_sm,
            "iti":                      iti,
            "outcome":                  outcome,
            "auto_dooropen":            auto_dooropen,
            "reward_count":             self.reward_count,
            "valve_time":               valve_time_used,
        }
        with self._df_lock:
            self.results_df.loc[len(self.results_df)] = row
        print(self.results_df.iloc[-1].to_dict())
