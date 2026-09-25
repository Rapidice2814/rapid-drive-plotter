from enum import IntEnum

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


class MsgType(IntEnum):
    MSG_GET_VERSION     = 0x00  # PC -> FOC
    MSG_VERSION_REPLY   = 0x01  # FOC -> PC
    MSG_LOG_DATA        = 0x02  # FOC -> PC
    MSG_SET_MASK        = 0x03  # PC -> FOC
    MSG_START_LOG       = 0x04  # PC -> FOC
    MSG_STOP_LOG        = 0x05  # PC -> FOC
    MSG_SET_PID         = 0x06  # PC -> FOC
    MSG_GET_PID         = 0x07  # PC -> FOC
    MSG_PID_REPLY       = 0x08  # FOC -> PC
    MSG_SET_VAR         = 0x09  # PC -> FOC
    MSG_GET_VAR         = 0x0A  # PC -> FOC
    MSG_VAR_REPLY       = 0x0B  # FOC -> PC
    MSG_FLASH_SAVE      = 0x0C  # PC -> FOC
    MSG_FLASH_LOAD      = 0x0D  # PC -> FOC
    MSG_SET_STATE       = 0x0E  # PC -> FOC
    MSG_GET_STATE       = 0x0F  # PC -> FOC
    MSG_STATE_REPLY     = 0x10  # FOC -> PC
    MSG_TEXT_COMMAND    = 0x11  # PC -> FOC
    MSG_TEXT_REPLY      = 0x12  # FOC -> PC
    MSG_ENDER_BOOTLOADER = 0x13  # PC -> FOC
    MSG_FLASH_CLEAR      = 0x14  # PC -> FOC

    MSG_UNKNOWN_TYPE    = 0xFA  # FOC -> PC
    MSG_INVALID_PAYLOAD = 0xFB  # FOC -> PC
    MSG_UNKNOWN_ID      = 0xFC  # FOC -> PC
    MSG_BUFFER_OVERFLOW = 0xFD  # FOC -> PC
    MSG_ACK             = 0xFE  # FOC -> PC
    MSG_ERROR           = 0xFF  # FOC -> PC