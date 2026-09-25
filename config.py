from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

SERIAL_BAUDRATE = 115200
SERIAL_TIMEOUT = 0.1

# Firmware-version ping cadence and reply deadline for driver health status.
VERSION_POLL_INTERVAL_SECONDS = 5.0
VERSION_REPLY_TIMEOUT_SECONDS = 2.0

# Current-state refresh cadence while a driver is connected.
STATE_POLL_INTERVAL_SECONDS = 1.0

# Refresh the available serial ports only while disconnected.
PORT_REFRESH_INTERVAL_SECONDS = 5.0

# Minimum spacing between commands sent to the driver. This limits bursts
# (for example, when reading all PID/variable values); set to 0 to disable.
COMMAND_RATE_LIMIT_SECONDS = 0.01

# Timestamp frequency of individual telemetry samples.
SAMPLE_RATE = 8000.0

# Set True to show the in-process motor-driver simulator in the connection list.
# Enable the simulator selector; selecting it never opens a serial port.
ENABLE_SIMULATOR = True
SIMULATOR_ENDPOINT = "__simulator__"
SIMULATOR_SAMPLES_PER_PACKET = 20

# HDF5 telemetry output directory (default: the project-root logs folder).
LOG_FOLDER = PROJECT_ROOT / "logs"
