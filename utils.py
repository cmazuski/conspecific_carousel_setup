# utils.py
from datetime import datetime
from typing import Optional

def now(): return datetime.now()

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