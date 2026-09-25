# SocialChoice/setup_dialog.py — Pre-session configuration dialog for Social Choice
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
        "valve_time":         0.30,
        "session_duration_s": 3600,
        "session_duration_t": "",
        "iti_min":            5.0,
        "iti_max":            10.0,
        "decision_window":    5.0,
        "social_angle":       90,
        "social_duration":    10.0,
        "door_open_speed":    255,
        "door_close_speed":   40,
        "table_speed":        40,
    },
    "mouse": {
        "valve_time":         0.20,
        "session_duration_s": 3600,
        "session_duration_t": "",
        "iti_min":            5.0,
        "iti_max":            10.0,
        "decision_window":    5.0,
        "social_angle":       90,
        "social_duration":    10.0,
        "door_open_speed":    255,
        "door_close_speed":   40,
        "table_speed":        40,
    },
}


class SCSetupDialog:

    def __init__(self):
        self.result = None
        self.root   = tk.Tk()
        self.root.title("Social Choice — Session Setup")
        self.root.resizable(False, True)

        self._species_var = tk.StringVar(value="rat")
        self._vars        = {}
        self._timing_vars = {}

        self._onechoice_frame  = None
        self._twochoice_frame  = None

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

        tk.Label(root, text="Social Choice — Session Setup",
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
            ("Animal ID:",   "animal",    ""),
            ("Session #:",   "session_n", "1"),
            ("Serial Port:", "port",      "COM3"),
            ("Baud Rate:",   "baud",      "115200"),
        ]):
            v = tk.StringVar(value=default)
            self._vars[key] = v
            self._row(root, label, v, row=3 + i)

        # Phase
        tk.Label(root, text="Phase:", anchor="w").grid(
            row=7, column=0, sticky="w", **pad)
        self._vars["phase"] = tk.StringVar(value="learning")
        phase_cb = ttk.Combobox(
            root, textvariable=self._vars["phase"],
            values=["learning", "one_choice", "two_choice"],
            state="readonly", width=14)
        phase_cb.grid(row=7, column=1, sticky="w", **pad)
        phase_cb.bind("<<ComboboxSelected>>", lambda _e: self._on_phase_change())

        ttk.Separator(root, orient="horizontal").grid(
            row=8, column=0, columnspan=4, sticky="ew", padx=8, pady=4)

        # Timing (always visible)
        tf = tk.LabelFrame(root, text="Timing", padx=8, pady=4)
        tf.grid(row=9, column=0, columnspan=4, sticky="ew", padx=8, pady=4)

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

        # One-choice frame (decision_window only)
        self._onechoice_frame = tk.LabelFrame(
            root, text="Phase Parameters", padx=8, pady=4)
        self._onechoice_frame.grid(
            row=10, column=0, columnspan=4, sticky="ew", padx=8, pady=4)
        self._timing_vars["decision_window"] = tk.StringVar()
        self._row(self._onechoice_frame, "Decision window (s):",
                  self._timing_vars["decision_window"], row=0)

        # Two-choice frame (decision_window + social_angle + social_duration)
        self._twochoice_frame = tk.LabelFrame(
            root, text="Phase Parameters", padx=8, pady=4)
        self._twochoice_frame.grid(
            row=10, column=0, columnspan=4, sticky="ew", padx=8, pady=4)
        for key in ("decision_window_2", "social_angle", "social_duration"):
            self._timing_vars[key] = tk.StringVar()
        self._row(self._twochoice_frame, "Decision window (s):",
                  self._timing_vars["decision_window_2"], row=0)
        self._row(self._twochoice_frame, "Social angle (°):",
                  self._timing_vars["social_angle"],      row=1)
        self._row(self._twochoice_frame, "Social duration (s):",
                  self._timing_vars["social_duration"],   row=2)
        tk.Label(self._twochoice_frame,
                 text="(anti-bias forces opposite port after 10 same choices)",
                 font=("Arial", 8), fg="gray").grid(
            row=3, column=0, columnspan=4, sticky="w", padx=8)

        # Notes + buttons
        ttk.Separator(root, orient="horizontal").grid(
            row=11, column=0, columnspan=4, sticky="ew", padx=8, pady=4)
        tk.Label(root, text="Notes:", anchor="w").grid(
            row=12, column=0, sticky="nw", **pad)
        self._notes = tk.Text(root, width=38, height=3, font=("Arial", 9))
        self._notes.grid(row=12, column=1, columnspan=3, sticky="ew", **pad)

        bf = tk.Frame(root)
        bf.grid(row=13, column=0, columnspan=4, pady=(8, 14))
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
                    "decision_window", "social_angle", "social_duration",
                    "door_open_speed", "door_close_speed", "table_speed"):
            self._timing_vars[key].set(str(d.get(key, "")))
        # Mirror decision_window into the two-choice field
        self._timing_vars["decision_window_2"].set(
            str(d.get("decision_window", "")))

    def _on_phase_change(self):
        phase = self._vars["phase"].get()
        self._onechoice_frame.grid_remove()
        self._twochoice_frame.grid_remove()
        if phase == "one_choice":
            self._onechoice_frame.grid()
        elif phase == "two_choice":
            self._twochoice_frame.grid()
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
        decision_window = social_angle = social_duration = None

        if phase == "one_choice":
            try:
                decision_window = float(self._timing_vars["decision_window"].get())
            except ValueError:
                errors.append("Decision window must be a number.")

        elif phase == "two_choice":
            try:
                decision_window = float(self._timing_vars["decision_window_2"].get())
                social_angle    = int(self._timing_vars["social_angle"].get())
                social_duration = float(self._timing_vars["social_duration"].get())
            except ValueError:
                errors.append("Two-choice parameters must be numbers.")

        if errors:
            messagebox.showerror("Input Error", "\n".join(errors))
            return

        self.result = {
            "species":           self._species_var.get(),
            "animal":            animal,
            "session_n":         session_n,
            "phase":             phase,
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
            "decision_window":   decision_window,
            "social_angle":      social_angle,
            "social_duration":   social_duration,
            "notes":             self._notes.get("1.0", "end").strip(),
        }
        self._save_settings()
        self.root.destroy()

    def run(self):
        self.root.mainloop()
        return self.result
