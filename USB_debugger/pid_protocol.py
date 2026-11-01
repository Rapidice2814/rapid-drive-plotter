"""Discovered PID bounds and optional-gain validation; firmware remains authoritative."""
import math
import struct

GAINS = ("kp", "ki", "kd")


def validate_pid_descriptor(entry):
    try:
        lo, hi = (struct.unpack("<f", struct.pack("<f", float(entry[key])))[0] for key in ("minimum", "maximum"))
    except (KeyError, ValueError, TypeError, OverflowError) as exc:
        raise ValueError("Invalid PID gain bounds") from exc
    if not math.isfinite(lo) or not math.isfinite(hi) or lo < 0 or lo > hi:
        raise ValueError("Invalid PID gain bounds")
    if type(entry.get("states")) is not int or not 0 < entry["states"] < 1 << 32:
        raise ValueError("Invalid PID write permissions")


def pack_gain(entry, value):
    if type(value) not in (int, float) or abs(value) > 1e100:
        raise ValueError("PID gains must be finite numbers")
    number, lo, hi = (struct.unpack("<f", struct.pack("<f", float(v)))[0]
                      for v in (value, entry["minimum"], entry["maximum"]))
    if not math.isfinite(number) or not lo <= number <= hi:
        raise ValueError(f"{entry['name']}: gain is outside advertised bounds")
    return struct.pack("<f", number)


def validate_partial_gains(entry, gains):
    if not isinstance(gains, dict) or any(key not in GAINS for key in gains):
        raise ValueError("PID gains must use kp, ki or kd")
    for value in gains.values():
        pack_gain(entry, value)
    return gains
