# SocialMemory/setup_dialog.py — Pre-session configuration dialog for Social Memory tasks
#
# Mode: training    → ClassicalConditioningSession (AutoRewardSession if auto_reward)
#       task        → SocialMemoryTaskSession
#       passivetest → PassiveTestSession
#
# Returns a dict of validated parameters on Start, or None on Cancel.

import json
import os
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from utils import parse_motor_speed
from tk_window_helpers import make_scrollable, fit_window_to_screen
from camera_select import CameraChooser
from rig_setups import SETUPS, SETUP_CAMERAS

_SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "last_settings.json")

# How many recent values each free-text field (animal ID, COM port, stim IDs...)
# offers in its dropdown. Kept in last_settings.json under "recent".
HISTORY_LEN = 4

SPECIES_DEFAULTS = {
    "rat": {
        "valve_time_A":     0.30,
        "valve_time_B":     0.30,
        "valve_time_C":     0.30,
        "session_duration": 3600,
        # Training CC
        "cc_led_on_time":   10.0,
        "cc_iti_min":        8.0,
        "cc_iti_max":       15.0,
        "cc_reward_prob":    1.0,
        "cc_delay":          0.0,
        # Task S1
        "s1_n":              4,
        "s1_duration":      300.0,
        "s1_box":             1,
        "s1_iti_min":       600.0,
        "s1_iti_max":       600.0,
        # Task S2
        "s2_n":              1,
        "s2_duration":      300.0,
        "s2_box":             3,
        "s2_iti_min":       600.0,
        "s2_iti_max":       600.0,
        # Passive test
        "passive_box_n":      3,
        "passive_duration": 300.0,
        "passive_iti_min":  600.0,
        "passive_iti_max":  600.0,
        "door_open_speed":  255,
        "door_close_speed": 40,
        "table_speed":      40,
    },
    "mouse": {
        "valve_time_A":     0.20,
        "valve_time_B":     0.20,
        "valve_time_C":     0.20,
        "session_duration": 3600,
        "cc_led_on_time":   10.0,
        "cc_iti_min":        8.0,
        "cc_iti_max":       15.0,
        "cc_reward_prob":    1.0,
        "cc_delay":          0.0,
        "s1_n":              4,
        "s1_duration":      180.0,
        "s1_box":             1,
        "s1_iti_min":       300.0,
        "s1_iti_max":       300.0,
        "s2_n":              1,
        "s2_duration":      180.0,
        "s2_box":             3,
        "s2_iti_min":       300.0,
        "s2_iti_max":       300.0,
        # Passive test
        "passive_box_n":      3,
        "passive_duration": 180.0,
        "passive_iti_min":  300.0,
        "passive_iti_max":  300.0,
        "door_open_speed":  255,
        "door_close_speed": 40,
        "table_speed":      40,
    },
}


class SMSetupDialog:

    def __init__(self):
        self.result = None
        self.root = tk.Tk()
        self.root.title("Social Memory — Session Setup")
        self.root.resizable(True, True)

        self._species_var = tk.StringVar(value="rat")
        self._mode_var    = tk.StringVar(value="training")
        self._vars        = {}          # core text fields
        self._port_vars   = {}          # port checkboxes
        self._cc_vars     = {}          # CC parameters
        self._task_vars   = {}          # task-specific parameters
        self._passive_vars = {}         # passive-test-specific parameters
        self._speed_vars  = {}          # motor speed parameters
        self._setup_var   = tk.StringVar(value="")
        self._treatment_var = tk.BooleanVar(value=False)
        self._sensor_display_var = tk.BooleanVar(value=True)
        self._history        = {}       # history key -> recent values, newest first
        self._history_combos = {}       # history key -> (Combobox, StringVar)

        self._training_frame = None
        self._task_frame     = None
        self._passive_frame  = None

        self._build_ui()
        self._on_species_change()
        self._on_mode_change()
        self._apply_saved_settings()
        fit_window_to_screen(self._scroll_body)

    # ── Settings persistence ──────────────────────────────────────────────────

    def _save_settings(self):
        s = {
            "species": self._species_var.get(),
            "mode":    self._mode_var.get(),
            "auto_reward": self._auto_reward_var.get(),
            "record_camera": self._camera.record,
            "camera_serial": self._camera.serial,
            "setup": self._setup_var.get(),
            "show_sensor_display": self._sensor_display_var.get(),
            "recent": self._history,
        }
        for k, v in self._vars.items():
            s[k] = v.get()
        for k, v in self._cc_vars.items():
            s[f"cc_{k}"] = v.get()
        for k, v in self._task_vars.items():
            s[f"task_{k}"] = v.get()
        for k, v in self._passive_vars.items():
            s[f"passive_{k}"] = v.get()
        for k, v in self._speed_vars.items():
            s[f"speed_{k}"] = v.get()
        for port, v in self._port_vars.items():
            s[f"port_{port}"] = v.get()
        for port, v in self._task_port_vars.items():
            s[f"task_port_{port}"] = v.get()
        for port, v in self._passive_port_vars.items():
            s[f"passive_port_{port}"] = v.get()
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
        if "mode" in s:
            self._mode_var.set(s["mode"])
            self._on_mode_change()
        if "auto_reward" in s:
            self._auto_reward_var.set(s["auto_reward"])
        if "record_camera" in s:
            self._camera.record = s["record_camera"]
        if "camera_serial" in s:
            self._camera.serial = s["camera_serial"]
        # After camera_serial, so a setup with a linked camera wins over a
        # stale saved serial.
        if s.get("setup") in SETUPS:
            self._setup_var.set(s["setup"])
            self._on_setup_change()
        if "show_sensor_display" in s:
            self._sensor_display_var.set(s["show_sensor_display"])
        # The treatment checkbox itself is deliberately not restored — it is
        # per-session, and a stale tick would mislabel the next animal.
        recent = s.get("recent")
        if isinstance(recent, dict):
            self._history = {k: [str(v) for v in vals][:HISTORY_LEN]
                             for k, vals in recent.items() if isinstance(vals, list)}
        for key, (combo, _var) in self._history_combos.items():
            combo["values"] = self._history.get(key, [])
        for k, v in self._vars.items():
            if k in s:
                v.set(s[k])
        for k, v in self._cc_vars.items():
            if f"cc_{k}" in s:
                v.set(s[f"cc_{k}"])
        for k, v in self._task_vars.items():
            if f"task_{k}" in s:
                v.set(s[f"task_{k}"])
        for k, v in self._passive_vars.items():
            if f"passive_{k}" in s:
                v.set(s[f"passive_{k}"])
        for k, v in self._speed_vars.items():
            if f"speed_{k}" in s:
                v.set(s[f"speed_{k}"])
        for port, v in self._port_vars.items():
            if f"port_{port}" in s:
                v.set(s[f"port_{port}"])
        for port, v in self._task_port_vars.items():
            if f"task_port_{port}" in s:
                v.set(s[f"task_port_{port}"])
        for port, v in self._passive_port_vars.items():
            if f"passive_port_{port}" in s:
                v.set(s[f"passive_port_{port}"])

    def _update_history(self):
        """Move each history field's current value to the front of its list."""
        for key, (_combo, var) in self._history_combos.items():
            val = var.get().strip()
            items = [v for v in self._history.get(key, []) if v != val]
            if val:
                items.insert(0, val)
            self._history[key] = items[:HISTORY_LEN]

    # ── Layout helpers ────────────────────────────────────────────────────────

    def _entry(self, parent, var, width, history_key=None):
        """A text entry, or — given a history_key — an editable dropdown of the
        field's HISTORY_LEN most recent values (filled in on restore)."""
        if history_key is None:
            return tk.Entry(parent, textvariable=var, width=width)
        combo = ttk.Combobox(parent, textvariable=var, width=width)
        self._history_combos[history_key] = (combo, var)
        return combo

    def _row(self, parent, label_text, var, row, col=0, width=14, history_key=None):
        pad = {"padx": 6, "pady": 2}
        tk.Label(parent, text=label_text, anchor="w").grid(
            row=row, column=col, sticky="w", **pad)
        self._entry(parent, var, width, history_key).grid(
            row=row, column=col + 1, sticky="w", **pad)

    def _make_var(self, key, default=""):
        v = tk.StringVar(value=str(default))
        return v

    # ── Build UI ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = make_scrollable(self.root)
        self._scroll_body = root
        pad = {"padx": 8, "pady": 3}

        tk.Label(root, text="Social Memory — Session Setup",
                 font=("Arial", 13, "bold")).grid(
            row=0, column=0, columnspan=4, pady=(12, 4))

        # Setup — which physical rig this session runs on. Also selects that
        # rig's camera (see rig_setups.SETUP_CAMERAS).
        tk.Label(root, text="Setup:", anchor="w").grid(
            row=1, column=0, sticky="w", **pad)
        setup_combo = ttk.Combobox(root, textvariable=self._setup_var,
                                   values=SETUPS, state="readonly", width=17)
        setup_combo.grid(row=1, column=1, sticky="w", **pad)
        setup_combo.bind("<<ComboboxSelected>>", self._on_setup_change)
        self._setup_camera_label = tk.Label(root, text="", font=("Arial", 8), fg="gray")
        self._setup_camera_label.grid(row=1, column=2, columnspan=2, sticky="w", **pad)

        # Species
        tk.Label(root, text="Species:", anchor="w").grid(
            row=2, column=0, sticky="w", **pad)
        sf = tk.Frame(root)
        sf.grid(row=2, column=1, columnspan=3, sticky="w", **pad)
        for sp in ("Rat", "Mouse"):
            tk.Radiobutton(sf, text=sp, variable=self._species_var,
                           value=sp.lower(),
                           command=self._on_species_change).pack(side="left", padx=4)

        ttk.Separator(root, orient="horizontal").grid(
            row=3, column=0, columnspan=4, sticky="ew", padx=8, pady=4)

        # Core fields (animal ID and port offer their recent values)
        for i, (label, key, default, history_key) in enumerate([
            ("Animal ID:",   "animal",    "",       "animal"),
            ("Session #:",   "session_n", "1",      None),
            ("Serial Port:", "port",      "COM3",   "port"),
            ("Baud Rate:",   "baud",      "115200", None),
        ]):
            v = tk.StringVar(value=default)
            self._vars[key] = v
            self._row(root, label, v, row=4 + i, width=20, history_key=history_key)

        # Save folder (with browse button) — session data (CSVs, metadata,
        # camera recording) is saved under <save_root>/<animal>_..._<datetime>/
        default_save_root = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "SocialMemoryData")
        self._vars["save_root"] = tk.StringVar(value=default_save_root)
        tk.Label(root, text="Save Folder:", anchor="w").grid(
            row=8, column=0, sticky="w", **pad)
        self._entry(root, self._vars["save_root"], 20, history_key="save_root").grid(
            row=8, column=1, sticky="w", **pad)
        tk.Button(root, text="Browse...", command=self._browse_save_root).grid(
            row=8, column=2, sticky="w", **pad)

        # Treatment — tick if the animal had one; describe it in the box.
        tf = tk.Frame(root)
        tf.grid(row=9, column=0, columnspan=4, sticky="w", **pad)
        tk.Checkbutton(tf, text="Treatment:", variable=self._treatment_var,
                       command=self._on_treatment_toggle).pack(side="left")
        self._treatment_detail_var = tk.StringVar()
        self._treatment_entry = self._entry(
            tf, self._treatment_detail_var, 30, history_key="treatment")
        self._treatment_entry.pack(side="left", padx=4)
        self._on_treatment_toggle()

        ttk.Separator(root, orient="horizontal").grid(
            row=10, column=0, columnspan=4, sticky="ew", padx=8, pady=4)

        # Mode
        tk.Label(root, text="Mode:", anchor="w").grid(
            row=11, column=0, sticky="w", **pad)
        mf = tk.Frame(root)
        mf.grid(row=11, column=1, columnspan=3, sticky="w", **pad)
        for label, value in (("Training", "training"), ("Task", "task"),
                              ("Passive Test", "passivetest")):
            tk.Radiobutton(mf, text=label, variable=self._mode_var,
                           value=value,
                           command=self._on_mode_change).pack(side="left", padx=4)

        ttk.Separator(root, orient="horizontal").grid(
            row=12, column=0, columnspan=4, sticky="ew", padx=8, pady=4)

        # ── Motor speeds (always visible) ─────────────────────────────────────
        speed_frame = tk.LabelFrame(root, text="Motor Speeds (0-255)", padx=8, pady=6)
        speed_frame.grid(row=13, column=0, columnspan=4, sticky="ew", padx=8, pady=4)
        for i, (label, key) in enumerate([
            ("Door Open Speed:",  "door_open_speed"),
            ("Door Close Speed:", "door_close_speed"),
            ("Turntable Speed:",  "table_speed"),
        ]):
            var = tk.StringVar()
            self._speed_vars[key] = var
            self._row(speed_frame, label, var, row=i, width=10)
        tk.Label(speed_frame,
                 text="(blank or 'off' = leave unset — for firmware without speed control)",
                 font=("Arial", 8), fg="gray").grid(
            row=3, column=0, columnspan=4, sticky="w", padx=6, pady=(0, 2))

        # ── Camera (optional, applies to all modes) ───────────────────────────
        # The camera is chosen by serial number so a second session running in
        # another terminal can record the other camera at the same time.
        cam_frame = tk.LabelFrame(root, text="Camera", padx=8, pady=6)
        cam_frame.grid(row=14, column=0, columnspan=4, sticky="ew", padx=8, pady=4)
        self._camera = CameraChooser(cam_frame)
        self._camera.grid(row=0, column=0, sticky="w")

        # ── Live display ──────────────────────────────────────────────────────
        disp_frame = tk.LabelFrame(root, text="Live Display", padx=8, pady=6)
        disp_frame.grid(row=15, column=0, columnspan=4, sticky="ew", padx=8, pady=4)
        tk.Checkbutton(disp_frame, text="Show live sensor display",
                       variable=self._sensor_display_var).grid(
            row=0, column=0, sticky="w", padx=6)
        tk.Label(disp_frame,
                 text="(the small port/door/table window; turn off to save CPU — "
                      "all sensor events are still logged)",
                 font=("Arial", 8), fg="gray").grid(
            row=1, column=0, sticky="w", padx=6)

        # ── Training frame ────────────────────────────────────────────────────
        self._training_frame = tk.LabelFrame(
            root, text="Training Parameters", padx=8, pady=6)
        self._training_frame.grid(
            row=16, column=0, columnspan=4, sticky="ew", padx=8, pady=4)

        # Port selection checkboxes
        tk.Label(self._training_frame, text="Ports:", anchor="w").grid(
            row=0, column=0, sticky="w", padx=6, pady=2)
        pf = tk.Frame(self._training_frame)
        pf.grid(row=0, column=1, columnspan=3, sticky="w")
        for port in ("A", "B", "C"):
            v = tk.BooleanVar(value=(port != "C"))   # A and B checked by default
            self._port_vars[port] = v
            tk.Checkbutton(pf, text=f"Port {port}", variable=v).pack(
                side="left", padx=4)

        # CC params (reused for both training and task CC section)
        self._cc_vars["led_on_time"]   = tk.StringVar()
        self._cc_vars["iti_min"]       = tk.StringVar()
        self._cc_vars["iti_max"]       = tk.StringVar()
        self._cc_vars["reward_prob"]   = tk.StringVar()
        self._cc_vars["valve_time_A"]  = tk.StringVar()
        self._cc_vars["valve_time_B"]  = tk.StringVar()
        self._cc_vars["valve_time_C"]  = tk.StringVar()
        self._cc_vars["session_dur"]   = tk.StringVar()

        for i, (label, key) in enumerate([
            ("LED on time (s):",      "led_on_time"),
            ("ITI min (s):",          "iti_min"),
            ("ITI max (s):",          "iti_max"),
            ("Reward probability:",   "reward_prob"),
            ("Valve time A (s):",     "valve_time_A"),
            ("Valve time B (s):",     "valve_time_B"),
            ("Valve time C (s):",     "valve_time_C"),
            ("Session duration (s):", "session_dur"),
        ]):
            self._row(self._training_frame, label,
                      self._cc_vars[key], row=1 + i)

        tk.Label(self._training_frame,
                 text="(leave duration blank for Ctrl+C stop)",
                 font=("Arial", 8), fg="gray").grid(
            row=9, column=0, columnspan=4, sticky="w", padx=6)

        # No-LED auto-reward option: LEDs stay off and a reward is delivered
        # whenever the animal pokes any selected port (LED on time is ignored).
        self._auto_reward_var = tk.BooleanVar(value=False)
        tk.Checkbutton(self._training_frame,
                       text="No LED — auto-reward any poked port",
                       variable=self._auto_reward_var).grid(
            row=10, column=0, columnspan=4, sticky="w", padx=6, pady=(6, 0))
        tk.Label(self._training_frame,
                 text="(LEDs stay off; a reward is delivered whenever the animal "
                      "pokes any selected port. 'LED on time' is ignored.)",
                 font=("Arial", 8), fg="gray").grid(
            row=11, column=0, columnspan=4, sticky="w", padx=6)

        # ── Task frame ────────────────────────────────────────────────────────
        self._task_frame = tk.LabelFrame(
            root, text="Task Parameters", padx=8, pady=6)
        self._task_frame.grid(
            row=16, column=0, columnspan=4, sticky="ew", padx=8, pady=4)

        # S1 sub-frame
        s1f = tk.LabelFrame(self._task_frame, text="S1 (familiar stimulus)",
                            padx=6, pady=4)
        s1f.grid(row=0, column=0, columnspan=2, sticky="ew", padx=4, pady=2)
        self._task_vars["s1_id"]       = tk.StringVar()
        self._task_vars["s1_n"]        = tk.StringVar()
        self._task_vars["s1_duration"] = tk.StringVar()
        self._task_vars["s1_box"]      = tk.StringVar()
        self._task_vars["s1_iti_min"]  = tk.StringVar()
        self._task_vars["s1_iti_max"]  = tk.StringVar()
        for i, (label, key) in enumerate([
            ("Stim ID:",          "s1_id"),
            ("# presentations:",  "s1_n"),
            ("Duration (s):",     "s1_duration"),
            ("Box (0–3):",        "s1_box"),
            ("ITI min (s):",      "s1_iti_min"),
            ("ITI max (s):",      "s1_iti_max"),
        ]):
            self._row(s1f, label, self._task_vars[key], row=i, width=10,
                      history_key="s1_id" if key == "s1_id" else None)

        # S2 sub-frame
        s2f = tk.LabelFrame(self._task_frame, text="S2 (novel stimulus)",
                            padx=6, pady=4)
        s2f.grid(row=0, column=2, columnspan=2, sticky="ew", padx=4, pady=2)
        self._task_vars["s2_id"]       = tk.StringVar()
        self._task_vars["s2_n"]        = tk.StringVar()
        self._task_vars["s2_duration"] = tk.StringVar()
        self._task_vars["s2_box"]      = tk.StringVar()
        self._task_vars["s2_iti_min"]  = tk.StringVar()
        self._task_vars["s2_iti_max"]  = tk.StringVar()
        for i, (label, key) in enumerate([
            ("Stim ID:",          "s2_id"),
            ("# presentations:",  "s2_n"),
            ("Duration (s):",     "s2_duration"),
            ("Box (0–3):",        "s2_box"),
            ("ITI min (s):",      "s2_iti_min"),
            ("ITI max (s):",      "s2_iti_max"),
        ]):
            self._row(s2f, label, self._task_vars[key], row=i, width=10,
                      history_key="s2_id" if key == "s2_id" else None)

        tk.Label(self._task_frame,
                 text="Box positions: 0 = home, 1 = 90° CW, 2 = 180°, 3 = 270° CW",
                 font=("Arial", 8), fg="gray").grid(
            row=1, column=0, columnspan=4, sticky="w", padx=8, pady=(0, 2))

        # CC sub-frame (within task)
        ccf = tk.LabelFrame(self._task_frame,
                            text="Classical Conditioning (during ITI)", padx=6, pady=4)
        ccf.grid(row=2, column=0, columnspan=4, sticky="ew", padx=4, pady=4)

        tk.Label(ccf, text="Ports:", anchor="w").grid(
            row=0, column=0, sticky="w", padx=4, pady=2)
        cpf = tk.Frame(ccf)
        cpf.grid(row=0, column=1, columnspan=3, sticky="w")
        self._task_port_vars = {}
        for port in ("A", "B", "C"):
            v = tk.BooleanVar(value=(port != "C"))
            self._task_port_vars[port] = v
            tk.Checkbutton(cpf, text=f"Port {port}", variable=v).pack(
                side="left", padx=4)

        self._task_vars["cc_led_on_time"] = tk.StringVar()
        self._task_vars["cc_iti_min"]     = tk.StringVar()
        self._task_vars["cc_iti_max"]     = tk.StringVar()
        self._task_vars["cc_reward_prob"] = tk.StringVar()
        self._task_vars["cc_delay"]       = tk.StringVar()
        self._task_vars["valve_time_A"]   = tk.StringVar()
        self._task_vars["valve_time_B"]   = tk.StringVar()
        self._task_vars["valve_time_C"]   = tk.StringVar()
        for i, (label, key) in enumerate([
            ("CC LED on time (s):",  "cc_led_on_time"),
            ("CC trial ITI min (s):","cc_iti_min"),
            ("CC trial ITI max (s):","cc_iti_max"),
            ("Reward probability:",  "cc_reward_prob"),
            ("CC delay (s):",        "cc_delay"),
            ("Valve time A (s):",    "valve_time_A"),
            ("Valve time B (s):",    "valve_time_B"),
            ("Valve time C (s):",    "valve_time_C"),
        ]):
            self._row(ccf, label, self._task_vars[key], row=1 + i, width=10)
        tk.Label(ccf,
                 text="(CC delay: idle time before conditioning starts each ITI)",
                 font=("Arial", 8), fg="gray").grid(
            row=9, column=0, columnspan=4, sticky="w", padx=4)

        # ── Passive Test frame ───────────────────────────────────────────────────
        self._passive_frame = tk.LabelFrame(
            root, text="Passive Test Parameters", padx=8, pady=6)
        self._passive_frame.grid(
            row=16, column=0, columnspan=4, sticky="ew", padx=8, pady=4)

        # Box sub-frame: label + # presentations for each of the 4 boxes
        boxf = tk.LabelFrame(self._passive_frame, text="Boxes", padx=6, pady=4)
        boxf.grid(row=0, column=0, columnspan=4, sticky="ew", padx=4, pady=2)
        tk.Label(boxf, text="Box", anchor="w").grid(row=0, column=0, padx=6, pady=2)
        tk.Label(boxf, text="Stim label", anchor="w").grid(row=0, column=1, padx=6, pady=2)
        tk.Label(boxf, text="# presentations", anchor="w").grid(row=0, column=2, padx=6, pady=2)
        for i in range(4):
            angle = i * 90
            self._passive_vars[f"box{i}_id"] = tk.StringVar()
            self._passive_vars[f"box{i}_n"]  = tk.StringVar()
            tk.Label(boxf, text=f"Box {i} ({angle}°):", anchor="w").grid(
                row=1 + i, column=0, sticky="w", padx=6, pady=2)
            self._entry(boxf, self._passive_vars[f"box{i}_id"], 16,
                        history_key=f"passive_box{i}_id").grid(
                row=1 + i, column=1, padx=6, pady=2)
            tk.Entry(boxf, textvariable=self._passive_vars[f"box{i}_n"], width=8).grid(
                row=1 + i, column=2, padx=6, pady=2)
        tk.Label(boxf,
                 text="(pseudorandom order — no two consecutive presentations use the same box)",
                 font=("Arial", 8), fg="gray").grid(
            row=5, column=0, columnspan=3, sticky="w", padx=6, pady=(2, 0))

        # Shared duration / ITI
        self._passive_vars["duration"] = tk.StringVar()
        self._passive_vars["iti_min"]  = tk.StringVar()
        self._passive_vars["iti_max"]  = tk.StringVar()
        for i, (label, key) in enumerate([
            ("Presentation duration (s):", "duration"),
            ("ITI min (s):",               "iti_min"),
            ("ITI max (s):",               "iti_max"),
        ]):
            self._row(self._passive_frame, label, self._passive_vars[key], row=1 + i, width=10)

        # CC sub-frame (within passive test)
        pccf = tk.LabelFrame(self._passive_frame,
                             text="Classical Conditioning (during ITI)", padx=6, pady=4)
        pccf.grid(row=4, column=0, columnspan=4, sticky="ew", padx=4, pady=4)

        tk.Label(pccf, text="Ports:", anchor="w").grid(
            row=0, column=0, sticky="w", padx=4, pady=2)
        ppf = tk.Frame(pccf)
        ppf.grid(row=0, column=1, columnspan=3, sticky="w")
        self._passive_port_vars = {}
        for port in ("A", "B", "C"):
            v = tk.BooleanVar(value=(port != "C"))
            self._passive_port_vars[port] = v
            tk.Checkbutton(ppf, text=f"Port {port}", variable=v).pack(
                side="left", padx=4)

        self._passive_vars["cc_led_on_time"] = tk.StringVar()
        self._passive_vars["cc_iti_min"]     = tk.StringVar()
        self._passive_vars["cc_iti_max"]     = tk.StringVar()
        self._passive_vars["cc_reward_prob"] = tk.StringVar()
        self._passive_vars["cc_delay"]       = tk.StringVar()
        self._passive_vars["valve_time_A"]   = tk.StringVar()
        self._passive_vars["valve_time_B"]   = tk.StringVar()
        self._passive_vars["valve_time_C"]   = tk.StringVar()
        for i, (label, key) in enumerate([
            ("CC LED on time (s):",   "cc_led_on_time"),
            ("CC trial ITI min (s):", "cc_iti_min"),
            ("CC trial ITI max (s):", "cc_iti_max"),
            ("Reward probability:",   "cc_reward_prob"),
            ("CC delay (s):",         "cc_delay"),
            ("Valve time A (s):",     "valve_time_A"),
            ("Valve time B (s):",     "valve_time_B"),
            ("Valve time C (s):",     "valve_time_C"),
        ]):
            self._row(pccf, label, self._passive_vars[key], row=1 + i, width=10)
        tk.Label(pccf,
                 text="(CC delay: idle time before conditioning starts each ITI)",
                 font=("Arial", 8), fg="gray").grid(
            row=9, column=0, columnspan=4, sticky="w", padx=4)

        # ── Notes ─────────────────────────────────────────────────────────────
        ttk.Separator(root, orient="horizontal").grid(
            row=17, column=0, columnspan=4, sticky="ew", padx=8, pady=4)
        tk.Label(root, text="Notes:", anchor="w").grid(
            row=18, column=0, sticky="nw", **pad)
        self._notes = tk.Text(root, width=40, height=3, font=("Arial", 9))
        self._notes.grid(row=18, column=1, columnspan=3, sticky="ew", **pad)

        # ── Buttons ───────────────────────────────────────────────────────────
        bf = tk.Frame(root)
        bf.grid(row=19, column=0, columnspan=4, pady=(8, 14))
        tk.Button(bf, text="Start Session", bg="#4CAF50", fg="white",
                  font=("Arial", 11, "bold"), width=18,
                  command=self._on_start).pack(side="left", padx=8)
        tk.Button(bf, text="Cancel", width=10,
                  command=self.root.destroy).pack(side="left", padx=8)

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _on_species_change(self):
        sp = self._species_var.get()
        d = SPECIES_DEFAULTS[sp]
        self._cc_vars["led_on_time"].set(str(d["cc_led_on_time"]))
        self._cc_vars["iti_min"].set(str(d["cc_iti_min"]))
        self._cc_vars["iti_max"].set(str(d["cc_iti_max"]))
        self._cc_vars["reward_prob"].set(str(d["cc_reward_prob"]))
        for p in "ABC":
            self._cc_vars[f"valve_time_{p}"].set(str(d[f"valve_time_{p}"]))
        self._cc_vars["session_dur"].set(str(d["session_duration"]))

        self._speed_vars["door_open_speed"].set(str(d["door_open_speed"]))
        self._speed_vars["door_close_speed"].set(str(d["door_close_speed"]))
        self._speed_vars["table_speed"].set(str(d["table_speed"]))

        self._task_vars["s1_n"].set(str(d["s1_n"]))
        self._task_vars["s1_duration"].set(str(d["s1_duration"]))
        self._task_vars["s1_box"].set(str(d["s1_box"]))
        self._task_vars["s1_iti_min"].set(str(d["s1_iti_min"]))
        self._task_vars["s1_iti_max"].set(str(d["s1_iti_max"]))
        self._task_vars["s2_n"].set(str(d["s2_n"]))
        self._task_vars["s2_duration"].set(str(d["s2_duration"]))
        self._task_vars["s2_box"].set(str(d["s2_box"]))
        self._task_vars["s2_iti_min"].set(str(d["s2_iti_min"]))
        self._task_vars["s2_iti_max"].set(str(d["s2_iti_max"]))
        self._task_vars["cc_led_on_time"].set(str(d["cc_led_on_time"]))
        self._task_vars["cc_iti_min"].set(str(d["cc_iti_min"]))
        self._task_vars["cc_iti_max"].set(str(d["cc_iti_max"]))
        self._task_vars["cc_reward_prob"].set(str(d["cc_reward_prob"]))
        self._task_vars["cc_delay"].set(str(d["cc_delay"]))
        for p in "ABC":
            self._task_vars[f"valve_time_{p}"].set(str(d[f"valve_time_{p}"]))

        for i in range(4):
            self._passive_vars[f"box{i}_n"].set(str(d["passive_box_n"]))
        self._passive_vars["duration"].set(str(d["passive_duration"]))
        self._passive_vars["iti_min"].set(str(d["passive_iti_min"]))
        self._passive_vars["iti_max"].set(str(d["passive_iti_max"]))
        self._passive_vars["cc_led_on_time"].set(str(d["cc_led_on_time"]))
        self._passive_vars["cc_iti_min"].set(str(d["cc_iti_min"]))
        self._passive_vars["cc_iti_max"].set(str(d["cc_iti_max"]))
        self._passive_vars["cc_reward_prob"].set(str(d["cc_reward_prob"]))
        self._passive_vars["cc_delay"].set(str(d["cc_delay"]))
        for p in "ABC":
            self._passive_vars[f"valve_time_{p}"].set(str(d[f"valve_time_{p}"]))

    def _on_setup_change(self, _event=None):
        """Select the chosen setup's camera, if one is linked to it."""
        serial = SETUP_CAMERAS.get(self._setup_var.get(), "")
        if serial:
            self._camera.serial = serial
            self._setup_camera_label.config(text=f"camera {serial}")
        else:
            self._setup_camera_label.config(
                text="no camera linked" if self._setup_var.get() else "")

    def _on_treatment_toggle(self):
        self._treatment_entry.config(
            state="normal" if self._treatment_var.get() else "disabled")

    def _browse_save_root(self):
        chosen = filedialog.askdirectory(
            initialdir=self._vars["save_root"].get() or os.getcwd(),
            title="Choose folder to save session data into",
        )
        if chosen:
            self._vars["save_root"].set(chosen)

    def _on_mode_change(self):
        mode = self._mode_var.get()
        self._training_frame.grid_remove()
        self._task_frame.grid_remove()
        self._passive_frame.grid_remove()
        if mode == "training":
            self._training_frame.grid()
        elif mode == "task":
            self._task_frame.grid()
        else:
            self._passive_frame.grid()
        self.root.update_idletasks()

    # ── Validation and start ──────────────────────────────────────────────────

    def _on_start(self):
        errors = []
        mode   = self._mode_var.get()

        animal    = self._vars["animal"].get().strip() or "unknown"
        session_n = self._vars["session_n"].get().strip() or "1"
        port      = self._vars["port"].get().strip()
        if not port:
            errors.append("Serial port is required.")

        save_root = self._vars["save_root"].get().strip()
        if not save_root:
            errors.append("Save folder is required.")

        setup = self._setup_var.get()
        if not setup:
            errors.append("Select which setup this session runs on.")

        treatment = self._treatment_var.get()
        treatment_details = self._treatment_detail_var.get().strip() if treatment else ""
        if treatment and not treatment_details:
            errors.append("Treatment is ticked — describe the treatment.")

        try:
            baud = int(self._vars["baud"].get())
        except ValueError:
            errors.append("Baud rate must be an integer.")
            baud = None

        try:
            door_open_speed = parse_motor_speed(self._speed_vars["door_open_speed"].get())
            door_close_speed = parse_motor_speed(self._speed_vars["door_close_speed"].get())
            table_speed = parse_motor_speed(self._speed_vars["table_speed"].get())
        except ValueError:
            errors.append("Motor speeds must be integers (0-255), or blank/'off' to leave unset.")
            door_open_speed = door_close_speed = table_speed = None

        if mode == "training":
            ports = [p for p in ("A", "B", "C") if self._port_vars[p].get()]
            if not ports:
                errors.append("Select at least one port for conditioning.")

            try:
                led_on_time  = float(self._cc_vars["led_on_time"].get())
                iti_min      = float(self._cc_vars["iti_min"].get())
                iti_max      = float(self._cc_vars["iti_max"].get())
                reward_prob  = float(self._cc_vars["reward_prob"].get())
                valve_time_A = float(self._cc_vars["valve_time_A"].get())
                valve_time_B = float(self._cc_vars["valve_time_B"].get())
                valve_time_C = float(self._cc_vars["valve_time_C"].get())
            except ValueError:
                errors.append("LED on time, ITI, reward prob, and valve times must be numbers.")
                led_on_time = iti_min = iti_max = reward_prob = None
                valve_time_A = valve_time_B = valve_time_C = None

            if reward_prob is not None and not (0.0 < reward_prob <= 1.0):
                errors.append("Reward probability must be between 0 (exclusive) and 1.")

            session_dur_str = self._cc_vars["session_dur"].get().strip()
            session_duration = None
            if session_dur_str:
                try:
                    session_duration = float(session_dur_str)
                except ValueError:
                    errors.append("Session duration must be a number (seconds).")

            if errors:
                messagebox.showerror("Input Error", "\n".join(errors))
                return

            self.result = {
                "mode":            "training",
                "species":         self._species_var.get(),
                "animal":          animal,
                "session_n":       session_n,
                "save_root":       save_root,
                "port":            port,
                "baud":            baud,
                "door_open_speed": door_open_speed,
                "door_close_speed": door_close_speed,
                "table_speed":     table_speed,
                "ports":           ports,
                "led_on_time":     led_on_time,
                "iti_min":         iti_min,
                "iti_max":         iti_max,
                "reward_prob":     reward_prob,
                "valve_times":     {"A": valve_time_A, "B": valve_time_B, "C": valve_time_C},
                "session_duration":session_duration,
                "auto_reward":     self._auto_reward_var.get(),
                "notes":           self._notes.get("1.0", "end").strip(),
            }

        elif mode == "task":
            cc_ports = [p for p in ("A", "B", "C") if self._task_port_vars[p].get()]
            # Empty is allowed: no CC ports means the ITI still runs (same
            # duration), just with no classical conditioning during it.

            task = self._task_vars
            s1_id = task["s1_id"].get().strip()
            s2_id = task["s2_id"].get().strip()
            s1_angle = s2_angle = None
            try:
                s1_n        = int(task["s1_n"].get())
                s1_duration = float(task["s1_duration"].get())
                s1_box      = int(task["s1_box"].get())
                s1_iti_min  = float(task["s1_iti_min"].get())
                s1_iti_max  = float(task["s1_iti_max"].get())
                s2_n        = int(task["s2_n"].get())
                s2_duration = float(task["s2_duration"].get())
                s2_box      = int(task["s2_box"].get())
                s2_iti_min  = float(task["s2_iti_min"].get())
                s2_iti_max  = float(task["s2_iti_max"].get())
                cc_led       = float(task["cc_led_on_time"].get())
                cc_iti_min   = float(task["cc_iti_min"].get())
                cc_iti_max   = float(task["cc_iti_max"].get())
                cc_prob      = float(task["cc_reward_prob"].get())
                cc_delay     = float(task["cc_delay"].get())
                valve_time_A = float(task["valve_time_A"].get())
                valve_time_B = float(task["valve_time_B"].get())
                valve_time_C = float(task["valve_time_C"].get())
            except ValueError:
                errors.append("All task parameters must be numbers.")
                s1_n = None

            if s1_n is not None:
                if s1_n < 1:
                    errors.append("S1 must have at least 1 presentation.")
                if s2_n < 0:
                    errors.append("S2 presentations cannot be negative.")
                if s1_box not in (0, 1, 2, 3):
                    errors.append("S1 box must be 0, 1, 2, or 3.")
                else:
                    s1_angle = s1_box * 90
                if s2_box not in (0, 1, 2, 3):
                    errors.append("S2 box must be 0, 1, 2, or 3.")
                else:
                    s2_angle = s2_box * 90

            if errors:
                messagebox.showerror("Input Error", "\n".join(errors))
                return

            self.result = {
                "mode":          "task",
                "species":       self._species_var.get(),
                "animal":        animal,
                "session_n":     session_n,
                "save_root":     save_root,
                "port":          port,
                "baud":          baud,
                "door_open_speed": door_open_speed,
                "door_close_speed": door_close_speed,
                "table_speed":     table_speed,
                "s1_id":         s1_id,
                "s1_n":          s1_n,
                "s1_duration":   s1_duration,
                "s1_box":        s1_box,
                "s1_angle":      s1_angle,
                "s1_iti_min":    s1_iti_min,
                "s1_iti_max":    s1_iti_max,
                "s2_id":         s2_id,
                "s2_n":          s2_n,
                "s2_duration":   s2_duration,
                "s2_box":        s2_box,
                "s2_angle":      s2_angle,
                "s2_iti_min":    s2_iti_min,
                "s2_iti_max":    s2_iti_max,
                "cc_ports":      cc_ports,
                "cc_led_on_time":cc_led,
                "cc_iti_min":    cc_iti_min,
                "cc_iti_max":    cc_iti_max,
                "cc_reward_prob":cc_prob,
                "cc_delay":      cc_delay,
                "valve_times":   {"A": valve_time_A, "B": valve_time_B, "C": valve_time_C},
                "notes":         self._notes.get("1.0", "end").strip(),
            }

        else:  # passivetest
            cc_ports = [p for p in ("A", "B", "C") if self._passive_port_vars[p].get()]
            # Empty is allowed: no CC ports means the ITI still runs (same
            # duration), just with no classical conditioning during it.

            pv = self._passive_vars
            box_ids = [pv[f"box{i}_id"].get().strip() for i in range(4)]
            box_n = duration = iti_min = iti_max = None
            cc_led = cc_iti_min = cc_iti_max = cc_prob = cc_delay = None
            valve_time_A = valve_time_B = valve_time_C = None
            try:
                box_n        = [int(pv[f"box{i}_n"].get()) for i in range(4)]
                duration     = float(pv["duration"].get())
                iti_min      = float(pv["iti_min"].get())
                iti_max      = float(pv["iti_max"].get())
                cc_led       = float(pv["cc_led_on_time"].get())
                cc_iti_min   = float(pv["cc_iti_min"].get())
                cc_iti_max   = float(pv["cc_iti_max"].get())
                cc_prob      = float(pv["cc_reward_prob"].get())
                cc_delay     = float(pv["cc_delay"].get())
                valve_time_A = float(pv["valve_time_A"].get())
                valve_time_B = float(pv["valve_time_B"].get())
                valve_time_C = float(pv["valve_time_C"].get())
            except ValueError:
                errors.append("All passive test parameters must be numbers.")

            if box_n is not None:
                if any(n < 0 for n in box_n):
                    errors.append("Box presentation counts cannot be negative.")
                total = sum(box_n)
                if total < 1:
                    errors.append("At least one box must have 1 or more presentations.")
                else:
                    max_n = max(box_n)
                    max_allowed = (total + 1) // 2
                    if max_n > max_allowed:
                        worst = box_n.index(max_n)
                        errors.append(
                            f"Box {worst} has {max_n} of {total} total presentations — "
                            f"no arrangement avoids consecutive repeats "
                            f"(max allowed is {max_allowed}). Reduce its count or add "
                            f"presentations to other boxes."
                        )
                if duration is not None and duration <= 0:
                    errors.append("Presentation duration must be greater than 0.")
                if iti_min is not None and iti_max is not None and iti_min > iti_max:
                    errors.append("ITI min cannot be greater than ITI max.")

            if cc_prob is not None and not (0.0 < cc_prob <= 1.0):
                errors.append("Reward probability must be between 0 (exclusive) and 1.")

            if errors:
                messagebox.showerror("Input Error", "\n".join(errors))
                return

            self.result = {
                "mode":            "passivetest",
                "species":         self._species_var.get(),
                "animal":          animal,
                "session_n":       session_n,
                "save_root":       save_root,
                "port":            port,
                "baud":            baud,
                "door_open_speed": door_open_speed,
                "door_close_speed": door_close_speed,
                "table_speed":     table_speed,
                "box_ids":         box_ids,
                "box_n":           box_n,
                "presentation_duration": duration,
                "iti_min":         iti_min,
                "iti_max":         iti_max,
                "cc_ports":        cc_ports,
                "cc_led_on_time":  cc_led,
                "cc_iti_min":      cc_iti_min,
                "cc_iti_max":      cc_iti_max,
                "cc_reward_prob":  cc_prob,
                "cc_delay":        cc_delay,
                "valve_times":     {"A": valve_time_A, "B": valve_time_B, "C": valve_time_C},
                "notes":           self._notes.get("1.0", "end").strip(),
            }

        # A deliberate camera override is allowed, but confirm it: filming the
        # neighbouring rig by mistake is only noticed after the session.
        linked = SETUP_CAMERAS.get(setup, "")
        if (self._camera.record and linked and self._camera.serial != linked
                and not messagebox.askyesno(
                    "Camera mismatch",
                    f"{setup} is linked to camera {linked}, but camera "
                    f"{self._camera.serial or '(auto)'} is selected.\n\n"
                    f"Record from the selected camera anyway?")):
            self.result = None
            return

        self.result["setup"]              = setup
        self.result["treatment"]          = treatment
        self.result["treatment_details"]  = treatment_details
        self.result["record_camera"] = self._camera.record
        self.result["camera_serial"] = self._camera.serial
        self.result["show_sensor_display"] = self._sensor_display_var.get()

        self._update_history()
        self._save_settings()
        self.root.destroy()

    # ── Public ────────────────────────────────────────────────────────────────

    def run(self):
        """Block until dialog closes; returns params dict or None."""
        self.root.mainloop()
        return self.result
