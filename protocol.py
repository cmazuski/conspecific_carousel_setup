HEADER = 0xCC

MSG_WRITE = 0x01
MSG_READ = 0x02
MSG_ACK = 0x02
MSG_EVENT = 0x03

PACKET_SIZE = 4

# Register addresses
REG_LED_SYNC = 0x01
REG_DOOR_SENSOR = 0x02
REG_TABLE_SENSOR = 0x03
REG_CAM_A = 0x04
REG_CAM_B = 0x05
REG_DOOR_STATUS = 0x10
REG_DOOR_CMD = 0x11
REG_DOOR_OPN_SPD = 0x12
REG_DOOR_CLS_SPD = 0x13
REG_TABLE_STATUS = 0x18
REG_TABLE_CMD = 0x19
REG_TABLE_SPD = 0x1A
REG_PA_LED = 0x21
REG_PA_VALVE = 0x22
REG_PA_IR = 0x23
REG_PB_LED = 0x24
REG_PB_VALVE = 0x25
REG_PB_IR = 0x26
REG_PC_LED = 0x27
REG_PC_VALVE = 0x28
REG_PC_IR = 0x29

REGISTER_NAMES = {
    REG_LED_SYNC: "LED/Sync",
    REG_DOOR_SENSOR: "Door Sensor",
    REG_TABLE_SENSOR: "Table Sensor",
    REG_CAM_A: "Cam A",
    REG_CAM_B: "Cam B",
    REG_DOOR_STATUS: "Door Status",
    REG_DOOR_CMD: "Door Command",
    REG_DOOR_OPN_SPD: "Door Open Speed",
    REG_DOOR_CLS_SPD: "Door Close Speed",
    REG_TABLE_STATUS: "Table Status",
    REG_TABLE_CMD: "Table Command",
    REG_TABLE_SPD: "Table Speed",
    REG_PA_LED: "Port A LED",
    REG_PA_VALVE: "Port A Valve",
    REG_PA_IR: "Port A IR",
    REG_PB_LED: "Port B LED",
    REG_PB_VALVE: "Port B Valve",
    REG_PB_IR: "Port B IR",
    REG_PC_LED: "Port C LED",
    REG_PC_VALVE: "Port C Valve",
    REG_PC_IR: "Port C IR",
}

DOOR_STATUS_MAP = {0: "Closed", 1: "Opened", 2: "Moving", 3: "Paused"}
TABLE_STATUS_MAP = {0: "Stopped", 1: "Moving"}

READABLE_REGISTERS = [
    REG_LED_SYNC, REG_DOOR_SENSOR, REG_TABLE_SENSOR,
    REG_CAM_A, REG_CAM_B, REG_DOOR_STATUS, REG_TABLE_STATUS,
    REG_DOOR_OPN_SPD, REG_DOOR_CLS_SPD, REG_TABLE_SPD,
    REG_PA_LED, REG_PA_VALVE, REG_PA_IR,
    REG_PB_LED, REG_PB_VALVE, REG_PB_IR,
    REG_PC_LED, REG_PC_VALVE, REG_PC_IR,
]


def build_packet(register, msg_type, value=0):
    return bytes((HEADER, register, msg_type, value))


def parse_packet(data):
    if len(data) != PACKET_SIZE or data[0] != HEADER:
        return None
    return data[1], data[2], data[3]


def build_table_command(direction, eighths):
    """Build table command byte. direction: 0=CW, 1=CCW. eighths: 1/8-turn units (1-127)."""
    return ((direction & 1) << 7) | (eighths & 0x7F)


def format_value(register, value):
    if register == REG_DOOR_STATUS:
        return DOOR_STATUS_MAP.get(value, f"0x{value:02X}")
    if register == REG_TABLE_STATUS:
        return TABLE_STATUS_MAP.get(value, f"0x{value:02X}")
    if register in (REG_DOOR_SENSOR, REG_TABLE_SENSOR, REG_PA_IR, REG_PB_IR, REG_PC_IR):
        return "Detected" if value else "Clear"
    if register in (REG_LED_SYNC, REG_PA_LED, REG_PB_LED, REG_PC_LED,
                    REG_PA_VALVE, REG_PB_VALVE, REG_PC_VALVE):
        return "On" if value else "Off"
    return f"0x{value:02X}"


def reg_name(register):
    return REGISTER_NAMES.get(register, f"0x{register:02X}")


# Trigger options for condition engine: (display_name, register, value)
TRIGGER_OPTIONS = [
    ("Door Sensor = Detected", REG_DOOR_SENSOR, 1),
    ("Door Sensor = Clear", REG_DOOR_SENSOR, 0),
    ("Table Sensor = Detected", REG_TABLE_SENSOR, 1),
    ("Table Sensor = Clear", REG_TABLE_SENSOR, 0),
    ("Door Status = Closed", REG_DOOR_STATUS, 0),
    ("Door Status = Opened", REG_DOOR_STATUS, 1),
    ("Door Status = Moving", REG_DOOR_STATUS, 2),
    ("Door Status = Paused", REG_DOOR_STATUS, 3),
    ("Table Status = Stopped", REG_TABLE_STATUS, 0),
    ("Table Status = Moving", REG_TABLE_STATUS, 1),
    ("Port A IR = Detected", REG_PA_IR, 1),
    ("Port A IR = Clear", REG_PA_IR, 0),
    ("Port B IR = Detected", REG_PB_IR, 1),
    ("Port B IR = Clear", REG_PB_IR, 0),
    ("Port C IR = Detected", REG_PC_IR, 1),
    ("Port C IR = Clear", REG_PC_IR, 0),
]

# Action options for condition engine: (display_name, register, value)
ACTION_OPTIONS = [
    ("Door: Open", REG_DOOR_CMD, 0x00),
    ("Door: Close", REG_DOOR_CMD, 0x01),
    ("Door: Stop", REG_DOOR_CMD, 0x02),
    ("Table: CW 1/8 turn", REG_TABLE_CMD, build_table_command(0, 1)),
    ("Table: CCW 1/8 turn", REG_TABLE_CMD, build_table_command(1, 1)),
    ("Table: CW 1/4 turn", REG_TABLE_CMD, build_table_command(0, 2)),
    ("Table: CCW 1/4 turn", REG_TABLE_CMD, build_table_command(1, 2)),
    ("Table: CW 1/2 turn", REG_TABLE_CMD, build_table_command(0, 4)),
    ("Table: CCW 1/2 turn", REG_TABLE_CMD, build_table_command(1, 4)),
    ("LED/Sync: On", REG_LED_SYNC, 1),
    ("LED/Sync: Off", REG_LED_SYNC, 0),
    ("Port A LED: On", REG_PA_LED, 1),
    ("Port A LED: Off", REG_PA_LED, 0),
    ("Port A Valve: On", REG_PA_VALVE, 1),
    ("Port A Valve: Off", REG_PA_VALVE, 0),
    ("Port B LED: On", REG_PB_LED, 1),
    ("Port B LED: Off", REG_PB_LED, 0),
    ("Port B Valve: On", REG_PB_VALVE, 1),
    ("Port B Valve: Off", REG_PB_VALVE, 0),
    ("Port C LED: On", REG_PC_LED, 1),
    ("Port C LED: Off", REG_PC_LED, 0),
    ("Port C Valve: On", REG_PC_VALVE, 1),
    ("Port C Valve: Off", REG_PC_VALVE, 0),
]
