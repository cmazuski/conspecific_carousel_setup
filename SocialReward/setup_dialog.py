# SocialReward/setup_dialog.py — Pre-session configuration dialog for Social Reward
import json
import os
import tkinter as tk
from tkinter import ttk, messagebox

from utils import parse_motor_speed
from tk_window_helpers import make_scrollable, fit_window_to_screen

_SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "last_settings.json")

SPECIES_DEFAULTS = {
    "rat": {
        "valve_time": 0.30,
        "session_duration_s": 3600,
        "session_duration_trials": "",
        "sensory_minimum": 0.100,          # phases 2 and 3a
        "phase4_sensory_min": 2.0,
        "phase4_decision_window": 5.0,
        "phase3b_thresholds": [15, 30, 50],
        "phase3b_holds": [0.5, 1.0, 1.5, 2.0],
        "task_sensory_min": 2.0,
        "task_decision_window": 10.0,
        "task_rewarded_box": 1,
        "task_unrewarded_box": 3,
        "4stim_sensory_min": 2.0,
        "4stim_decision_window": 5.0,
        "4stim_box0_freq": 40, "4stim_box0_reward": "+",
        "4stim_box1_freq":  5, "4stim_box1_reward": "-",
        "4stim_box2_freq":  5, "4stim_box2_reward": "-",
        "4stim_box3_freq": 40, "4stim_box3_reward": "+",
        "door_open_speed":  255,
        "door_close_speed": 40,
        "table_speed":      40,
    },
    "mouse": {
        "valve_time": 0.20,
        "session_duration_s": 3600,
        "session_duration_trials": "",
        "sensory_minimum": 0.100,          # phases 2 and 3a
        "phase4_sensory_min": 1.5,
        "phase4_decision_window": 5.0,
        "phase3b_thresholds": [15, 30],
        "phase3b_holds": [0.5, 1.0, 1.5],
        "task_sensory_min": 2.0,
        "task_decision_window": 5.0,
        "task_rewarded_box": 1,
        "task_unrewarded_box": 3,
        "4stim_sensory_min": 2.0,
        "4stim_decision_window": 5.0,
        "4stim_box0_freq": 40, "4stim_box0_reward": "+",
        "4stim_box1_freq":  5, "4stim_box1_reward": "-",
        "4stim_box2_freq":  5, "4stim_box2_reward": "-",
        "4stim_box3_freq": 40, "4stim_box3_reward": "+",
        "door_open_speed":  255,
        "door_close_speed": 40,
        "table_speed":      40,
    },
}


class SetupDialog:
    """Tkinter dialog that collects session parameters and returns them as a dict."""

    def __init__(self):
        self.result = None
        self.root = tk.Tk()
        self.root.title("Social Reward Task — Session Setup")
        self.root.resizable(False, True)

        self._species_var = tk.StringVar(value="rat")
        self._vars = {}
        self._timing_vars = {}
        self._phase3b_threshold_vars = []
        self._phase3b_hold_vars = []
        self._sm_frame    = None   # sensory minimum only (phases 2, 3a)
        self._p4_frame    = None   # phase 4
        self._p3b_frame   = None   # phase 3b
        self._task_frame  = None   # task
        self._4stim_frame = None   # 4stimuli

        self._build_ui()
        self._on_species_change()
        self._on_phase_change()
        self._apply_saved_settings()
        fit_window_to_screen(self._scroll_body)

    # ── Settings persistence ──────────────────────────────────────────────────

    def _save_settings(self):
        s = {"species": self._species_var.get()}
        for k, v in self._vars.items():
            s[k] = v.get()
        for k, v in self._timing_vars.items():
            s[f"t_{k}"] = v.get()
        for i, v in enumerate(self._phase3b_threshold_vars):
            s[f"p3b_thr_{i}"] = v.get()
        for i, v in enumerate(self._phase3b_hold_vars):
            s[f"p3b_hold_{i}"] = v.get()
        for box, v in self._4stim_freq_vars.items():
            s[f"4stim_freq_{box}"] = v.get()
        for box, v in self._4stim_reward_vars.items():
            s[f"4stim_reward_{box}"] = v.get()
        try:
            with open(_SETTINGS_FILE, "w") as f:
                json.dump(s, f, indent=2)
        except Exception:
            pass

    def _apply_saved_settings(self):
        try:
            with open(_SETTINGS_FILE) as f:
                s = json.load(f)
        except Exception:
            return
        if "species" in s:
            self._species_var.set(s["species"])
            self._on_species_change()
        if "phase" in s:
            self._vars["phase"].set(s["phase"])
            self._on_phase_change()
        for k, v in self._vars.items():
            if k in s:
                v.set(s[k])
        for k, v in self._timing_vars.items():
            if f"t_{k}" in s:
                v.set(s[f"t_{k}"])
        for i, v in enumerate(self._phase3b_threshold_vars):
            if f"p3b_thr_{i}" in s:
                v.set(s[f"p3b_thr_{i}"])
        for i, v in enumerate(self._phase3b_hold_vars):
            if f"p3b_hold_{i}" in s:
                v.set(s[f"p3b_hold_{i}"])
        for box, v in self._4stim_freq_vars.items():
            if f"4stim_freq_{box}" in s:
                v.set(s[f"4stim_freq_{box}"])
        for box, v in self._4stim_reward_vars.items():
            if f"4stim_reward_{box}" in s:
                v.set(s[f"4stim_reward_{box}"])

    def _row(self, parent, label_text, var, row, width=20):
        pad = {"padx": 8, "pady": 3}
        tk.Label(parent, text=label_text, anchor="w").grid(
            row=row, column=0, sticky="w", **pad)
        tk.Entry(parent, textvariable=var, width=width).grid(
            row=row, column=1, sticky="w", **pad)

    def _build_ui(self):
        root = make_scrollable(self.root)
        self._scroll_body = root
        pad = {"padx": 8, "pady": 3}

        # ── Header ────────────────────────────────────────────────────────────
        tk.Label(root, text="Social Reward Task — Session Setup",
                 font=("Arial", 13, "bold")).grid(
            row=0, column=0, columnspan=4, pady=(12, 4))

        # ── Species ───────────────────────────────────────────────────────────
        tk.Label(root, text="Species:", anchor="w").grid(
            row=1, column=0, sticky="w", **pad)
        sf = tk.Frame(root)
        sf.grid(row=1, column=1, columnspan=3, sticky="w", **pad)
        for species in ("Rat", "Mouse"):
            tk.Radiobutton(sf, text=species,
                           variable=self._species_var, value=species.lower(),
                           command=self._on_species_change).pack(side="left", padx=4)

        ttk.Separator(root, orient="horizontal").grid(
            row=2, column=0, columnspan=4, sticky="ew", padx=8, pady=6)

        # ── Core session fields ───────────────────────────────────────────────
        core_fields = [
            ("Animal ID:",   "animal",    ""),
            ("Session #:",   "session_n", "1"),
            ("Serial Port:", "port",      "COM3"),
            ("Baud Rate:",   "baud",      "115200"),
        ]
        for i, (label, key, default) in enumerate(core_fields):
            var = tk.StringVar(value=default)
            self._vars[key] = var
            self._row(root, label, var, row=3 + i)

        # Phase dropdown
        tk.Label(root, text="Phase:", anchor="w").grid(
            row=7, column=0, sticky="w", **pad)
        self._vars["phase"] = tk.StringVar(value="1")
        phase_cb = ttk.Combobox(root, textvariable=self._vars["phase"],
                                values=["1", "2", "3a", "3b", "4", "task", "4stimuli"],
                                state="readonly", width=10)
        phase_cb.grid(row=7, column=1, sticky="w", **pad)
        phase_cb.bind("<<ComboboxSelected>>", lambda _e: self._on_phase_change())

        # ── Phase description (updated by _on_phase_change) ──────────────────
        self._phase_desc_label = tk.Label(
            root, text="", anchor="w", justify="left",
            wraplength=400, fg="#333333", font=("Arial", 9),
            bg=root.cget("bg"))
        self._phase_desc_label.grid(
            row=8, column=0, columnspan=4, sticky="w", padx=12, pady=(4, 0))

        ttk.Separator(root, orient="horizontal").grid(
            row=9, column=0, columnspan=4, sticky="ew", padx=8, pady=6)

        # ── Timing parameters (always visible) ───────────────────────────────
        timing_frame = tk.LabelFrame(root, text="Timing Parameters", padx=8, pady=4)
        timing_frame.grid(row=10, column=0, columnspan=4, sticky="ew", padx=8, pady=4)

        # Valve time
        var = tk.StringVar()
        self._timing_vars["valve_time"] = var
        self._row(timing_frame, "Valve Time (s):", var, row=0)

        # Session duration — time OR trials
        tk.Label(timing_frame, text="Session Duration:", anchor="w").grid(
            row=1, column=0, sticky="w", padx=8, pady=3)

        dur_frame = tk.Frame(timing_frame)
        dur_frame.grid(row=1, column=1, sticky="w", pady=3)

        self._timing_vars["session_duration_s"] = tk.StringVar()
        self._timing_vars["session_duration_trials"] = tk.StringVar()

        tk.Entry(dur_frame, textvariable=self._timing_vars["session_duration_s"],
                 width=7).pack(side="left")
        tk.Label(dur_frame, text=" s  OR ").pack(side="left")
        tk.Entry(dur_frame, textvariable=self._timing_vars["session_duration_trials"],
                 width=5).pack(side="left")
        tk.Label(dur_frame, text=" trials  (first reached ends session)").pack(side="left")

        # Motor speeds (0-255)
        for i, (label, key) in enumerate([
            ("Door Open Speed (0-255):",  "door_open_speed"),
            ("Door Close Speed (0-255):", "door_close_speed"),
            ("Turntable Speed (0-255):",  "table_speed"),
        ]):
            var = tk.StringVar()
            self._timing_vars[key] = var
            self._row(timing_frame, label, var, row=2 + i)
        tk.Label(timing_frame,
                 text="(blank or 'off' = leave unset — for firmware without speed control)",
                 font=("Arial", 8), fg="gray").grid(
            row=5, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 2))

        # ── Sensory Minimum only (phases 2 and 3a) ───────────────────────────
        self._sm_frame = tk.LabelFrame(root, text="Phase Parameters", padx=8, pady=4)
        self._sm_frame.grid(row=11, column=0, columnspan=4, sticky="ew", padx=8, pady=4)

        var = tk.StringVar()
        self._timing_vars["sensory_minimum"] = var
        self._row(self._sm_frame, "Sensory Minimum (s):", var, row=0)

        # ── Phase 4 parameters ────────────────────────────────────────────────
        self._p4_frame = tk.LabelFrame(root, text="Phase Parameters", padx=8, pady=4)
        self._p4_frame.grid(row=11, column=0, columnspan=4, sticky="ew", padx=8, pady=4)

        for i, (label, key) in enumerate([
            ("Sensory Minimum (s):", "phase4_sensory_min"),
            ("Decision Window (s):", "phase4_decision_window"),
        ]):
            var = tk.StringVar()
            self._timing_vars[key] = var
            self._row(self._p4_frame, label, var, row=i)

        # ── Task parameters ───────────────────────────────────────────────────
        self._task_frame = tk.LabelFrame(root, text="Phase Parameters", padx=8, pady=4)
        self._task_frame.grid(row=11, column=0, columnspan=4, sticky="ew", padx=8, pady=4)

        for i, (label, key) in enumerate([
            ("Sensory Minimum (s):", "task_sensory_min"),
            ("Decision Window (s):", "task_decision_window"),
            ("Rewarded box (0–3):", "task_rewarded_box"),
            ("Unrewarded box (0–3):", "task_unrewarded_box"),
        ]):
            var = tk.StringVar()
            self._timing_vars[key] = var
            self._row(self._task_frame, label, var, row=i)

        tk.Label(self._task_frame,
                 text="Box positions: 0=home, 1=90° CW, 2=180°, 3=270° CW",
                 fg="gray", font=("Arial", 8)).grid(
            row=4, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 2))

        # ── 4-Stimuli parameters ──────────────────────────────────────────────
        self._4stim_frame = tk.LabelFrame(root, text="Phase Parameters", padx=8, pady=4)
        self._4stim_frame.grid(row=11, column=0, columnspan=4, sticky="ew", padx=8, pady=4)

        for i, (label, key) in enumerate([
            ("Sensory Minimum (s):", "4stim_sensory_min"),
            ("Decision Window (s):", "4stim_decision_window"),
        ]):
            var = tk.StringVar()
            self._timing_vars[key] = var
            self._row(self._4stim_frame, label, var, row=i)

        tk.Label(self._4stim_frame,
                 text="Box positions: 0=home, 1=90° CW, 2=180°, 3=270° CW",
                 fg="gray", font=("Arial", 8)).grid(
            row=2, column=0, columnspan=4, sticky="w", padx=8, pady=(2, 2))

        # Header row for box table
        for col, txt in enumerate(("Box", "Frequency (%)", "Reward")):
            tk.Label(self._4stim_frame, text=txt, font=("Arial", 9, "bold")).grid(
                row=3, column=col, padx=6, pady=(4, 2))

        self._4stim_freq_vars   = {}   # {box_num: StringVar}
        self._4stim_reward_vars = {}   # {box_num: StringVar  (+/-)}
        for box in range(4):
            tk.Label(self._4stim_frame, text=f"Box {box}").grid(
                row=4 + box, column=0, padx=6, pady=2, sticky="w")

            fv = tk.StringVar()
            self._4stim_freq_vars[box] = fv
            tk.Entry(self._4stim_frame, textvariable=fv, width=8).grid(
                row=4 + box, column=1, padx=6, pady=2)

            rv = tk.StringVar(value="+")
            self._4stim_reward_vars[box] = rv
            ttk.Combobox(self._4stim_frame, textvariable=rv,
                         values=["+", "-"], state="readonly", width=4).grid(
                row=4 + box, column=2, padx=6, pady=2)

        # ── Phase 3b gradual sensory minimum ─────────────────────────────────
        self._p3b_frame = tk.LabelFrame(
            root,
            text="Phase 3b Gradual Sensory Minimum  (leave blank to remove a step)",
            padx=8, pady=4)
        self._p3b_frame.grid(row=11, column=0, columnspan=4, sticky="ew", padx=8, pady=4)

        tk.Label(self._p3b_frame, text="Trial threshold (<)", anchor="w").grid(
            row=0, column=0, columnspan=2, sticky="w", padx=4)
        tk.Label(self._p3b_frame, text="Sensory Min (s)", anchor="w").grid(
            row=0, column=2, sticky="w", padx=4)

        self._phase3b_threshold_vars = [tk.StringVar() for _ in range(3)]
        self._phase3b_hold_vars = [tk.StringVar() for _ in range(4)]

        for i in range(3):
            tk.Label(self._p3b_frame, text=f"Step {i+1}:").grid(
                row=1 + i, column=0, sticky="e", padx=4, pady=2)
            tk.Entry(self._p3b_frame, textvariable=self._phase3b_threshold_vars[i],
                     width=6).grid(row=1 + i, column=1, sticky="w", padx=4, pady=2)
            tk.Entry(self._p3b_frame, textvariable=self._phase3b_hold_vars[i],
                     width=6).grid(row=1 + i, column=2, sticky="w", padx=4, pady=2)

        tk.Label(self._p3b_frame, text="Final (≥ last threshold):").grid(
            row=4, column=0, columnspan=2, sticky="e", padx=4, pady=2)
        tk.Entry(self._p3b_frame, textvariable=self._phase3b_hold_vars[3],
                 width=6).grid(row=4, column=2, sticky="w", padx=4, pady=2)

        # ── Notes ─────────────────────────────────────────────────────────────
        ttk.Separator(root, orient="horizontal").grid(
            row=12, column=0, columnspan=4, sticky="ew", padx=8, pady=6)

        tk.Label(root, text="Notes:", anchor="w").grid(
            row=13, column=0, sticky="nw", **pad)
        self._notes = tk.Text(root, width=36, height=3, font=("Arial", 9))
        self._notes.grid(row=13, column=1, columnspan=3, sticky="ew", **pad)

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_frame = tk.Frame(root)
        btn_frame.grid(row=14, column=0, columnspan=4, pady=(8, 14))
        tk.Button(btn_frame, text="Start Session", bg="#4CAF50", fg="white",
                  font=("Arial", 11, "bold"), width=18,
                  command=self._on_start).pack(side="left", padx=8)
        tk.Button(btn_frame, text="Cancel", width=10,
                  command=self.root.destroy).pack(side="left", padx=8)

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _on_species_change(self):
        species = self._species_var.get()
        d = SPECIES_DEFAULTS[species]
        self._timing_vars["valve_time"].set(str(d["valve_time"]))
        self._timing_vars["session_duration_s"].set(str(d["session_duration_s"]))
        self._timing_vars["session_duration_trials"].set(str(d["session_duration_trials"]))
        self._timing_vars["door_open_speed"].set(str(d["door_open_speed"]))
        self._timing_vars["door_close_speed"].set(str(d["door_close_speed"]))
        self._timing_vars["table_speed"].set(str(d["table_speed"]))
        self._timing_vars["sensory_minimum"].set(str(d["sensory_minimum"]))
        self._timing_vars["phase4_sensory_min"].set(str(d["phase4_sensory_min"]))
        self._timing_vars["phase4_decision_window"].set(str(d["phase4_decision_window"]))
        self._timing_vars["task_sensory_min"].set(str(d["task_sensory_min"]))
        self._timing_vars["task_decision_window"].set(str(d["task_decision_window"]))
        self._timing_vars["task_rewarded_box"].set(str(d["task_rewarded_box"]))
        self._timing_vars["task_unrewarded_box"].set(str(d["task_unrewarded_box"]))
        self._timing_vars["4stim_sensory_min"].set(str(d["4stim_sensory_min"]))
        self._timing_vars["4stim_decision_window"].set(str(d["4stim_decision_window"]))
        for box in range(4):
            self._4stim_freq_vars[box].set(str(d[f"4stim_box{box}_freq"]))
            self._4stim_reward_vars[box].set(str(d[f"4stim_box{box}_reward"]))

        thresholds = d["phase3b_thresholds"]
        holds = d["phase3b_holds"]
        for i in range(3):
            self._phase3b_threshold_vars[i].set(
                str(thresholds[i]) if i < len(thresholds) else "")
        for i in range(4):
            self._phase3b_hold_vars[i].set(
                str(holds[i]) if i < len(holds) else "")

    _PHASE_DESCRIPTIONS = {
        "1": (
            "Autoshaping. Port C LED illuminates on each trial; a poke at port C "
            "delivers reward. Used to establish the initial poke response before "
            "any door or turntable involvement."
        ),
        "2": (
            "Sensory door sampling. The door opens automatically and the animal must "
            "hold the table sensor for the required minimum duration. On clearing the "
            "sensor, port C LED lights and a poke delivers reward. Trial is marked "
            "missed if the sensory minimum is not met within 200 s."
        ),
        "3a": (
            "Port-initiated door. The animal pokes port A to trigger the door. It "
            "then holds the table sensor for the sensory minimum, clears the sensor, "
            "and pokes port C for reward. Port A has a 200 s deadline — if not poked "
            "in time the door opens automatically and the trial continues."
        ),
        "3b": (
            "Gradual sensory minimum. Identical to phase 3a but the required hold "
            "time increases in steps across trials according to configurable "
            "trial-number thresholds, allowing incremental shaping."
        ),
        "4": (
            "Decision window. Identical to phase 3a but port C must be poked within "
            "a fixed decision window after the sensor is cleared. Trials where the "
            "window expires are logged as missed."
        ),
        "task": (
            "Full social reward task. The turntable presents a rewarded or unrewarded "
            "box position in balanced blocks. The animal pokes port A to open the "
            "door, meets the sensory minimum, and must poke port C within the "
            "decision window. Reward is only available at the rewarded box position."
        ),
        "4stimuli": (
            "Four-box weighted presentation. Each of the four box positions (0–3) "
            "has an independently configurable presentation frequency and reward "
            "status (+/−). After each trial the turntable makes a random 45–180° "
            "return turn rather than returning to a fixed position. All other trial "
            "steps (port A, door, sensory minimum, port C decision window) are "
            "identical to the task phase."
        ),
    }

    def _on_phase_change(self):
        phase = self._vars["phase"].get()
        for frame in (self._sm_frame, self._p4_frame, self._task_frame,
                      self._p3b_frame, self._4stim_frame):
            frame.grid_remove()
        if phase in ("2", "3a"):
            self._sm_frame.grid()
        elif phase == "4":
            self._p4_frame.grid()
        elif phase == "task":
            self._task_frame.grid()
        elif phase == "3b":
            self._p3b_frame.grid()
        elif phase == "4stimuli":
            self._4stim_frame.grid()
        if self._phase_desc_label is not None:
            self._phase_desc_label.config(
                text=self._PHASE_DESCRIPTIONS.get(phase, ""))
        self.root.update_idletasks()

    def _on_start(self):
        errors = []
        phase = self._vars["phase"].get().strip()

        animal = self._vars["animal"].get().strip() or "unknown"
        session_n = self._vars["session_n"].get().strip() or "1"
        port = self._vars["port"].get().strip()
        if not port:
            errors.append("Serial port is required.")

        try:
            baud = int(self._vars["baud"].get().strip())
        except ValueError:
            errors.append("Baud rate must be an integer.")
            baud = None

        try:
            valve_time = float(self._timing_vars["valve_time"].get())
        except ValueError:
            errors.append("Valve time must be a number.")
            valve_time = None

        try:
            door_open_speed = parse_motor_speed(self._timing_vars["door_open_speed"].get())
            door_close_speed = parse_motor_speed(self._timing_vars["door_close_speed"].get())
            table_speed = parse_motor_speed(self._timing_vars["table_speed"].get())
        except ValueError:
            errors.append("Motor speeds must be integers (0-255), or blank/'off' to leave unset.")
            door_open_speed = door_close_speed = table_speed = None

        # Session duration — at least one of time or trials must be set
        duration_s_str = self._timing_vars["session_duration_s"].get().strip()
        duration_t_str = self._timing_vars["session_duration_trials"].get().strip()
        session_duration_s = session_duration_trials = None
        if not duration_s_str and not duration_t_str:
            errors.append("Set at least one session duration (time in seconds or number of trials).")
        else:
            if duration_s_str:
                try:
                    session_duration_s = int(duration_s_str)
                except ValueError:
                    errors.append("Session duration (s) must be an integer.")
            if duration_t_str:
                try:
                    session_duration_trials = int(duration_t_str)
                except ValueError:
                    errors.append("Session duration (trials) must be an integer.")

        # Sensory minimum (phases 2, 3a)
        sensory_minimum = None
        if phase in ("2", "3a"):
            try:
                sensory_minimum = float(self._timing_vars["sensory_minimum"].get())
            except ValueError:
                errors.append("Sensory Minimum must be a number.")

        # Phase 4 params
        phase4_sensory_min = phase4_decision_window = None
        if phase == "4":
            try:
                phase4_sensory_min = float(self._timing_vars["phase4_sensory_min"].get())
                phase4_decision_window = float(self._timing_vars["phase4_decision_window"].get())
            except ValueError:
                errors.append("Phase 4 Sensory Minimum and Decision Window must be numbers.")

        # Task params
        task_sensory_min = task_decision_window = None
        task_rewarded_angle = task_unrewarded_angle = None
        if phase == "task":
            try:
                task_sensory_min     = float(self._timing_vars["task_sensory_min"].get())
                task_decision_window = float(self._timing_vars["task_decision_window"].get())
                rewarded_box    = int(self._timing_vars["task_rewarded_box"].get())
                unrewarded_box  = int(self._timing_vars["task_unrewarded_box"].get())
                if rewarded_box not in (0, 1, 2, 3) or unrewarded_box not in (0, 1, 2, 3):
                    errors.append("Box numbers must be 0, 1, 2, or 3.")
                elif rewarded_box == unrewarded_box:
                    errors.append("Rewarded and unrewarded boxes must be different.")
                else:
                    task_rewarded_angle   = rewarded_box * 90
                    task_unrewarded_angle = unrewarded_box * 90
            except ValueError:
                errors.append(
                    "Task Sensory Minimum, Decision Window, and box numbers must be numbers.")

        # 4-Stimuli params
        stim4_config = None
        if phase == "4stimuli":
            try:
                s4_sensory_min  = float(self._timing_vars["4stim_sensory_min"].get())
                s4_decision_win = float(self._timing_vars["4stim_decision_window"].get())
                stim4_config = {
                    "sensory_min":  s4_sensory_min,
                    "decision_win": s4_decision_win,
                    "box_config": {},
                }
                for box in range(4):
                    freq = float(self._4stim_freq_vars[box].get())
                    if freq < 0:
                        errors.append(f"Box {box} frequency must be ≥ 0.")
                    stim4_config["box_config"][box] = {
                        "freq":     freq,
                        "rewarded": self._4stim_reward_vars[box].get() == "+",
                    }
                if sum(v["freq"] for v in stim4_config["box_config"].values()) <= 0:
                    errors.append("At least one box must have frequency > 0.")
            except ValueError:
                errors.append("4-Stimuli: all frequency and timing values must be numbers.")

        # Phase 3b gradual sensory minimum
        thresholds, holds = [], []
        if phase == "3b":
            try:
                for i in range(3):
                    t = self._phase3b_threshold_vars[i].get().strip()
                    h = self._phase3b_hold_vars[i].get().strip()
                    if t and h:
                        thresholds.append(int(t))
                        holds.append(float(h))
                    elif t or h:
                        errors.append(
                            f"Phase 3b step {i+1}: fill both threshold and sensory minimum, or leave both blank.")
                final_h = self._phase3b_hold_vars[3].get().strip()
                if final_h:
                    holds.append(float(final_h))
                else:
                    errors.append("Phase 3b final sensory minimum (s) is required.")
            except ValueError:
                errors.append("Phase 3b values must be numbers.")

        if errors:
            messagebox.showerror("Input Error", "\n".join(errors))
            return

        self.result = {
            "species": self._species_var.get(),
            "animal": animal,
            "session_n": session_n,
            "phase": phase,
            "port": port,
            "baud": baud,
            "valve_time": valve_time,
            "door_open_speed": door_open_speed,
            "door_close_speed": door_close_speed,
            "table_speed": table_speed,
            "session_duration_s": session_duration_s,
            "session_duration_trials": session_duration_trials,
            "sensory_minimum": sensory_minimum,
            "phase4_sensory_min": phase4_sensory_min,
            "phase4_decision_window": phase4_decision_window,
            "task_sensory_min": task_sensory_min,
            "task_decision_window": task_decision_window,
            "task_rewarded_angle": task_rewarded_angle,
            "task_unrewarded_angle": task_unrewarded_angle,
            "stim4_config": stim4_config,
            "phase3b_thresholds": thresholds,
            "phase3b_holds": holds,
            "notes": self._notes.get("1.0", "end").strip(),
        }
        self._save_settings()
        self.root.destroy()

    # ── Public ────────────────────────────────────────────────────────────────

    def run(self):
        """Block until the dialog closes; returns the params dict or None."""
        self.root.mainloop()
        return self.result
