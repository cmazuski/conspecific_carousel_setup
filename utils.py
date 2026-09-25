# utils.py
from datetime import datetime
import os
from typing import Optional, Tuple

def now(): return datetime.now()

def parse_beambreak(msg: str) -> Tuple[Optional[str], Optional[str]]:
    msg_l = msg.lower()
    if not any(k in msg_l for k in ("beambreak", "sensor")):
        return None, None
    if "triggered" in msg_l: state = "triggered"
    elif "cleared" in msg_l: state = "cleared"
    else: return None, None
    if "port a" in msg_l: return "A", state
    if "port b" in msg_l: return "B", state
    if "port c" in msg_l: return "C", state
    if "door" in msg_l: return "doorsensor", state
    if "table" in msg_l: return "table", state
    return None, None

def safe_filename(base: str, ext: str) -> str:
    name = f"{base}.{ext}"
    if not os.path.exists(name): return name
    i = 0
    while True:
        name_i = f"{base}_{i}.{ext}"
        if not os.path.exists(name_i): return name_i
        i += 1

# Tokens that mean "leave this motor speed unset" — for firmware builds that
# predate the door/table speed registers and would not ACK a write to them.
SPEED_OFF_TOKENS = {"", "off", "nan", "none", "n/a", "na", "-"}

def parse_motor_speed(text: str) -> Optional[int]:
    """Parse a motor-speed GUI field into 0-255 or None (unset/unsupported).

    Returns None if the field is blank or one of SPEED_OFF_TOKENS, meaning
    the speed register should not be written. Raises ValueError if the text
    is not empty/'off' and not a valid integer in 0-255.
    """
    if text.strip().lower() in SPEED_OFF_TOKENS:
        return None
    value = int(text.strip())
    if not 0 <= value <= 255:
        raise ValueError(f"Motor speed must be between 0 and 255 (got {value}).")
    return value