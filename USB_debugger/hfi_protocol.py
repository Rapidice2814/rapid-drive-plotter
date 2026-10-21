"""Experimental encoder-seeded HFI status; not an absolute sensorless encoder."""
from dataclasses import dataclass
import math
import struct

ENABLED, ACTIVE, SEEDED, LOCKED, VALID, SELECTED = 1, 2, 4, 8, 16, 32
LAYOUT = struct.Struct("<BBffff")


@dataclass(frozen=True)
class HfiInfo:
    source: int
    flags: int
    angle: float
    amplitude: float
    error: float
    speed: float

    @classmethod
    def decode(cls, payload):
        if len(payload) != LAYOUT.size:
            raise ValueError("Invalid HFI info length")
        info = cls(*LAYOUT.unpack(payload))
        if info.source not in (0, 1) or info.flags & ~63 or bool(info.flags & SELECTED) != bool(info.source):
            raise ValueError("Invalid HFI source/status")
        if any(not math.isfinite(v) for v in (info.angle, info.amplitude, info.error, info.speed)) or info.amplitude < 0:
            raise ValueError("Invalid HFI numeric status")
        return info
