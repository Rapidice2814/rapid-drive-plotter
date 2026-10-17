# Rapid Drive Plotter

Rapid Drive Plotter is a desktop application for USB telemetry, signal plotting,
PID tuning, variable access, and HDF5 recording for Rapid Drive controllers.

## Installation

Python 3.10 or newer is required. Installation from the repository root:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install .
.venv\Scripts\rapid-drive-plotter
```

The application package includes the simulator and requires no firmware source
checkout. The application supports USB protocol version 3, introduced in
firmware 0.3.0. Earlier protocol command IDs are incompatible. Firmware version
polling uses device info; there are no separate USB version commands.

## Connection

Connection setup reads device info, stops logging, and discovers the signals,
variables, and PID controllers. Python contains no built-in lists for these.
The controls are built from the discovered definitions, then the selected mask
is set and read back before logging starts. Commands run one at a
time with a two-second reply deadline. Rejected runtime commands are reported
in the terminal without disconnecting. Initialization failures, receive-buffer
overflow, malformed replies, and timeouts stop the session and display the
reason in the title and terminal. Mask width and
the maximum selected signals come from the device. With the current firmware,
36 signals use a five-byte mask and at most eight can be selected at once.
The plot uses the sample rate reported by the device.

## Recording

HDF5 logs go to `%LOCALAPPDATA%\RapidDrive\logs` on Windows and the user data
directory on other platforms. `RAPID_DRIVE_LOG_DIR` overrides this location. Files
include protocol version, firmware version, sample rate, all three definition
lists, and their fingerprint.
An active recording starts a new file on reconnect.

## Definitions cache

Definitions are cached atomically under `%LOCALAPPDATA%\RapidDrive\cache` on
Windows, or `$XDG_CACHE_HOME/RapidDrive/cache` (default `~/.cache/RapidDrive/cache`)
on other platforms. `RAPID_DRIVE_CACHE_DIR` overrides this location.
Each connection checks device info first. A matching, valid cached definition
set avoids all definition-page requests, including after restarting the app.
Missing, corrupt, or changed definitions are downloaded and checked against
the driver's fingerprint before being cached. Unrelated firmware changes do
not invalidate the cache. Reinstalling may retain user cache files; clearing
this directory forces discovery.

Saved signal selections contain names only. On every connection, names are
resolved against that driver's definitions; missing names are ignored. IDs and
controls are cleared on disconnect. Names must be unique within each list;
sharing a name between a signal and a variable is allowed.

## Simulator

The simulator loads its example definitions and defaults from
`Serial_Simulator/definitions.json`. Its waveform and control behavior refer to
names, so fixture entries can be reordered without changing simulator code.

## Tests

Regression tests from the repository root:

```powershell
python -m unittest discover -s tests -v
python tests/smoke_app.py
```

The contract test compares the simulator fixture to the number-free C lists
and checks message IDs and protocol version against a sibling `rapid-drive-foc` checkout.
`RAPID_DRIVE_FIRMWARE` selects an alternative firmware checkout. The test is
skipped when firmware source is unavailable.

## Protocol compatibility

Firmware and plotter share a protocol version. Wire layout, discovery grammar,
or primitive encoding changes require a new protocol version in both repositories.
Adding, removing, or reordering list entries changes the fingerprint and does
not require a protocol bump. The firmware repository's `docs/usb-protocol.md`
documents the discovery wire format.
