from enum import IntEnum, IntFlag

SOF1_BIN = 0xAA
SOF2_BIN = 0x55

SIGNAL_MASK_BYTES = 8

FOC_USB_DEBUG_SIGNAL_LIST = [
    {"bit": 0,  "type": "u32",   "name": "timestamp"},
    {"bit": 1,  "type": "f",     "name": "adc_values.motor_temp"},
    {"bit": 2,  "type": "f",     "name": "adc_values.mosfet_temp"},
    {"bit": 3,  "type": "f",     "name": "adc_values.vbus"},
    {"bit": 4,  "type": "f",     "name": "ibus"},
    {"bit": 5,  "type": "f",     "name": "adc_values.phase_current.a"},
    {"bit": 6,  "type": "f",     "name": "adc_values.phase_current.b"},
    {"bit": 7,  "type": "f",     "name": "adc_values.phase_current.c"},

    {"bit": 8,  "type": "f",     "name": "ab_current.alpha"},
    {"bit": 9,  "type": "f",     "name": "ab_current.beta"},
    {"bit": 10, "type": "f",     "name": "dq_current.d"},
    {"bit": 11, "type": "f",     "name": "dq_current.q"},
    {"bit": 12, "type": "f",     "name": "dq_current_filtered.d"},
    {"bit": 13, "type": "f",     "name": "dq_current_filtered.q"},

    {"bit": 14, "type": "f",     "name": "phase_voltage.a"},
    {"bit": 15, "type": "f",     "name": "phase_voltage.b"},
    {"bit": 16, "type": "f",     "name": "phase_voltage.c"},
    {"bit": 17, "type": "f",     "name": "ab_voltage.alpha"},

    {"bit": 18, "type": "f",     "name": "ab_voltage.beta"},
    {"bit": 19, "type": "f",     "name": "dq_voltage.d"},
    {"bit": 20, "type": "f",     "name": "dq_voltage.q"},
    {"bit": 21, "type": "f",     "name": "encoder_angle_mechanical_wrapped"},
    {"bit": 22, "type": "f",     "name": "encoder_angle_mechanical_unwrapped"},
    {"bit": 23, "type": "f",     "name": "encoder_speed_mechanical"},
    {"bit": 24, "type": "f",     "name": "encoder_angle_electrical"},
    {"bit": 25, "type": "f",     "name": "encoder_speed_electrical"},
    {"bit": 26, "type": "f",     "name": "dq_current_setpoint.d"},

    {"bit": 27, "type": "f",     "name": "dq_current_setpoint.q"},
    {"bit": 28, "type": "f",     "name": "angle_setpoint"},
    {"bit": 29, "type": "f",     "name": "speed_setpoint"},
    {"bit": 30, "type": "u32",   "name": "execution_time.loop_max"},

    {"bit": 31, "type": "f",     "name": "hfi.injection_phase"},
    {"bit": 32, "type": "f",     "name": "hfi.i_alpha_l_raw"},
    {"bit": 33, "type": "f",     "name": "hfi.i_beta_l_raw"},
    {"bit": 34, "type": "f",     "name": "hfi.i_alpha_l_filtered"},
    {"bit": 35, "type": "f",     "name": "hfi.i_beta_l_filtered"},
]

FOC_PID_CONTROLLERS_LIST = [
    {"id": 0, "name": "pid_current_d"},
    {"id": 1, "name": "pid_current_q"},
    {"id": 2, "name": "pid_speed"},
    {"id": 3, "name": "pid_position"},
]

VAR_ID_LIST = [
    {"id": 0, "type": "f", "name": "dq_current_setpoint.d"},
    {"id": 1, "type": "f", "name": "dq_current_setpoint.q"},
    {"id": 2, "type": "f", "name": "angle_setpoint"},
    {"id": 3, "type": "f", "name": "speed_setpoint"},
    {"id": 4, "type": "f", "name": "flash_data.limits.max_dq_current"},
    {"id": 5, "type": "f", "name": "flash_data.limits.max_dq_voltage"},
    {"id": 6, "type": "f", "name": "flash_data.limits.vbus_overvoltage_trip_level"},
    {"id": 7, "type": "f", "name": "flash_data.limits.vbus_undervoltage_trip_level"},
    {"id": 8, "type": "f", "name": "flash_data.limits.ibus_overcurrent_trip_level"},
    {"id": 9, "type": "f", "name": "flash_data.limits.motor_temp_trip_level"},
    {"id": 10, "type": "f", "name": "flash_data.limits.mosfet_temp_trip_level"},
    {"id": 11, "type": "u32", "name": "flash_data.motor.pole_pairs"},
    {"id": 12, "type": "f", "name": "flash_data.motor.phase_resistance"},
    {"id": 13, "type": "f", "name": "flash_data.motor.phase_inductance"},
    {"id": 14, "type": "f", "name": "flash_data.motor.torque_constant"},
    {"id": 15, "type": "u32", "name": "flash_data.hfi.hfi_enabled"},
    {"id": 16, "type": "f", "name": "flash_data.hfi.injection_amplitude"},
    {"id": 17, "type": "f", "name": "flash_data.hfi.injection_omega"},
    {"id": 18, "type": "f", "name": "flash_data.controller.current_control_bandwidth"}
]


class FOCState(IntEnum):
    """FOC state identifiers shared with the firmware protocol."""

    FOC_STATE_NONE = 0
    FOC_STATE_INIT = 1
    FOC_STATE_RESET = 2
    FOC_STATE_BOOTUP_SOUND = 3
    FOC_STATE_CURRENT_SENSOR_CALIBRATION = 4
    FOC_STATE_IDENTIFY = 5
    FOC_STATE_ANTICOGGING = 6
    FOC_STATE_CHECKLIST = 7
    FOC_STATE_PID_AUTOTUNE = 8
    FOC_STATE_ERROR = 9
    FOC_STATE_ALIGNMENT = 10
    FOC_STATE_ALIGNMENT_TEST = 11
    FOC_STATE_RUN = 12
    FOC_STATE_STOP = 13
    FOC_STATE_IDLE = 14
    FOC_STATE_FLASH_SAVE = 15
    FOC_STATE_FLASH_LOAD = 16
    FOC_STATE_FLASH_CLEAR = 17
    FOC_STATE_BOOTLOADER = 18
    FOC_STATE_OPENLOOP = 19
    FOC_STATE_COUNT = 20


class CanCyclicIndex(IntEnum):
    """Cyclic CAN message identifiers shared with the driver protocol."""

    CAN_CYCLIC_HEARTBEAT = 0
    CAN_CYCLIC_ENCODER_ESTIMATES = 0x01
    CAN_CYCLIC_BUS_VOLTAGE_CURRENT = 0x02
    CAN_CYCLIC_TEMPERATURES = 0x03
    CAN_CYCLIC_TORQUE = 0x04
    CAN_CYCLIC_CURRENT = 0x05
    CAN_CYCLIC_SPEED = 0x06
    CAN_CYCLIC_POSITION = 0x07
    CAN_CYCLIC_ERRORS = 0x08
    CAN_CYCLIC_COUNT = 9


CAN_CYCLIC_LABELS = {
    CanCyclicIndex.CAN_CYCLIC_HEARTBEAT: "Heartbeat",
    CanCyclicIndex.CAN_CYCLIC_ENCODER_ESTIMATES: "Encoder estimates",
    CanCyclicIndex.CAN_CYCLIC_BUS_VOLTAGE_CURRENT: "Bus voltage / current",
    CanCyclicIndex.CAN_CYCLIC_TEMPERATURES: "Temperatures",
    CanCyclicIndex.CAN_CYCLIC_TORQUE: "Torque",
    CanCyclicIndex.CAN_CYCLIC_CURRENT: "Current",
    CanCyclicIndex.CAN_CYCLIC_SPEED: "Speed",
    CanCyclicIndex.CAN_CYCLIC_POSITION: "Position",
    CanCyclicIndex.CAN_CYCLIC_ERRORS: "Errors",
}


class ControlMode(IntEnum):
    """Control modes accepted by MSG_SET_CONTROL_MODE."""

    CONTROL_MODE_OPENLOOP = 0
    CONTROL_MODE_POSITION = 1
    CONTROL_MODE_SPEED = 2


CONTROL_MODE_LABELS = {
    ControlMode.CONTROL_MODE_OPENLOOP: "Open loop",
    ControlMode.CONTROL_MODE_POSITION: "Position",
    ControlMode.CONTROL_MODE_SPEED: "Speed",
}


class FOCError(IntFlag):
    """Driver error bits shared with the firmware error flags."""

    FOC_ERROR_INITIALIZING = 1 << 0
    FOC_ERROR_SYSTEM_FAULT = 1 << 1
    FOC_ERROR_TIMING_VIOLATION = 1 << 2
    FOC_ERROR_INVALID_CONFIGURATION = 1 << 3
    FOC_ERROR_DRIVER_FAULT = 1 << 4
    FOC_ERROR_MOTOR_OT = 1 << 5
    FOC_ERROR_MOTOR_UT = 1 << 6
    FOC_ERROR_MOTOR_DISCONNECTED = 1 << 7
    FOC_ERROR_MOTOR_STALL = 1 << 8
    FOC_ERROR_MOSFET_OT = 1 << 9
    FOC_ERROR_MOSFET_UT = 1 << 10
    FOC_ERROR_VBUS_OV = 1 << 11
    FOC_ERROR_VBUS_UV = 1 << 12
    FOC_ERROR_IBUS_OC = 1 << 13
    FOC_ERROR_ENCODER_FAULT = 1 << 14


# Keep user-facing descriptions alongside the bit definitions so the UI and
# protocol mapping stay in sync when firmware names or bit locations change.
FOC_ERROR_LABELS = {
    FOCError.FOC_ERROR_INITIALIZING: "Initializing",
    FOCError.FOC_ERROR_SYSTEM_FAULT: "System fault",
    FOCError.FOC_ERROR_TIMING_VIOLATION: "Timing violation",
    FOCError.FOC_ERROR_INVALID_CONFIGURATION: "Invalid configuration",
    FOCError.FOC_ERROR_DRIVER_FAULT: "Driver fault",
    FOCError.FOC_ERROR_MOTOR_OT: "Motor over-temperature",
    FOCError.FOC_ERROR_MOTOR_UT: "Motor under-temperature",
    FOCError.FOC_ERROR_MOTOR_DISCONNECTED: "Motor disconnected",
    FOCError.FOC_ERROR_MOTOR_STALL: "Motor stall",
    FOCError.FOC_ERROR_MOSFET_OT: "MOSFET over-temperature",
    FOCError.FOC_ERROR_MOSFET_UT: "MOSFET under-temperature",
    FOCError.FOC_ERROR_VBUS_OV: "VBUS over-voltage",
    FOCError.FOC_ERROR_VBUS_UV: "VBUS under-voltage",
    FOCError.FOC_ERROR_IBUS_OC: "IBUS over-current",
    FOCError.FOC_ERROR_ENCODER_FAULT: "Encoder fault",
}


class MsgType(IntEnum):
    MSG_GET_VERSION = 0x00  # PC -> FOC
    MSG_VERSION_REPLY = 0x01  # FOC -> PC
    MSG_ENTER_BOOTLOADER = 0x02  # PC -> FOC
    MSG_LOG_DATA = 0x03  # FOC -> PC
    MSG_SET_MASK = 0x04  # PC -> FOC
    MSG_GET_MASK = 0x05  # PC -> FOC
    MSG_MASK_REPLY = 0x06  # FOC -> PC
    MSG_START_LOG = 0x07  # PC -> FOC
    MSG_STOP_LOG = 0x08  # PC -> FOC
    MSG_SET_PID = 0x09  # PC -> FOC
    MSG_GET_PID = 0x0A  # PC -> FOC
    MSG_PID_REPLY = 0x0B  # FOC -> PC
    MSG_SET_VAR = 0x0C  # PC -> FOC
    MSG_GET_VAR = 0x0D  # PC -> FOC
    MSG_VAR_REPLY = 0x0E  # FOC -> PC
    MSG_FLASH_SAVE = 0x0F  # PC -> FOC
    MSG_FLASH_LOAD = 0x10  # PC -> FOC
    MSG_FLASH_CLEAR = 0x11  # PC -> FOC
    MSG_SET_STATE = 0x12  # PC -> FOC
    MSG_GET_STATE = 0x13  # PC -> FOC
    MSG_STATE_REPLY = 0x14  # FOC -> PC
    MSG_TEXT_COMMAND = 0x15  # PC -> FOC
    MSG_TEXT_REPLY = 0x16  # FOC -> PC
    MSG_SET_NODE_ID = 0x17  # PC -> FOC
    MSG_GET_NODE_ID = 0x18  # PC -> FOC
    MSG_NODE_ID_REPLY = 0x19  # FOC -> PC
    MSG_GET_ACTIVE_ERRORS = 0x1A  # PC -> FOC
    MSG_ACTIVE_ERRORS_REPLY = 0x1B  # FOC -> PC
    MSG_GET_LATCHED_ERRORS = 0x1C  # PC -> FOC
    MSG_LATCHED_ERRORS_REPLY = 0x1D  # FOC -> PC
    MSG_CLEAR_LATCHED_ERRORS = 0x1E  # PC -> FOC
    MSG_SET_CAN_CYCLIC_RATE = 0x1F  # PC -> FOC
    MSG_GET_CAN_CYCLIC_RATE = 0x20  # PC -> FOC
    MSG_CAN_CYCLIC_REPLY = 0x21  # FOC -> PC
    MSG_SET_CONTROL_MODE = 0x22  # PC -> FOC
    MSG_GET_CONTROL_MODE = 0x23  # PC -> FOC
    MSG_CONTROL_MODE_REPLY = 0x24  # FOC -> PC
    MSG_UNKNOWN_TYPE = 0xFA  # FOC -> PC
    MSG_INVALID_PAYLOAD = 0xFB  # FOC -> PC
    MSG_UNKNOWN_ID = 0xFC  # FOC -> PC
    MSG_BUFFER_OVERFLOW = 0xFD  # FOC -> PC
    MSG_ACK = 0xFE  # FOC -> PC
    MSG_ERROR = 0xFF  # FOC -> PC
