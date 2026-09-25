# check_status_reads.py — bench check for the door/table status READ path.
#
# The freeze fix (hardware.resync_door_state) recovers from a lost door-status
# EVENT by reading REG_DOOR_STATUS directly.  Nothing in this codebase used the
# READ path before, so run this once against the rig to confirm the firmware
# answers it.
#
#   python check_status_reads.py COM4
#
# Expected: every register reports a value within a second or so.
# If reads time out, the resync cannot help — the door waits still can't hang
# forever (they have timeouts now), but a lost event will cost a failed trial
# instead of a 2-second correction, and the firmware needs the READ handler.

import sys
import time

from serial_comm import DeviceConnection
from protocol import (
    REG_DOOR_STATUS, REG_TABLE_STATUS,
    REG_DOOR_SENSOR, REG_TABLE_SENSOR,
    reg_name, format_value,
)

CHECKS = [REG_DOOR_STATUS, REG_TABLE_STATUS, REG_DOOR_SENSOR, REG_TABLE_SENSOR]


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "COM4"
    baud = int(sys.argv[2]) if len(sys.argv) > 2 else 115200

    device = DeviceConnection(port, baudrate=baud)
    device.on_error(lambda msg: print(f"[ERROR] Serial: {msg}"))
    device.connect()
    time.sleep(2)   # let the device settle after the port opens

    ok = True
    try:
        for reg in CHECKS:
            t0 = time.time()
            try:
                ack = device.read_register(reg)
            except TimeoutError as e:
                print(f"  FAIL  {reg_name(reg):14s} (0x{reg:02X}) — {e}")
                ok = False
                continue

            dt = time.time() - t0
            if not ack or ack[0] != reg:
                print(f"  FAIL  {reg_name(reg):14s} (0x{reg:02X}) — "
                      f"reply was for a different register: {ack}")
                ok = False
            else:
                print(f"  OK    {reg_name(reg):14s} (0x{reg:02X}) = "
                      f"{format_value(reg, ack[1])}  [{dt * 1000:.0f} ms]")
    finally:
        device.disconnect()

    if ok:
        print("\nAll status registers are readable — door/table resync will work.")
    else:
        print("\nSome status reads failed. Door waits still time out safely, but "
              "lost status events cannot self-correct until the firmware answers "
              "READ for those registers.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
