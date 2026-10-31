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
checkout. The application supports USB protocol versions 6, 7, 8 and 9. It retains protocol
3 command IDs and successful reply layouts, but uses structured errors containing
the originating command, numeric reason, and driver-provided explanation.
CAN catalog discovery requires version 7; protocol-6 connections remain supported
without guessed CAN cyclic entries. Firmware version polling uses
device info; there are no separate USB version commands.

## Connection

Calibration lists the driver's available angle sources, correction status and
calibration progress. Source changes require stationary IDLE/ERROR and invalidate
alignment and anti-cogging. Encoder calibration requires aligned,
fault-free IDLE and confirmation that the unloaded shaft can rotate. STOP cancels
measurement; success saves settings and requires anti-cogging recalibration.
Angle sources are discovered from the driver. Current firmware offers internal and
external AS5047P, each via quadrature or SPI DMA. Source IDs are saved in flash;
select a source in Calibration, align it, then save settings to persist the choice.
External quadrature uses the shared ENC_A/ENC_B inputs (PB4/PB5); only one sensor
may drive these pins.
The firmware's flash format 2 rejects older settings records rather than
interpreting their old layout. Back up settings before a firmware upgrade.

Connection setup reads device info, stops logging, and discovers the signals,
variables, PID controllers, and CAN cyclic messages (protocol 7). Python contains
no built-in device catalogs for these.
Protocol 8 also discovers configuration groups, field types, bounds, units and
write-state permissions. This metadata is included in the persistent cache and
fingerprint; changing a driver-side bound refreshes the generated controls.
The controls are built from the discovered definitions, then the selected mask
is set and read back before logging starts. Commands run one at a
time with a two-second reply deadline. Rejected runtime commands are reported
in the terminal with the error name, numeric code, and driver message, without
disconnecting. Each error is checked against the outstanding command.
Initialization failures, receive-buffer overflow, malformed replies, and
timeouts stop the session and display the reason in the title and terminal.
Mask width and the maximum selected signals come from the device. With the current firmware,
36 signals use a five-byte mask and at most eight can be selected at once.
The plot uses the sample rate reported by the device.

Command buttons display “Working…” and prevent repeat clicks while their
requests are queued or awaiting replies. Batch actions remain busy until all
replies arrive; mask changes include read-back confirmation. Completion,
rejection, cancellation, and disconnect clear the indicator while preserving
state-based availability. Flash command completion still means acceptance,
not completion of the flash operation itself.

Routine ACKs and PID/variable reply dumps are not printed in the terminal.
Structured errors and driver text remain visible, including warnings,
diagnostics, and responses to manually entered text commands.

The fault displays include the shared Operation failed flag (bit 15,
0x00008000) for failed calibration, tuning, identification, and storage actions.
Clearing latched faults returns ERROR to STOP only when active faults are also
clear; a present fault immediately relatches. Recovery does not resume RUN.

## Production configuration and debugging

The Configuration tab generates grouped forms from the connected driver's
metadata. Current groups are Limits, Motor, Controller, and Encoder
calibration D current. Read a group, edit one or more fields, then apply its edits
to RAM. The firmware validates the complete candidate group and applies all edits
or none; rejection retains the local edits and reports the error in the terminal.
Successful writes are read back. Saving to flash remains a separate explicit
action in Basics/Calibration. Configuration changes can invalidate calibration
tables; changing pole pairs/direction also invalidates encoder alignment/correction.
Current-control bandwidth changes do not automatically retune the current PI gains.

Limits allow stationary IDLE/ERROR; other groups require stationary IDLE. Hover
over disabled controls for an explanation. Absolute bounds are defined by
`CONFIG_ABS_*` in the firmware's `FOC_Config.h`, not a Python settings catalog.
Firmware checks state, finite/native values, physical bounds and related-field
constraints. Advertised ranges do not guarantee controller tuning stability.

Setpoints use dedicated validated target/readback commands on protocol 8 and do
not write debug variables. Target writes require fault-free RUN and the matching
control mode; d/q current magnitude must fit the configured current limit. PID
updates retain their dedicated command with firmware-side gain/state validation.
Existing node-ID, CAN rate, encoder, calibration and storage controls remain
dedicated operations. Older firmware can connect, but validated configuration
and target writes are unavailable; the plotter does not fall back to unsafe raw
writes for normal target operation.

Variables is a debug-only view. Raw writes bypass functional validation in
development builds. Non-debug firmware disables MSG_SET_VAR and legacy text
commands by default; it advertises this capability and the plotter disables the
write controls. Debug reads and telemetry remain available. Production controls
must not use the Variables panel as an alternate path around validation.
The current driver exposes no debug-only variables; its former 19 entries are
covered by Configuration and dedicated target commands. The Variables tab
explains this empty state and remains available for future debug fields.
Calibration parameter readbacks use discovered configuration fields, and
Setpoints/target telemetry do not depend on debug-variable definitions.

## Recording

HDF5 logs go to `%LOCALAPPDATA%\RapidDrive\logs` on Windows and the user data
directory on other platforms. `RAPID_DRIVE_LOG_DIR` overrides this location. Files
include the connected device's protocol version, firmware version, sample rate, all discovered definition
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

## Calibration tab

The right-side Calibration tab contains encoder source selection and Motor setup,
Encoder and Anti-cogging pages. Motor setup provides individual encoder direction
detection, encoder alignment, motor phase resistance/inductance measurement, and
d/q current PI tuning. Current-sensor calibration remains part of firmware startup;
there is no manual current-sensor calibration action on this tab. The startup
checklist is not exposed as a calibration action because it can continue into RUN.

Motor setup requires fault-free IDLE, no table transfer/upload, and explicit
confirmation. Pole pairs must be configured before direction detection. Current
PI tuning uses motor R/L and current-control bandwidth; it does not tune speed or
position controllers. Motor parameters and current gains can be read in the same
page using IDs resolved from the connected driver's definitions. Unsupported
readbacks are marked as unavailable. Parameter editing remains in Variables/PID
Controllers. These setup routines show the driver state, not percentage progress:
an ACK only confirms acceptance, and STOP/IDLE alone does not prove success.
Detailed routine results and errors appear in the terminal. Stop requests firmware
STOP without entering RUN; it is not a hardware emergency stop. Setup changes
remain in RAM until saved to flash.

Encoder and anti-cogging routines support 1-8 revolutions per direction
(default one), percentage/stage reporting and compensation enable/disable.
Calibration rotates the unloaded motor; STOP cancels it.

Both routines leave newly calibrated compensation disabled. Click the enable
checkbox to send the setting immediately; no separate Apply button is needed.
Driver status updates do not send enable commands, and rejected changes revert
the checkbox. Encoder calibration also saves its table with correction disabled.

Read table downloads the accepted driver table. Radial plots show deviation
from a solid reference circle: positive outside, negative inside, with 0 radians
at the right and angles increasing counterclockwise. Encoder deviation is inferred
measurement error (negative correction), exaggerated radially around an ideal
circle; it is not the magnet's physical shape or a raw sweep recording. Encoder
bins use raw sensor angle. Anti-cogging shows forward/reverse current in amperes
at aligned mechanical angle, sharing one scale around a zero-current circle.
Default ranges are fixed at +/-0.02 rad and +/-1 A, respectively, so small
errors look smaller across motors. Ranges expand by factors of two only when
needed and return to the default when values fit; the current range is always
displayed. Compare ranges as well as RMS/peak values between motors.
Numeric cells are editable; select a cell or plotted point and drag the yellow
handle radially to edit its value. Encoder cells still contain correction radians,
so outward dragging decreases correction. RMS and peak summarize table
magnitudes, not independently measured residual errors.

The Calibration tab's **Last test / calibration result** area shows the retained
firmware reason, stage and fault flags for encoder calibration, alignment,
direction testing, R/L identification, PI tuning, current-sensor calibration and
anti-cogging. It refreshes on IDLE/ERROR; **Read result and encoder diagnostics**
retries manually. Results survive a USB reconnect, but not a device reboot.
With updated firmware, stopped-motor readback also checks AS5047P magnetic-field
flags: MAGL = weak field, MAGH = strong field, COF = unreliable angle. This is a
snapshot when read, not proof of the field at the earlier failure instant.
The simulator reports hardware diagnostics as unavailable.

Apply edited table to RAM uploads acknowledged chunks with CRC validation.
Successful uploads leave compensation disabled; activation is explicit. Encoder
tables must preserve a monotonic periodic angle mapping; anti-cogging values are
bounded by the driver's current limit. An interrupted anti-cogging upload leaves
its in-place table invalid; Abort unfinished table upload releases an abandoned
transfer after reconnect. Transfers require stationary IDLE/ERROR.

Save all settings and tables to flash is separate from editing/uploading.
Encoder measurement still saves all settings automatically on success;
anti-cogging measurement does not. Encoder coordinate changes invalidate
anti-cogging. Reconnect clears downloaded snapshots/local edits.

## Tests

Regression tests from the repository root:

```powershell
python -m unittest discover -s tests -v
python tests/smoke_app.py
```

Static type checking uses Pyright with the project's Python 3.10 baseline:

```powershell
python -m pip install -e ".[dev]"
python -m pyright
```

Run the checker in the same Python environment as the application dependencies.

The contract test compares the simulator fixture to the number-free C lists
and checks message IDs and protocol version against a sibling `rapid-drive-foc` checkout.
`RAPID_DRIVE_FIRMWARE` selects an alternative firmware checkout. The test is
skipped when firmware source is unavailable.

## Protocol compatibility

Flash save/load/clear and bootloader controls are available in IDLE or ERROR.
These recovery actions do not acknowledge faults. Firmware keeps motor outputs
disabled during maintenance; flash actions from ERROR return to ERROR while
faults remain, rather than resuming RUN. The simulator supports the same
ERROR-state permissions and preserves its active/latched fault masks.

The plotter supports protocols 6 through 11. Protocol 6 has three catalogs,
protocol 7 adds CAN cyclic discovery, and protocols 8–11 include validated
configuration and targets. Protocol 11 uses the physical encoder source selector;
retired command IDs 0x3C–0x3F are not issued. Unknown protocols are rejected. Wire layout, discovery
grammar, or primitive encoding changes require explicit protocol support;
firmware release/version numbers alone do not determine compatibility.
Adding, removing, or reordering list entries changes the fingerprint and does
not require a protocol bump. The firmware repository's `docs/usb-protocol.md`
documents the discovery wire format.

The CAN tab starts empty and is populated from the driver's CAN cyclic ID/name
list. The same startup pagination, validation and on-disk fingerprint cache used
for signals/variables/PIDs also covers this list. New entries, renames or ordering
changes invalidate the cache and are discovered on reconnect without editing
plotter source. The simulator's catalog lives in `Serial_Simulator/definitions.json`,
not in production Python constants. Period tooltips use the reported driver tick
rate; zero disables a cyclic message. A legacy driver or a driver advertising an
empty CAN list shows an explanatory message while node-ID controls remain usable.

Calibration progress bars include a plotter-only observed elapsed time and
remaining-time estimate. It extrapolates existing progress against the local
monotonic clock, waits for enough progress, and marks the estimate uncertain
when progress stalls. It needs no additional driver command. Reconnecting
starts a new observation interval; it cannot recover elapsed time before the
connection. Completed/failed elapsed time is frozen until a new run starts.

Anti-cogging uses dedicated configurable PID gains, continuous targets and
100 ms current-averaging windows with movement/noise/saturation checks. Configure Kp/Ki/Kd in Calibration > Anti-cogging (defaults 10/20/0.2), click
Apply anti-cogging gains in IDLE, and save settings to flash to retain them.
Normal position gains are independent. All angle graphs, radial labels and table bins use radians.
Forward/reverse disagreement is shown after downloading the table.

Encoder calibration now regulates D current through the normal current loop
using a generated transform angle, while reading the selected physical encoder.
Calibration > Encoder provides **Calibration D current**, **Read current** and
**Apply D current** controls (default 0.25 A). Apply in fault-free IDLE; save
settings to retain it. Current PI gains must be configured first. The source
list also includes **Internal simulator**; it is for virtual-angle tests and
cannot be selected as the physical measurement for encoder calibration.

## New motor Setup

The Setup tab puts duplicated manual configuration controls first, then
runs selected operations in order: combined encoder direction/pole-pair detection,
encoder alignment, L/R measurement, current PI tuning, encoder calibration and
optional anti-cogging. All steps default selected except anti-cogging. Individual
Run step buttons are also available. Apply/read back manual limits and excitation
settings first. Detection/alignment voltage defaults to 0.5 V and is RAM-only.

The sequence waits for a fresh, matching driver success result and fault-free
IDLE, rather than advancing on ACK or a stale result. Any rejection, fault,
cancellation, unexpected result or disconnect stops it; reconnection never
resumes motion. Stop and Clear faults controls are available at the top. Current
calibration progress/ETA is shown while those steps run. Firmware protocol 12 is
required. New tables remain disabled until explicitly enabled, and anti-cogging
results still need a separate flash save. Enabling encoder correction after
anti-cogging invalidates that table; rerun anti-cogging in the desired coordinates.
