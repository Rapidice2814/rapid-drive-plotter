"""Shared calibration transfer layouts (all scalars little-endian)."""
from dataclasses import dataclass
import math
import struct

CHUNK = 12
ERROR_NAMES = ("No error", "Not started", "Invalid stage", "Invalid input", "Timed out",
               "Encoder error", "Current-sense error", "Invalid result")
STAGES = (("Not started", "Locking rotor", "Moving", "Measuring", "Validating", "Saving", "Complete", "Failed", "Cancelled"),
          ("Not started", "Starting", "Positioning", "Measuring", "Reporting", "Finishing", "Complete", "Failed", "Cancelled"))


@dataclass(frozen=True)
class CalibrationInfo:
    kind: int
    flags: int
    stage: int
    error: int
    progress: int
    revolutions: int
    bins: int
    channels: int

    @classmethod
    def decode(cls, data: bytes):
        if len(data) != 10:
            raise ValueError("Invalid calibration info length")
        value = cls(*struct.unpack("<BBBBHBHB", data))
        if (value.kind not in (0, 1) or value.flags & ~15 or value.stage > 8 or value.error > 7 or
                value.progress > 1000 or not 1 <= value.revolutions <= 8 or not 1 <= value.bins <= 4096 or
                value.channels != value.kind + 1):
            raise ValueError("Invalid calibration info")
        return value

    def encode(self):
        data = struct.pack("<BBBBHBHB", self.kind, self.flags, self.stage, self.error, self.progress,
                           self.revolutions, self.bins, self.channels)
        self.decode(data)
        return data


@dataclass(frozen=True)
class CalibrationTable:
    kind: int
    offset: int
    total: int
    values: tuple[float, ...]

    @classmethod
    def decode(cls, data: bytes):
        if len(data) < 6:
            raise ValueError("Truncated calibration table")
        kind, offset, total, count = struct.unpack_from("<BHHB", data)
        if kind not in (0, 1) or not 1 <= count <= CHUNK or not 1 <= total <= 8192 or offset + count > total or len(data) != 6 + 4 * count:
            raise ValueError("Invalid calibration table range")
        values = struct.unpack_from(f"<{count}f", data, 6)
        if not all(math.isfinite(v) for v in values):
            raise ValueError("Non-finite calibration value")
        return cls(kind, offset, total, values)

    def encode(self):
        data = struct.pack("<BHHB", self.kind, self.offset, self.total, len(self.values)) + struct.pack(f"<{len(self.values)}f", *self.values)
        self.decode(data)
        return data
