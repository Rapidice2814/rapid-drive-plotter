BAUDRATE = 115200
TIMEOUT = 1


# The minimum time between sending commands.
COMMAND_RATE_LIMIT_S = 0.1
# Maximum time to wait for the reply to one command before reporting an error.
COMMAND_REPLY_TIMEOUT_S = 0.1
# "all" shows successful replies and errors; "errors" shows only errors.
VERBOSITY = "errors"

LOG_FREQ = 8000.0
LOG_PLOT_DECIMATION = 100
LOG_PLOT_MAX_POINTS = 800
LOG_PLOT_UPDATE_PERIOD_S = 0.1
# Width of the x-axis while auto-follow is enabled.
AUTO_FOLLOW_WINDOW_S = 5.0

LOG_BATCH_PACKETS = 20


log_isrunning = False
log_mask = 0
hdf5_initialized = False
plot_state = None
log_filename = None
