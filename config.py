from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

SERIAL_BAUDRATE = 115200
SERIAL_TIMEOUT = 0.1

# Firmware-version ping cadence and reply deadline for driver health status.
VERSION_POLL_INTERVAL_SECONDS = 5.0
VERSION_REPLY_TIMEOUT_SECONDS = 2.0

# Current-state refresh cadence while a driver is connected.
STATE_POLL_INTERVAL_SECONDS = 1.0

# Refresh active and latched driver error flags while connected.
ERROR_POLL_INTERVAL_SECONDS = 1.0

# Refresh the selected control mode while the driver is in RUN.
CONTROL_MODE_POLL_INTERVAL_SECONDS = 1.0

# Refresh the available serial ports only while disconnected.
PORT_REFRESH_INTERVAL_SECONDS = 5.0

# Minimum spacing between commands sent to the driver. This limits bursts
# (for example, when reading all PID/variable values); set to 0 to disable.
COMMAND_RATE_LIMIT_SECONDS = 0.01

# CAN cyclic message types shown in the CAN tab. Add a (type ID, label) pair
# here when firmware adds another CAN_CyclicIndexTypeDef entry.
CAN_CYCLIC_RATE_OPTIONS = (
    (0x00, "Heartbeat"),
    (0x01, "Encoder estimates"),
    (0x02, "Bus voltage / current"),
    (0x03, "Temperatures"),
    (0x04, "Torque"),
    (0x05, "Current"),
    (0x06, "Speed"),
    (0x07, "Position"),
    (0x08, "Errors"),
)

# Timestamp frequency of individual telemetry samples.
SAMPLE_RATE = 8000.0

# Set True to show the in-process motor-driver simulator in the connection list.
# Enable the simulator selector; selecting it never opens a serial port.
ENABLE_SIMULATOR = True
SIMULATOR_ENDPOINT = "__simulator__"
SIMULATOR_SAMPLES_PER_PACKET = 20

# HDF5 telemetry output directory (default: the project-root logs folder).
LOG_FOLDER = PROJECT_ROOT / "logs"
