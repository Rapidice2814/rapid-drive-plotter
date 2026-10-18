"""Generic discovered configuration metadata and scalar transaction encoding."""
import math
import struct


def validate_descriptor(entry):
    """Reject malformed metadata before building controls or accepting a cache."""
    if entry.get("type") not in ("f", "u32", "i32", "bool", "enum"):
        raise ValueError("Unsupported configuration type")
    if type(entry.get("group_id")) is not int or not 0 <= entry["group_id"] <= 255:
        raise ValueError("Invalid configuration group ID")
    if type(entry.get("states")) is not int or not 0 < entry["states"] < 1 << 32:
        raise ValueError("Invalid configuration permissions")
    for key in ("group", "label", "unit"):
        value = entry.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > 127 or any(ord(c) < 32 or ord(c) > 126 for c in value):
            raise ValueError("Invalid configuration display metadata")
    lo, hi = float(entry["minimum"]), float(entry["maximum"])
    if not math.isfinite(lo) or not math.isfinite(hi) or lo > hi:
        raise ValueError("Invalid configuration bounds")
    if entry["type"] in ("u32", "i32", "bool", "enum"):
        if not lo.is_integer() or not hi.is_integer() or lo < (-2147483648 if entry["type"] == "i32" else 0) or hi > (2147483647 if entry["type"] == "i32" else 4294967295):
            raise ValueError("Invalid integral configuration bounds")
    if entry["type"] == "bool" and (lo, hi) != (0, 1):
        raise ValueError("Invalid boolean bounds")
    if entry["type"] == "enum" and not entry.get("choices"):
        raise ValueError("Enum configuration needs advertised choices")


def pack_value(entry, value):
    number = float(value)
    lo, hi = float(entry["minimum"]), float(entry["maximum"])
    if entry["type"] == "f":
        number, lo, hi = (struct.unpack("<f", struct.pack("<f", v))[0] for v in (number, lo, hi))
    if not math.isfinite(number) or not lo <= number <= hi:
        raise ValueError(f"{entry['label']} is outside the advertised range")
    if entry["type"] != "f" and number != int(number):
        raise ValueError("Integer configuration value required")
    return struct.pack("<f" if entry["type"] == "f" else "<i" if entry["type"] == "i32" else "<I", value if entry["type"] == "f" else int(value))


def unpack_value(entry, raw):
    return struct.unpack("<f" if entry["type"] == "f" else "<i" if entry["type"] == "i32" else "<I", raw)[0]


def encode_configuration(entries, group, values):
    """Encode one bounded, unique group transaction using the discovered catalog."""
    fields = {entry["id"]: entry for entry in entries if entry["group_id"] == group}
    if not fields or not values or len(values) > 48 or any(identifier not in fields for identifier in values):
        raise ValueError("Unknown group/field or empty configuration transaction")
    return bytes((group, len(values))) + b"".join(bytes((identifier,)) + pack_value(fields[identifier], value) for identifier, value in values.items())


def decode_configuration(entries, payload):
    if len(payload) < 2 or len(payload) != 2 + 5 * payload[1]:
        raise ValueError("Malformed configuration reply")
    group = payload[0]
    fields = {entry["id"]: entry for entry in entries if entry["group_id"] == group}
    values = {}
    for offset in range(2, len(payload), 5):
        identifier = payload[offset]
        if identifier not in fields or identifier in values:
            raise ValueError("Unknown or duplicate configuration reply field")
        values[identifier] = unpack_value(fields[identifier], payload[offset + 1:offset + 5])
    if set(values) != set(fields) or not fields:
        raise ValueError("Incomplete configuration group reply")
    return group, values
