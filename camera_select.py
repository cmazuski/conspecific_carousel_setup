# camera_select.py — "which camera?" picker shared by the setup GUIs
#
# With two Basler cameras plugged in, Windows/pylon gives no stable ordering, so
# a camera is identified by its serial number (printed on the camera and stable
# across reboots and USB ports). The chosen serial is passed straight through to
# `cameracontrol --camera <serial>`, which lets two sessions started in two
# terminals each drive their own camera.

import json
import os
import subprocess
import sys
import tkinter as tk
from tkinter import ttk

# cameracontrol lives next to this file, at the repo root.
CAMERACONTROL_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "cameracontrol.py")

AUTO_LABEL = "Auto — first camera found"


def list_cameras(timeout: float = 30.0):
    """Attached Basler cameras, as dicts with serial/model/user_id/index.

    Enumeration runs inside cameracontrol (`--list-cameras`) rather than here so
    pypylon is never imported into the Tk process. Returns [] if pypylon isn't
    installed, no camera is attached, or anything else goes wrong — the picker
    then just offers "Auto", which is the old single-camera behaviour.
    """
    try:
        # CREATE_NO_WINDOW: don't flash a console if the GUI was started from
        # pythonw.exe / a shortcut rather than a terminal.
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        proc = subprocess.run(
            [sys.executable, CAMERACONTROL_PATH, "--list-cameras"],
            capture_output=True, text=True, timeout=timeout,
            creationflags=creationflags,
        )
    except Exception as e:
        print(f"[WARN] Could not list cameras: {e}")
        return []
    if proc.returncode != 0:
        print(f"[WARN] Could not list cameras:\n{proc.stderr.strip()}")
        return []
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        print(f"[WARN] Unexpected output from --list-cameras:\n{proc.stdout.strip()}")
        return []


def format_camera(cam) -> str:
    name = f" \"{cam['user_id']}\"" if cam.get("user_id") else ""
    return f"[{cam['index']}] {cam['model']}{name} — {cam['serial']}"


class CameraChooser:
    """A "Record camera" checkbox plus a camera dropdown and Refresh button.

    The selected serial (`serial`, "" meaning auto) is the source of truth; the
    dropdown is just a view of it, so a serial restored from last session's
    settings survives even when that camera isn't currently attached.
    """

    def __init__(self, parent, record_default=True, autodetect=True):
        self.frame = tk.Frame(parent)
        self.record_var = tk.BooleanVar(value=record_default)
        self._serial = ""
        self._label_var = tk.StringVar(value=AUTO_LABEL)
        self._serial_by_label = {AUTO_LABEL: ""}

        tk.Checkbutton(self.frame, text="Record camera",
                       variable=self.record_var).grid(
            row=0, column=0, sticky="w", padx=6, pady=2)
        tk.Label(self.frame, text="Camera:", anchor="w").grid(
            row=0, column=1, sticky="w", padx=(12, 2), pady=2)
        self._combo = ttk.Combobox(self.frame, textvariable=self._label_var,
                                   values=[AUTO_LABEL], state="readonly", width=42)
        self._combo.grid(row=0, column=2, sticky="w", padx=2, pady=2)
        self._combo.bind("<<ComboboxSelected>>", self._on_pick)
        tk.Button(self.frame, text="Refresh", command=self.refresh).grid(
            row=0, column=3, sticky="w", padx=4, pady=2)
        tk.Label(self.frame,
                 text="(pick a camera by serial so a second session in another "
                      "terminal can record the other one)",
                 font=("Arial", 8), fg="gray").grid(
            row=1, column=0, columnspan=4, sticky="w", padx=6)

        if autodetect:
            # After the window is drawn — enumerating takes a second or two, and
            # the operator shouldn't stare at an empty screen while it happens.
            self.frame.after(200, self.refresh)

    # ── Placement (mirrors the widget API the setup GUIs use) ─────────────────

    def grid(self, **kwargs):
        self.frame.grid(**kwargs)
        return self

    # ── State ─────────────────────────────────────────────────────────────────

    @property
    def record(self) -> bool:
        return bool(self.record_var.get())

    @record.setter
    def record(self, value):
        self.record_var.set(bool(value))

    @property
    def serial(self) -> str:
        return self._serial

    @serial.setter
    def serial(self, value):
        self._serial = (value or "").strip()
        self._show_current()

    # ── Internals ─────────────────────────────────────────────────────────────

    def _on_pick(self, _event=None):
        self._serial = self._serial_by_label.get(self._label_var.get(), "")

    def _show_current(self):
        for label, serial in self._serial_by_label.items():
            if serial == self._serial:
                self._label_var.set(label)
                return
        # Saved camera not (yet) detected — keep the serial, say so in the list.
        label = f"{self._serial} — not detected"
        self._serial_by_label[label] = self._serial
        self._combo["values"] = list(self._serial_by_label)
        self._label_var.set(label)

    def refresh(self):
        cameras = list_cameras()
        self._serial_by_label = {AUTO_LABEL: ""}
        for cam in cameras:
            self._serial_by_label[format_camera(cam)] = cam["serial"]
        self._combo["values"] = list(self._serial_by_label)
        self._show_current()
