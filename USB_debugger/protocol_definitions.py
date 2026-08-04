from enum import IntEnum

SOF1 = 0xAA
SOF2 = 0x55


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
    {"bit": 12, "type": "f",     "name": "phase_voltage.a"},
    {"bit": 13, "type": "f",     "name": "phase_voltage.b"},
    {"bit": 14, "type": "f",     "name": "phase_voltage.c"},
    {"bit": 15, "type": "f",     "name": "ab_voltage.alpha"},

    {"bit": 16, "type": "f",     "name": "ab_voltage.beta"},
    {"bit": 17, "type": "f",     "name": "dq_voltage.d"},
    {"bit": 18, "type": "f",     "name": "dq_voltage.q"},
    {"bit": 19, "type": "f",     "name": "encoder_angle_mechanical"},
    {"bit": 20, "type": "f",     "name": "encoder_speed_mechanical"},
    {"bit": 21, "type": "f",     "name": "encoder_angle_electrical"},
    {"bit": 22, "type": "f",     "name": "encoder_speed_electrical"},
    {"bit": 23, "type": "f",     "name": "dq_current_setpoint.d"},
    
    {"bit": 24, "type": "f",     "name": "dq_current_setpoint.q"},
    {"bit": 25, "type": "f",     "name": "angle_setpoint"},
    {"bit": 26, "type": "f",     "name": "speed_setpoint"},
    {"bit": 27, "type": "u32",   "name": "execution_time.loop_max"},
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
]


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

    MSG_UNKNOWN_TYPE    = 0xFA  # FOC -> PC
    MSG_INVALID_PAYLOAD = 0xFB  # FOC -> PC
    MSG_UNKNOWN_ID      = 0xFC  # FOC -> PC
    MSG_BUFFER_OVERFLOW = 0xFD  # FOC -> PC
    MSG_ACK             = 0xFE  # FOC -> PC
    MSG_ERROR           = 0xFF  # FOC -> PC