# SocialReward2AFC/setup_dialog.py — Pre-session configuration dialog for 2AFC Social Reward
import json
import os
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from utils import parse_motor_speed
from tk_window_helpers import make_scrollable, fit_window_to_screen
from camera_select import CameraChooser

_SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "last_settings.json")

SPECIES_DEFAULTS = {
    "rat": {
        "valve_time":          0.30,
        "session_duration_s":  3600,
        "session_duration_t":  "",
        "iti_min":             10.0,
        "iti_max":             15.0,
        "sensory_minimum":     0.100,
        "phase4_sensory_min":  2.0,
        "phase4_decision_win": 5.0,
        "phase3b_thresholds":  [15, 30, 50],
        "phase3b_holds":       [0.5, 1.0, 1.5, 2.0],
        "task_sensory_min":    2.0,
        "task_decision_win":   10.0,
        "angle_a":             90,
        "angle_b":             270,
        "mixed_start_ratio":   0.75,
        "door_open_speed":     255,
        "door_close_speed":    40,
        "table_speed":         40,
    },
    "mouse": {
        "valve_time":          0.20,
        "session_duration_s":  3600,
        "session_duration_t":  "",
        "iti_min":             10.0,
        "iti_max":             15.0,
        "sensory_minimum":     0.100,
        "phase4_sensory_min":  1.5,
        "phase4_decision_win": 5.0,
        "phase3b_thresholds":  [15, 30],
        "phase3b_holds":       [0.5, 1.0, 1.5],
        "task_sensory_min":    2.0,
        "task_decision_win":   5.0,
        "angle_a":             90,
        "angle_b":             270,
        "mixed_start_ratio":   0.75,
        "door_open_speed":     255,
        "door_close_speed":    40,
        "table_speed":         40,
    },
}


class SetupDialog2AFC:

    def __init__(self):
        self.result = None
        self.root   = tk.Tk()
        self.root.title("2AFC Social Reward — Session Setup")
        self.root.resizable(False, True)

        self._species_var = tk.StringVar(value="rat")
        self._vars        = {}
        self._timing_vars = {}
        self._p3b_threshold_vars = []
        self._p3b_hold_vars      = []

        self._sm_frame   = None
        self._p4_frame   = None
        self._p3b_frame  = None
        self._task_frame = None

        self._build_ui()
        self._on_species_change()
        self._on_phase_change()
        self._apply_saved_settings()
        fit_window_to_screen(self._scroll_body)

    # ── Settings persistence ──────────────────────────────────────────────────

    def _save_settings(self):
        s = {"species": self._species_var.get(),
             "record_camera": self._camera.record,
             "camera_serial": self._camera.serial}
        for k, v in self._vars.items():
            s[k] = v.get()
        for k, v in self._timing_vars.items():
            s[f"t_{k}"] = v.get()
        for i, v in enumerate(self._p3b_threshold_vars):
            s[f"p3b_thr_{i}"] = v.get()
        for i, v in enumerate(self._p3b_hold_vars):
            s[f"p3b_hold_{i}"] = v.get()
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
        if "record_camera" in s:
            self._camera.record = s["record_camera"]
        if "camera_serial" in s:
            self._camera.serial = s["camera_serial"]
        for k, v in self._vars.items():
            if k in s:
                v.set(s[k])
        for k, v in self._timing_vars.items():
            if f"t_{k}" in s:
                v.set(s[f"t_{k}"])
        for i, v in enumerate(self._p3b_threshold_vars):
            if f"p3b_thr_{i}" in s:
                v.set(s[f"p3b_thr_{i}"])
        for i, v in enumerate(self._p3b_hold_vars):
            if f"p3b_hold_{i}" in s:
                v.set(s[f"p3b_hold_{i}"])

    # ── Layout helpers ────────────────────────────────────────────────────────

    def _row(self, parent, label_text, var, row, col=0, width=18):
        pad = {"padx": 8, "pady": 2}
        tk.Label(parent, text=label_text, anchor="w").grid(
            row=row, column=col, sticky="w", **pad)
        tk.Entry(parent, textvariable=var, width=width).grid(
            row=row, column=col + 1, sticky="w", **pad)

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = make_scrollable(self.root)
        self._scroll_body = root
        pad  = {"padx": 8, "pady": 3}

        tk.Label(root, text="2AFC Social Reward — Session Setup",
                 font=("Arial", 13, "bold")).grid(
            row=0, column=0, columnspan=4, pady=(12, 4))

        # Species
        tk.Label(root, text="Species:", anchor="w").grid(
            row=1, column=0, sticky="w", **pad)
        sf = tk.Frame(root)
        sf.grid(row=1, column=1, columnspan=3, sticky="w", **pad)
        for sp in ("Rat", "Mouse"):
            tk.Radiobutton(sf, text=sp, variable=self._species_var,
                           value=sp.lower(),
                           command=self._on_species_change).pack(side="left", padx=4)

        ttk.Separator(root, orient="horizontal").grid(
            row=2, column=0, columnspan=4, sticky="ew", padx=8, pady=4)

        # Core fields
        for i, (label, key, default) in enumerate([
            ("Animal ID:",    "animal",    ""),
            ("Session #:",    "session_n", "1"),
            ("Serial Port:",  "port",      "COM3"),
            ("Baud Rate:",    "baud",      "115200"),
        ]):
            v = tk.StringVar(value=default)
            self._vars[key] = v
            self._row(root, label, v, row=3 + i)

        # Save folder (with browse button) — session data (CSVs, metadata,
        # camera recording) is saved under <save_root>/<animal>_..._<date>_<species>/
        default_save_root = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "SocialReward2AFCData")
        self._vars["save_root"] = tk.StringVar(value=default_save_root)
        tk.Label(root, text="Save Folder:", anchor="w").grid(
            row=7, column=0, sticky="w", **pad)
        tk.Entry(root, textvariable=self._vars["save_root"], width=20).grid(
            row=7, column=1, sticky="w", **pad)
        tk.Button(root, text="Browse...", command=self._browse_save_root).grid(
            row=7, column=2, sticky="w", **pad)

        # Phase
        tk.Label(root, text="Phase:", anchor="w").grid(
            row=8, column=0, sticky="w", **pad)
        self._vars["phase"] = tk.StringVar(value="1")
        phase_cb = ttk.Combobox(
            root, textvariable=self._vars["phase"],
            values=["1", "2", "3", "3b", "4", "forced", "mixed", "free"],
            state="readonly", width=12)
        phase_cb.grid(row=8, column=1, sticky="w", **pad)
        phase_cb.bind("<<ComboboxSelected>>", lambda _e: self._on_phase_change())

        ttk.Separator(root, orient="horizontal").grid(
            row=9, column=0, columnspan=4, sticky="ew", padx=8, pady=4)

        # Timing (always visible)
        tf = tk.LabelFrame(root, text="Timing", padx=8, pady=4)
        tf.grid(row=10, column=0, columnspan=4, sticky="ew", padx=8, pady=4)

        for key in ("valve_time", "iti_min", "iti_max",
                    "session_duration_s", "session_duration_t"):
            self._timing_vars[key] = tk.StringVar()

        self._row(tf, "Valve time (s):", self._timing_vars["valve_time"], row=0)
        self._row(tf, "ITI min (s):",    self._timing_vars["iti_min"],    row=1)
        self._row(tf, "ITI max (s):",    self._timing_vars["iti_max"],    row=2)

        tk.Label(tf, text="Session duration:", anchor="w").grid(
            row=3, column=0, sticky="w", padx=8, pady=2)
        dur_f = tk.Frame(tf)
        dur_f.grid(row=3, column=1, sticky="w", pady=2)
        tk.Entry(dur_f, textvariable=self._timing_vars["session_duration_s"],
                 width=7).pack(side="left")
        tk.Label(dur_f, text=" s  OR ").pack(side="left")
        tk.Entry(dur_f, textvariable=self._timing_vars["session_duration_t"],
                 width=5).pack(side="left")
        tk.Label(dur_f, text=" trials").pack(side="left")

        # Motor speeds (0-255)
        for i, (label, key) in enumerate([
            ("Door Open Speed (0-255):",  "door_open_speed"),
            ("Door Close Speed (0-255):", "door_close_speed"),
            ("Turntable Speed (0-255):",  "table_speed"),
        ]):
            self._timing_vars[key] = tk.StringVar()
            self._row(tf, label, self._timing_vars[key], row=4 + i)
        tk.Label(tf,
                 text="(blank or 'off' = leave unset — for firmware without speed control)",
                 font=("Arial", 8), fg="gray").grid(
            row=7, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 2))

        # Phase-specific frames (overlapping in same grid row)
        # Phase 2 / 3a — sensory minimum
        self._sm_frame = tk.LabelFrame(root, text="Phase Parameters", padx=8, pady=4)
        self._sm_frame.grid(row=11, column=0, columnspan=4, sticky="ew", padx=8, pady=4)
        self._timing_vars["sensory_minimum"] = tk.StringVar()
        self._row(self._sm_frame, "Sensory minimum (s):",
                  self._timing_vars["sensory_minimum"], row=0)

        # Phase 4
        self._p4_frame = tk.LabelFrame(root, text="Phase Parameters", padx=8, pady=4)
        self._p4_frame.grid(row=11, column=0, columnspan=4, sticky="ew", padx=8, pady=4)
        self._timing_vars["phase4_sensory_min"]  = tk.StringVar()
        self._timing_vars["phase4_decision_win"] = tk.StringVar()
        self._row(self._p4_frame, "Sensory minimum (s):",
                  self._timing_vars["phase4_sensory_min"], row=0)
        self._row(self._p4_frame, "Decision window (s):",
                  self._timing_vars["phase4_decision_win"], row=1)

        # Phase 3b
        self._p3b_frame = tk.LabelFrame(
            root, text="Phase 3b — Gradual Sensory Min", padx=8, pady=4)
        self._p3b_frame.grid(row=11, column=0, columnspan=4,
                             sticky="ew", padx=8, pady=4)
        tk.Label(self._p3b_frame, text="Trial threshold (<)").grid(
            row=0, column=0, columnspan=2, sticky="w", padx=4)
        tk.Label(self._p3b_frame, text="Hold (s)").grid(
            row=0, column=2, sticky="w", padx=4)
        self._p3b_threshold_vars = [tk.StringVar() for _ in range(3)]
        self._p3b_hold_vars      = [tk.StringVar() for _ in range(4)]
        for i in range(3):
            tk.Label(self._p3b_frame, text=f"Step {i+1}:").grid(
                row=1+i, column=0, sticky="e", padx=4, pady=2)
            tk.Entry(self._p3b_frame,
                     textvariable=self._p3b_threshold_vars[i], width=6).grid(
                row=1+i, column=1, sticky="w", padx=4, pady=2)
            tk.Entry(self._p3b_frame,
                     textvariable=self._p3b_hold_vars[i], width=6).grid(
                row=1+i, column=2, sticky="w", padx=4, pady=2)
        tk.Label(self._p3b_frame, text="Final (≥ last threshold):").grid(
            row=4, column=0, columnspan=2, sticky="e", padx=4, pady=2)
        tk.Entry(self._p3b_frame,
                 textvariable=self._p3b_hold_vars[3], width=6).grid(
            row=4, column=2, sticky="w", padx=4, pady=2)

        # Task phases (forced / mixed / free)
        self._task_frame = tk.LabelFrame(root, text="Task Parameters", padx=8, pady=4)
        self._task_frame.grid(row=11, column=0, columnspan=4,
                              sticky="ew", padx=8, pady=4)
        for key in ("task_sensory_min", "task_decision_win",
                    "angle_a", "angle_b", "mixed_start_ratio"):
            self._timing_vars[key] = tk.StringVar()
        for i, (label, key) in enumerate([
            ("Sensory minimum (s):",    "task_sensory_min"),
            ("Decision window (s):",    "task_decision_win"),
            ("Port A stimulus angle °:","angle_a"),
            ("Port B stimulus angle °:","angle_b"),
            ("Mixed start forced ratio:","mixed_start_ratio"),
        ]):
            self._row(self._task_frame, label, self._timing_vars[key], row=i)
        tk.Label(self._task_frame,
                 text="(mixed_start_ratio only applies to Mixed phase)",
                 font=("Arial", 8), fg="gray").grid(
            row=5, column=0, columnspan=4, sticky="w", padx=8)

        # Camera — chosen by serial number so a second session running in
        # another terminal can record the other camera at the same time.
        cam_frame = tk.LabelFrame(root, text="Camera", padx=8, pady=4)
        cam_frame.grid(row=12, column=0, columnspan=4, sticky="ew", padx=8, pady=4)
        self._camera = CameraChooser(cam_frame)
        self._camera.grid(row=0, column=0, sticky="w")

        # Notes + buttons
        ttk.Separator(root, orient="horizontal").grid(
            row=13, column=0, columnspan=4, sticky="ew", padx=8, pady=4)
        tk.Label(root, text="Notes:", anchor="w").grid(
            row=14, column=0, sticky="nw", **pad)
        self._notes = tk.Text(root, width=38, height=3, font=("Arial", 9))
        self._notes.grid(row=14, column=1, columnspan=3, sticky="ew", **pad)

        bf = tk.Frame(root)
        bf.grid(row=15, column=0, columnspan=4, pady=(8, 14))
        tk.Button(bf, text="Start Session", bg="#4CAF50", fg="white",
                  font=("Arial", 11, "bold"), width=18,
                  command=self._on_start).pack(side="left", padx=8)
        tk.Button(bf, text="Cancel", width=10,
                  command=self.root.destroy).pack(side="left", padx=8)

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _on_species_change(self):
        sp = self._species_var.get()
        d  = SPECIES_DEFAULTS[sp]
        for key in ("valve_time", "iti_min", "iti_max",
                    "session_duration_s", "session_duration_t",
                    "sensory_minimum", "phase4_sensory_min",
                    "phase4_decision_win", "task_sensory_min",
                    "task_decision_win", "angle_a", "angle_b",
                    "mixed_start_ratio",
                    "door_open_speed", "door_close_speed", "table_speed"):
            self._timing_vars[key].set(str(d.get(key, "")))

        thresholds = d["phase3b_thresholds"]
        holds      = d["phase3b_holds"]
        for i in range(3):
            self._p3b_threshold_vars[i].set(
                str(thresholds[i]) if i < len(thresholds) else "")
        for i in range(4):
            self._p3b_hold_vars[i].set(
                str(holds[i]) if i < len(holds) else "")

    def _browse_save_root(self):
        chosen = filedialog.askdirectory(
            initialdir=self._vars["save_root"].get() or os.getcwd(),
            title="Choose folder to save session data into",
        )
        if chosen:
            self._vars["save_root"].set(chosen)

    def _on_phase_change(self):
        phase = self._vars["phase"].get()
        for frame in (self._sm_frame, self._p4_frame,
                      self._p3b_frame, self._task_frame):
            frame.grid_remove()
        if phase in ("2", "3"):
            self._sm_frame.grid()
        elif phase == "4":
            self._p4_frame.grid()
        elif phase == "3b":
            self._p3b_frame.grid()
        elif phase in ("forced", "mixed", "free"):
            self._task_frame.grid()
        self.root.update_idletasks()

    # ── Validate + collect ────────────────────────────────────────────────────

    def _on_start(self):
        errors = []
        phase  = self._vars["phase"].get()

        animal    = self._vars["animal"].get().strip() or "unknown"
        session_n = self._vars["session_n"].get().strip() or "1"
        port      = self._vars["port"].get().strip()
        if not port:
            errors.append("Serial port is required.")

        save_root = self._vars["save_root"].get().strip()
        if not save_root:
            errors.append("Save folder is required.")

        try:
            baud = int(self._vars["baud"].get())
        except ValueError:
            errors.append("Baud rate must be an integer.")
            baud = None

        try:
            valve_time = float(self._timing_vars["valve_time"].get())
            iti_min    = float(self._timing_vars["iti_min"].get())
            iti_max    = float(self._timing_vars["iti_max"].get())
        except ValueError:
            errors.append("Valve time and ITI must be numbers.")
            valve_time = iti_min = iti_max = None

        try:
            door_open_speed = parse_motor_speed(self._timing_vars["door_open_speed"].get())
            door_close_speed = parse_motor_speed(self._timing_vars["door_close_speed"].get())
            table_speed = parse_motor_speed(self._timing_vars["table_speed"].get())
        except ValueError:
            errors.append("Motor speeds must be integers (0-255), or blank/'off' to leave unset.")
            door_open_speed = door_close_speed = table_speed = None

        # Session duration
        dur_s_str = self._timing_vars["session_duration_s"].get().strip()
        dur_t_str = self._timing_vars["session_duration_t"].get().strip()
        session_duration_s = session_duration_t = None
        if not dur_s_str and not dur_t_str:
            errors.append("Set at least one session duration.")
        else:
            if dur_s_str:
                try:
                    session_duration_s = int(dur_s_str)
                except ValueError:
                    errors.append("Session duration (s) must be an integer.")
            if dur_t_str:
                try:
                    session_duration_t = int(dur_t_str)
                except ValueError:
                    errors.append("Session duration (trials) must be an integer.")

        # Phase-specific
        sensory_minimum = phase4_sensory_min = phase4_decision_win = None
        task_sensory_min = task_decision_win = None
        angle_a = angle_b = mixed_start_ratio = None
        thresholds, holds = [], []

        if phase in ("2", "3"):
            try:
                sensory_minimum = float(self._timing_vars["sensory_minimum"].get())
            except ValueError:
                errors.append("Sensory minimum must be a number.")

        elif phase == "4":
            try:
                phase4_sensory_min  = float(self._timing_vars["phase4_sensory_min"].get())
                phase4_decision_win = float(self._timing_vars["phase4_decision_win"].get())
            except ValueError:
                errors.append("Phase 4 sensory min and decision window must be numbers.")

        elif phase == "3b":
            try:
                for i in range(3):
                    t = self._p3b_threshold_vars[i].get().strip()
                    h = self._p3b_hold_vars[i].get().strip()
                    if t and h:
                        thresholds.append(int(t))
                        holds.append(float(h))
                    elif t or h:
                        errors.append(
                            f"Phase 3b step {i+1}: fill both or leave both blank.")
                final_h = self._p3b_hold_vars[3].get().strip()
                if final_h:
                    holds.append(float(final_h))
                else:
                    errors.append("Phase 3b final hold time is required.")
            except ValueError:
                errors.append("Phase 3b values must be numbers.")

        elif phase in ("forced", "mixed", "free"):
            try:
                task_sensory_min  = float(self._timing_vars["task_sensory_min"].get())
                task_decision_win = float(self._timing_vars["task_decision_win"].get())
                angle_a           = int(self._timing_vars["angle_a"].get())
                angle_b           = int(self._timing_vars["angle_b"].get())
                if phase == "mixed":
                    mixed_start_ratio = float(
                        self._timing_vars["mixed_start_ratio"].get())
            except ValueError:
                errors.append("Task parameters must be numbers.")

        if errors:
            messagebox.showerror("Input Error", "\n".join(errors))
            return

        self.result = {
            "species":           self._species_var.get(),
            "animal":            animal,
            "session_n":         session_n,
            "phase":             phase,
            "save_root":         save_root,
            "port":              port,
            "baud":              baud,
            "valve_time":        valve_time,
            "door_open_speed":   door_open_speed,
            "door_close_speed":  door_close_speed,
            "table_speed":       table_speed,
            "iti_min":           iti_min,
            "iti_max":           iti_max,
            "session_duration_s":session_duration_s,
            "session_duration_t":session_duration_t,
            "sensory_minimum":   sensory_minimum,
            "phase4_sensory_min":phase4_sensory_min,
            "phase4_decision_win":phase4_decision_win,
            "task_sensory_min":  task_sensory_min,
            "task_decision_win": task_decision_win,
            "angle_a":           angle_a,
            "angle_b":           angle_b,
            "mixed_start_ratio": mixed_start_ratio,
            "phase3b_thresholds":thresholds,
            "phase3b_holds":     holds,
            "record_camera":     self._camera.record,
            "camera_serial":     self._camera.serial,
            "notes":             self._notes.get("1.0", "end").strip(),
        }
        self._save_settings()
        self.root.destroy()

    def run(self):
        self.root.mainloop()
        return self.result
