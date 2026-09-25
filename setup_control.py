#!/usr/bin/env python3
"""
Setup control GUI for conspecific carousel (new firmware).
Provides buttons for all basic device commands:
  LEDs A/B/C, reward valves A/B/C, door open/close, turntable 90° CW/CCW,
  buzzer (frequency, on/off, timed beep).
"""

import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

from protocol import (
    REG_PA_LED, REG_PA_VALVE,
    REG_PB_LED, REG_PB_VALVE,
    REG_PC_LED, REG_PC_VALVE,
    REG_DOOR_CMD, REG_TABLE_CMD,
    REG_DOOR_OPN_SPD, REG_DOOR_CLS_SPD, REG_TABLE_SPD,
    REG_BZR_EN, REG_BZR_FREQ,
    BUZZER_HZ_PER_UNIT, BUZZER_FREQ_MIN, BUZZER_FREQ_MAX,
    build_table_command,
    reg_name, format_value,
)
from serial_comm import DeviceConnection, list_serial_ports
from tk_window_helpers import make_scrollable, fit_window_to_screen

BAUDRATE = 115200

# Turntable value for 90 degrees (= 2 eighths of a turn)
TABLE_90_CW  = build_table_command(0, 2)
TABLE_90_CCW = build_table_command(1, 2)

# Default motor speeds (0-255), matching firmware defaults
DOOR_OPEN_SPEED_DEFAULT = 255
DOOR_CLOSE_SPEED_DEFAULT = 40
TABLE_SPEED_DEFAULT = 40

# Buzzer frequency register default: 50 x 20 = 1000 Hz, the firmware's own
# power-on frequency.
BUZZER_FREQ_DEFAULT = 50

# Beep duration limits (ms). Every beep switches itself off; the cap stops a
# typo from leaving the buzzer on for minutes.
BEEP_MIN_MS = 10
BEEP_MAX_MS = 10000


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Carousel Setup Control")
        self.resizable(True, True)
        self.conn: DeviceConnection | None = None
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        fit_window_to_screen(self._scroll_body)

    # ------------------------------------------------------------------ UI --

    def _build_ui(self):
        body = make_scrollable(self)
        self._scroll_body = body
        pad = dict(padx=6, pady=4)

        # Connection panel
        conn_frame = ttk.LabelFrame(body, text="Connection")
        conn_frame.grid(row=0, column=0, columnspan=2, sticky="ew", **pad)

        ttk.Label(conn_frame, text="COM Port:").grid(row=0, column=0, **pad)
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(conn_frame, textvariable=self.port_var, width=12)
        self.port_combo.grid(row=0, column=1, **pad)
        ttk.Button(conn_frame, text="Refresh", command=self._refresh_ports).grid(row=0, column=2, **pad)
        self.connect_btn = ttk.Button(conn_frame, text="Connect", command=self._toggle_connect)
        self.connect_btn.grid(row=0, column=3, **pad)
        self.status_lbl = tk.Label(conn_frame, text="Disconnected", fg="red", width=14)
        self.status_lbl.grid(row=0, column=4, **pad)
        self._refresh_ports()

        # LEDs
        led_frame = ttk.LabelFrame(body, text="LEDs")
        led_frame.grid(row=1, column=0, sticky="nsew", **pad)

        led_defs = [("LED A", REG_PA_LED), ("LED B", REG_PB_LED), ("LED C", REG_PC_LED)]
        for i, (label, reg) in enumerate(led_defs):
            ttk.Label(led_frame, text=label, width=6).grid(row=i, column=0, **pad)
            ttk.Button(led_frame, text="On",
                       command=lambda r=reg: self._write(r, 1)).grid(row=i, column=1, **pad)
            ttk.Button(led_frame, text="Off",
                       command=lambda r=reg: self._write(r, 0)).grid(row=i, column=2, **pad)

        # Reward delivery
        reward_frame = ttk.LabelFrame(body, text="Reward Delivery")
        reward_frame.grid(row=2, column=0, sticky="nsew", **pad)

        ttk.Label(reward_frame, text="Duration (ms):").grid(row=0, column=0, columnspan=2, **pad)
        self.reward_dur_var = tk.IntVar(value=100)
        ttk.Spinbox(reward_frame, from_=10, to=5000, increment=10,
                    textvariable=self.reward_dur_var, width=7).grid(row=0, column=2, **pad)

        reward_defs = [("Reward A", REG_PA_VALVE), ("Reward B", REG_PB_VALVE), ("Reward C", REG_PC_VALVE)]
        for i, (label, reg) in enumerate(reward_defs):
            ttk.Button(reward_frame, text=label, width=14,
                       command=lambda r=reg: self._reward_pulse(r)).grid(
                           row=i + 1, column=0, columnspan=3, sticky="ew", **pad)

        # Door
        door_frame = ttk.LabelFrame(body, text="Door")
        door_frame.grid(row=1, column=1, sticky="nsew", **pad)

        ttk.Button(door_frame, text="Open",  width=10,
                   command=lambda: self._write(REG_DOOR_CMD, 0x00)).grid(row=0, column=0, **pad)
        ttk.Button(door_frame, text="Close", width=10,
                   command=lambda: self._write(REG_DOOR_CMD, 0x01)).grid(row=0, column=1, **pad)

        # Turntable
        table_frame = ttk.LabelFrame(body, text="Turntable (90°)")
        table_frame.grid(row=2, column=1, sticky="nsew", **pad)

        ttk.Button(table_frame, text="90° CW",  width=10,
                   command=lambda: self._write(REG_TABLE_CMD, TABLE_90_CW)).grid(row=0, column=0, **pad)
        ttk.Button(table_frame, text="90° CCW", width=10,
                   command=lambda: self._write(REG_TABLE_CMD, TABLE_90_CCW)).grid(row=0, column=1, **pad)

        # Motor speeds
        speed_frame = ttk.LabelFrame(body, text="Motor Speeds (0-255)")
        speed_frame.grid(row=3, column=0, columnspan=2, sticky="ew", **pad)

        speed_defs = [
            ("Door Open",  REG_DOOR_OPN_SPD, DOOR_OPEN_SPEED_DEFAULT),
            ("Door Close", REG_DOOR_CLS_SPD, DOOR_CLOSE_SPEED_DEFAULT),
            ("Turntable",  REG_TABLE_SPD,    TABLE_SPEED_DEFAULT),
        ]
        self.speed_vars = {}
        self.speed_enabled_vars = {}
        for i, (label, reg, default) in enumerate(speed_defs):
            ttk.Label(speed_frame, text=label, width=10).grid(row=i, column=0, **pad)
            var = tk.IntVar(value=default)
            self.speed_vars[reg] = var
            value_lbl = ttk.Label(speed_frame, text=str(default), width=4)

            def _on_move(raw_value, var=var, lbl=value_lbl):
                iv = round(float(raw_value))
                var.set(iv)
                lbl.config(text=str(iv))

            scale = ttk.Scale(speed_frame, from_=0, to=255, orient="horizontal",
                               length=200, command=_on_move)
            scale.set(default)
            scale.grid(row=i, column=1, **pad)
            value_lbl.grid(row=i, column=2, **pad)

            enabled_var = tk.BooleanVar(value=True)
            self.speed_enabled_vars[reg] = enabled_var
            ttk.Checkbutton(speed_frame, text="Enabled", variable=enabled_var).grid(
                row=i, column=3, **pad)

            scale.bind("<ButtonRelease-1>",
                       lambda _e, r=reg, vv=var: self._write_speed(r, vv.get()))

        ttk.Label(speed_frame,
                  text="Uncheck 'Enabled' for firmware without speed-register support.",
                  font=("Arial", 8), foreground="gray").grid(
            row=len(speed_defs), column=0, columnspan=4, sticky="w", padx=6, pady=(0, 2))

        # Buzzer
        buzzer_frame = ttk.LabelFrame(body, text="Buzzer")
        buzzer_frame.grid(row=4, column=0, columnspan=2, sticky="ew", **pad)

        ttk.Label(buzzer_frame, text="Frequency", width=10).grid(row=0, column=0, **pad)
        self.buzzer_freq_var = tk.IntVar(value=BUZZER_FREQ_DEFAULT)
        buzzer_hz_lbl = ttk.Label(buzzer_frame, width=8,
                                  text=f"{BUZZER_FREQ_DEFAULT * BUZZER_HZ_PER_UNIT} Hz")

        def _on_buzzer_move(raw_value):
            iv = round(float(raw_value))
            self.buzzer_freq_var.set(iv)
            buzzer_hz_lbl.config(text=f"{iv * BUZZER_HZ_PER_UNIT} Hz")

        buzzer_scale = ttk.Scale(buzzer_frame, from_=BUZZER_FREQ_MIN, to=BUZZER_FREQ_MAX,
                                 orient="horizontal", length=200, command=_on_buzzer_move)
        buzzer_scale.set(BUZZER_FREQ_DEFAULT)
        buzzer_scale.grid(row=0, column=1, columnspan=3, **pad)
        buzzer_hz_lbl.grid(row=0, column=4, **pad)

        # Only timed beeps — there is deliberately no plain "On", so the
        # buzzer can't be left sounding. Frequency is sent with each beep.
        ttk.Label(buzzer_frame, text="Duration (ms):").grid(row=1, column=0, columnspan=2, **pad)
        self.beep_dur_var = tk.IntVar(value=500)
        ttk.Spinbox(buzzer_frame, from_=BEEP_MIN_MS, to=BEEP_MAX_MS, increment=50,
                    textvariable=self.beep_dur_var, width=7).grid(row=1, column=2, **pad)
        ttk.Button(buzzer_frame, text="Beep", width=8,
                   command=self._buzzer_beep).grid(row=1, column=3, **pad)
        ttk.Button(buzzer_frame, text="Stop", width=8,
                   command=self._buzzer_stop).grid(row=1, column=4, **pad)

        ttk.Label(buzzer_frame,
                  text="Needs firmware with buzzer support (registers 0x30/0x31) — "
                       "older builds won't ACK.",
                  font=("Arial", 8), foreground="gray").grid(
            row=2, column=0, columnspan=5, sticky="w", padx=6, pady=(0, 2))
        # Set once a beep has been sent, so disconnect/close only sends a
        # buzzer-off to firmware that has one.
        self._buzzer_used = False
        # Incremented by every Beep/Stop; a beep only switches the buzzer off
        # if it is still the latest, so a new beep isn't cut short by an old one.
        self._beep_id = 0
        self._beep_lock = threading.Lock()

        # Log
        log_frame = ttk.LabelFrame(body, text="Log")
        log_frame.grid(row=5, column=0, columnspan=2, sticky="ew", **pad)

        self.log_text = tk.Text(log_frame, height=10, width=62, state="disabled", font=("Courier", 9))
        self.log_text.grid(row=0, column=0, **pad)
        sb = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.log_text["yscrollcommand"] = sb.set

    # ------------------------------------------------------------- actions --

    def _refresh_ports(self):
        ports = list_serial_ports()
        self.port_combo["values"] = ports
        if ports and not self.port_var.get():
            self.port_var.set(ports[0])

    def _toggle_connect(self):
        if self.conn and self.conn.is_connected:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        port = self.port_var.get().strip()
        if not port:
            messagebox.showerror("Error", "Select a COM port first.")
            return
        try:
            self.conn = DeviceConnection(port, baudrate=BAUDRATE)
            self.conn.on_event(self._on_event)
            self.conn.on_error(self._on_error)
            self.conn.connect()
            self.status_lbl.config(text="Connected", fg="green")
            self.connect_btn.config(text="Disconnect")
            self.port_combo.config(state="disabled")
            self._log(f"Connected to {port} @ {BAUDRATE} baud")
            for reg, var in self.speed_vars.items():
                self._write_speed(reg, var.get())
        except Exception as e:
            messagebox.showerror("Connection Error", str(e))

    def _disconnect(self):
        self._silence_buzzer()
        if self.conn:
            self.conn.disconnect()
            self.conn = None
        self.status_lbl.config(text="Disconnected", fg="red")
        self.connect_btn.config(text="Connect")
        self.port_combo.config(state="normal")
        self._log("Disconnected")

    def _write(self, register: int, value: int) -> None:
        if not self.conn or not self.conn.is_connected:
            self._log("[WARN] Not connected")
            return
        conn = self.conn

        def _do():
            try:
                conn.write_register(register, value)
                self._log(f"[TX] {reg_name(register)} = {format_value(register, value)}")
            except Exception as e:
                self._log(f"[ERROR] {e}")

        threading.Thread(target=_do, daemon=True).start()

    def _write_speed(self, register: int, value: int) -> None:
        """Write a motor-speed register, unless its 'Enabled' checkbox is off
        (for old firmware builds that don't implement the speed registers)."""
        if not self.speed_enabled_vars[register].get():
            self._log(f"[INFO] {reg_name(register)} disabled — not sent (old firmware mode)")
            return
        self._write(register, value)

    def _reward_pulse(self, valve_reg: int) -> None:
        if not self.conn or not self.conn.is_connected:
            self._log("[WARN] Not connected")
            return
        conn = self.conn
        duration_ms = self.reward_dur_var.get()

        def pulse():
            try:
                conn.write_register(valve_reg, 1)
                self._log(f"[TX] {reg_name(valve_reg)} = On")
                time.sleep(duration_ms / 1000)
                conn.write_register(valve_reg, 0)
                self._log(f"[TX] {reg_name(valve_reg)} = Off")
            except Exception as e:
                self._log(f"[ERROR] {e}")

        threading.Thread(target=pulse, daemon=True).start()

    def _buzzer_beep(self) -> None:
        """Sound the buzzer at the slider's frequency for the set duration,
        then switch it off."""
        if not self.conn or not self.conn.is_connected:
            self._log("[WARN] Not connected")
            return
        try:
            duration_ms = int(self.beep_dur_var.get())
        except (tk.TclError, ValueError):
            duration_ms = None
        if duration_ms is None or not BEEP_MIN_MS <= duration_ms <= BEEP_MAX_MS:
            self._log(f"[WARN] Beep duration must be {BEEP_MIN_MS}-{BEEP_MAX_MS} ms")
            return
        conn = self.conn
        freq = self.buzzer_freq_var.get()
        self._buzzer_used = True
        with self._beep_lock:
            self._beep_id += 1
            beep_id = self._beep_id

        def run():
            try:
                conn.write_register(REG_BZR_FREQ, freq)
                conn.write_register(REG_BZR_EN, 1)
                self._log(f"[TX] Buzzer = On @ {format_value(REG_BZR_FREQ, freq)} "
                          f"for {duration_ms} ms")
                time.sleep(duration_ms / 1000)
                with self._beep_lock:
                    if beep_id != self._beep_id:
                        return   # superseded by a newer Beep, or stopped
                conn.write_register(REG_BZR_EN, 0)
                self._log("[TX] Buzzer = Off")
            except Exception as e:
                self._log(f"[ERROR] {e}")

        threading.Thread(target=run, daemon=True).start()

    def _buzzer_stop(self) -> None:
        """Cut a beep short."""
        with self._beep_lock:
            self._beep_id += 1
        self._write(REG_BZR_EN, 0)

    def _silence_buzzer(self) -> None:
        """Before disconnecting: make sure a beep in progress doesn't keep
        sounding. Skipped if no beep was ever sent (e.g. old firmware)."""
        with self._beep_lock:
            self._beep_id += 1
        if self._buzzer_used and self.conn and self.conn.is_connected:
            try:
                self.conn.write_register(REG_BZR_EN, 0)
            except Exception as e:
                print(f"[WARN] Could not switch buzzer off: {e}")
            self._buzzer_used = False

    # ----------------------------------------------------------- callbacks --

    def _on_event(self, register: int, value: int) -> None:
        self._log(f"[EVENT] {reg_name(register)} = {format_value(register, value)}")

    def _on_error(self, msg: str) -> None:
        self._log(f"[ERROR] {msg}")

    def _log(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")

        def _update():
            self.log_text.config(state="normal")
            self.log_text.insert("end", f"{ts}  {msg}\n")
            self.log_text.see("end")
            self.log_text.config(state="disabled")

        self.after(0, _update)

    def _on_close(self):
        self._silence_buzzer()
        if self.conn:
            self.conn.disconnect()
        self.destroy()


if __name__ == "__main__":
    app = App()
    app.mainloop()
