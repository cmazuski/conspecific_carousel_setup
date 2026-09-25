#!/usr/bin/env python3
"""
Water calibration script for a single port (new firmware).

1) Send a 10 s flush x1
2) Record end volume and calculate change; user inputs flow volume (ml)
3) Calculate flow rate (ml per s) = volume / total flow duration
4) Return 0.001 / flow_rate = pulse duration (s) for 1 ul per pulse
"""

import argparse
import time
import serial

from protocol import (
    MSG_WRITE,
    REG_PA_LED, REG_PA_VALVE,
    REG_PB_LED, REG_PB_VALVE,
    REG_PC_LED, REG_PC_VALVE,
    build_packet,
)

PORT_REGISTERS = {
    "A": (REG_PA_LED, REG_PA_VALVE),
    "B": (REG_PB_LED, REG_PB_VALVE),
    "C": (REG_PC_LED, REG_PC_VALVE),
}


def _send(ser: serial.Serial, register: int, value: int) -> None:
    ser.write(build_packet(register, MSG_WRITE, value))
    ser.flush()


def flush(ser: serial.Serial, port: str, time_s: float = 10, n: int = 1) -> float:
    port = port.upper()
    if port not in PORT_REGISTERS:
        raise ValueError("Invalid port. Must be A, B, or C.")

    reg_led, reg_valve = PORT_REGISTERS[port]

    _send(ser, reg_led, 1)
    time.sleep(0.1)

    for _ in range(n):
        _send(ser, reg_valve, 1)
        time.sleep(time_s)
        _send(ser, reg_valve, 0)
        time.sleep(0.1)

    _send(ser, reg_led, 0)

    return time_s * n


def main() -> None:
    parser = argparse.ArgumentParser(description="Water calibration script (new firmware)")
    parser.add_argument("--port", required=True, help="Serial COM port (e.g., COM3)")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate (default: 115200)")
    args = parser.parse_args()

    try:
        ser = serial.Serial(port=args.port, baudrate=args.baud, timeout=0.2)
        print("Serial port open.")
    except Exception as e:
        print(f"[ERROR] Could not open port {args.port}: {e}")
        return

    time.sleep(2.0)

    port = input("Enter flush port (A/B/C): ").strip().upper()
    if port not in PORT_REGISTERS:
        print("[ERROR] Invalid port. Must be A, B, or C.")
        ser.close()
        return

    start_ml = float(input("Enter START volume (ml): ").strip())

    try:
        print("Flowing... (10 s)")
        total_flow_duration = flush(ser, port, time_s=10, n=1)
        print(f"Total flow duration: {total_flow_duration:.2f} s")

        end_ml = float(input("Enter END volume (ml): ").strip())
        water_ml = start_ml - end_ml

        if water_ml <= 0:
            print("[ERROR] End volume must be smaller than start volume.")
            return

        print(f"Flow volume: {water_ml:.3f} ml")
        flow_rate = water_ml / total_flow_duration
        print(f"Flow rate: {flow_rate:.3f} ml/s")
        duration_per_ul = 0.001 / flow_rate
        print(f"Pulse duration per 1 ul for port {port}: {duration_per_ul:.6f} s")

    except Exception as e:
        print(f"[ERROR] {e}")
    finally:
        ser.close()
        print("Serial port closed.")


if __name__ == "__main__":
    main()
