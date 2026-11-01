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
checkout. The application requires USB protocol 14 and a matching firmware build.
Other protocol versions are rejected before discovery. Device info reports
firmware version, capabilities and all five definition catalogs.

## Connection

If the installed firmware has a different protocol version, select its physical
serial port and use **Enter bootloader** below **Disconnect**. This action is
available without a successful connection or device discovery. It closes the
normal connection, stops logging, requests STOP, waits for IDLE/ERROR, and sends
the bootloader request over the selected port. The status shows acceptance,
rejection or timeout. Connect again after flashing matching firmware. The
recovery action is unavailable for the simulator.


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
The firmware's flash format 3 accepts only the current record layout. After a
format change, reapply a motor preset and recalibrate the driver.

Connection setup reads device info, stops logging, and discovers the signals,
variables, PID controllers, and CAN cyclic messages. Python contains
no built-in device catalogs for these.
Discovery also includes configuration groups, field types, bounds, units and
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
diagnostics, and responses to manually entered text commands. Text commands
remain available in the Command Line panel for development builds.

The fault displays include the shared Operation failed flag (bit 15,
0x00008000) for failed calibration, tuning, identification, and storage actions.
Clearing latched faults returns ERROR to STOP only when active faults are also
clear; a present fault immediately relatches. Recovery does not resume RUN.

## Production configuration and debugging

The Configuration tab generates grouped forms from the connected driver's
metadata. Current groups are Limits, Motor, Controller, Encoder calibration
D current. PID gains use their dedicated discovered registry and commands. Use
**Read from driver**, edit values, then **Apply values to RAM**. These global
actions cover all configuration groups and PID controllers. The firmware
validates the complete candidate group and applies all edits
or none; rejection retains the local edits and reports the error in the terminal.
Successful writes are read back. Saving to flash remains a separate explicit
action in Basics/Calibration. Configuration changes can invalidate calibration
tables; changing pole pairs/direction also invalidates encoder alignment/correction.
Current-control bandwidth changes do not automatically retune the current PI gains.

Local values can be edited, reset, and saved to presets in every motor state.
Applying to RAM is blocked in RUN and other active states: limits allow
stationary IDLE/ERROR, while other groups and PID gains require stationary IDLE.
The driver's advertised permissions remain authoritative. Hover over the
disabled Apply button for an explanation. Absolute bounds are defined by
`CONFIG_ABS_*` in the firmware's `FOC_Config.h`, not a Python settings catalog.
Firmware checks state, finite/native values, physical bounds and related-field
constraints. Advertised ranges do not guarantee controller tuning stability.

Setpoints use dedicated validated target/readback commands and do
not write debug variables. Target writes require fault-free RUN and the matching
control mode; d/q current magnitude must fit the configured current limit. PID
updates retain their dedicated command with firmware-side gain/state validation.
Existing node-ID, CAN rate, encoder, calibration and storage controls remain
dedicated operations.

Variables is a debug-only view. Raw writes bypass functional validation in
development builds. Non-debug firmware disables MSG_SET_VAR and text commands
by default; it advertises this capability and the plotter disables the
write controls. Debug reads and telemetry remain available. Production controls
must not use the Variables panel as an alternate path around validation.
The current driver exposes no debug-only variables. Functional settings and
targets use Configuration and dedicated commands. The Variables tab
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

The plotter supports only USB protocol 14, with one device-info layout and five
catalogs. Other versions are rejected; there are no backward compatibility paths.
Firmware and plotter must be updated together when the protocol changes.
Adding, removing, or reordering list entries changes the fingerprint and does
not require a protocol bump. The firmware repository's `docs/usb-protocol.md`
documents the discovery wire format.

The CAN tab starts empty and is populated from the driver's CAN cyclic ID/name
list. The same startup pagination, validation and on-disk fingerprint cache used
for signals/variables/PIDs also covers this list. New entries, renames or ordering
changes invalidate the cache and are discovered on reconnect without editing
plotter source. The simulator's catalog lives in `Serial_Simulator/definitions.json`,
not in production Python constants. Period tooltips use the reported driver tick
rate; zero disables a cyclic message. A driver advertising an empty CAN list shows an explanatory message while node-ID controls remain usable.

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

## Motor / model presets

Configuration (also inside Motor Setup) includes a preset dropdown. Selecting a
preset loads it directly into the editor. **Read from driver** fills the editor
with all current RAM settings and PID gains without saving a file. **Save preset…**
saves editor values to an existing user preset or creates a new user preset;
the dialog lets you choose the destination, name, and optional fields. Motor parameters,
all current/voltage/bus/temperature limits, bandwidth, calibration current and
all five PID gain sets can be included. Scalar settings are stored under
`parameters`; PID gains are stored under `pids`, keyed by controller name
and `kp`/`ki`/`kd`. Each PID controller is optional as a whole: the save dialog
has one checkbox per controller and saves Kp, Ki and Kd together. Unsaved editor values are
included when saving; saving a preset does not read or write the driver.

The selected preset is the comparison reference. Orange names indicate values
that differ from that preset, including after **Read from driver**. Controller
names also turn orange when any of their gains differ, with a reset arrow next
to the name to restore all gains. Individual gain reset arrows remain available.
Names of fields and PID controllers omitted from the preset are grey; loading the preset leaves those values
unchanged. This applies to each individual PID gain, including explicit zero
gains, which are included rather than omitted. Omitted fields remain editable.

The reset arrow restores an included field's preset value in the editor.
For an omitted field, it undoes a local edit using the last read/loaded value.
Without a selected preset, comparisons and resets use the last read/loaded values.
Reading or choosing another preset asks before replacing local edits; differences
from a preset alone do not trigger that confirmation. The Apply button reflects
pending changes to driver RAM independently of the preset comparison colors.

Saving updates the selected comparison reference to the saved preset. Omitted
scalar fields and PID controllers stay unchecked unless explicitly edited locally
or checked there. A selected PID always saves its complete gain set; all three
values must be known. Existing partial PID objects can still be loaded, but saving
their controller includes the remaining editor gains as well. Reading the driver
does not automatically add unrelated fields or controllers to the preset.

Review the settings, then **Apply values to RAM**. There are no per-group
read/apply buttons in Configuration. Omitted fields retain their driver values. Unknown
keys are reported and skipped. Application is sequential and atomic per group;
a failure stops later writes, while earlier accepted groups remain in RAM.
For each included PID, the plotter reads current gains, merges only supplied
values, writes the complete set, and verifies readback. Omitted gains stay
unchanged; explicit zero clears that gain. Use the driver's existing Save to
Flash action to persist its settings.

The dropdown includes two preset sources:

- Built-in templates from the project's `motor-presets` folder, marked **(built-in)**.
  These can be loaded and edited, but the plotter cannot overwrite their files.
  Saving from a built-in template defaults to a new user preset named **(custom)**.
- Writable user presets in a separate persistent folder:
`%LOCALAPPDATA%/RapidDrive/motor-presets` on Windows, or
`$XDG_DATA_HOME/RapidDrive/motor-presets` (`~/.local/share` fallback) elsewhere.

Set `RAPID_DRIVE_PRESET_DIR` to choose another user folder; it must be outside the
built-in folder. The UI displays the user folder path. Existing user copies remain
separate and editable, even if they share a name with a built-in preset. Editing
a built-in JSON file directly takes effect when the plotter restarts. Installed
wheels include the built-in templates under the Python environment's
`share/rapid-drive-plotter/motor-presets` folder.
The offline Save preset dialog accepts an object containing `parameters` and/or
`pids`; connect a driver for the discovered optional-field form.

Presets store stable parameter names, not session IDs. Every parameter is
optional; zero clears a numeric setting. Generic firmware keeps motor output
disabled until an operation's required settings are configured or measured.
Zero never disables an operating protection. Only current-format driver flash records are accepted; reapply a preset and
recalibrate after a format change. The simulator starts with a commissioned
example configuration for UI/protocol demonstrations.

Encoder hardware selection, per-device calibration tables/offsets, CAN identity
and live targets stay in their dedicated controls rather than model presets.

Preset files use schema version 2; earlier layouts are not loaded. PID registry
metadata advertises shared gain bounds and writable states, included in discovery
fingerprints. The PID tab allows local editing and reading in any connected
state, while applying gains requires IDLE and the driver's permission. Configuration and
the PID Controllers tab use the same PID editor and GET_PID/SET_PID workflow.
The PID Controllers tab has **Read PID gains** and **Apply edited gains to RAM**;
Configuration uses its global actions for the same editor. Omitted preset gains
stay unchanged and are merged from a fresh driver read at apply time. Every
write is verified by another read. Applying to RAM does not save a preset or
persist settings to driver flash.
