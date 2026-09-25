# Rapid Drive Plotter

## Simulator test mode

The in-process motor-driver simulator is disabled by default. To make it appear in the USB debugger's connection selector, set `ENABLE_SIMULATOR = True` in `USB_debugger/config.py` and restart the application. Select **Simulator (Test Mode)** instead of a COM port to connect without opening a serial device.

The simulator uses a worker thread and the debugger's existing framed protocol path. It produces synthetic motor telemetry, accepts signal-mask/logging requests, reads and stores PID and variable values, acknowledges commands, and responds to the text-command panel. Set `ENABLE_SIMULATOR = False` to hide the selector option again.

Run the simulator unit tests from the project root with:

```bash
python3 -m unittest discover -s tests -v
```
