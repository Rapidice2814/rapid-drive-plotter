# Built-in motor presets

The plotter loads JSON files in this folder automatically and marks them
**(built-in)**. They are read-only in the plotter. Use **Save preset…** to create
an editable user copy; the built-in file is left intact. User presets live in
the separate user folder shown in Configuration. Built-in names and parameters
can be maintained by editing these JSON files directly and restarting the plotter.

## Legacy motor preset

`legacy-code-defaults.json` preserves the former motor constants, operating limits,
speed/position PID defaults, anti-cogging gains, bandwidth and calibration current.

The old resistance/inductance constants (0.2 ohm, 25 uH) were identification fit
seeds, not measured or initially loaded flash values. This preset uses those
constants as requested; identify the actual motor if they are not known values.

The old default d/q PID gains were zero and were calculated during startup PI
tuning. They remain zero in this preset: run current PI tuning after applying it
before requesting RUN. The new firmware does not permit RUN with unset current PI.

The old 8 V constant became `max_dq_voltage = 8 / sqrt(3)`, about 4.619 V;
the preset preserves that conversion. The torque constant is 8.27 / 380 Nm/A.

No copying into AppData is needed. Applying affects RAM; save to driver flash
separately. Calibration offsets/tables and driver hardware identity are excluded.

The file uses preset schema 2: motor/limit settings are in `parameters`, and
controller gains are in `pids`, keyed by registry name. Numerical values match
the previously generated legacy preset. PID writes use the dedicated PID API.
