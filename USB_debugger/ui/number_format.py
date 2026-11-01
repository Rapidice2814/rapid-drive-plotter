"""Readable decimal text; formatting never changes the driver's float32 value."""
from decimal import Decimal
import math
import struct


def decimal_text(value, digits=None, *, float32=False, signed=False):
    if isinstance(value, bool):
        value = int(value)
    if not math.isfinite(float(value)):
        return str(value)
    if float32:
        original = struct.pack("<f", float(value))
        for precision in range(1, 10):
            candidate = f"{float(value):.{precision}g}"
            try:
                if struct.pack("<f", float(candidate)) == original:
                    break
            except OverflowError:
                continue
    else:
        candidate = f"{value:.{digits}g}" if digits is not None else str(value)
    text = format(Decimal(candidate), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if signed and not text.startswith("-"):
        text = "+" + text
    return text
