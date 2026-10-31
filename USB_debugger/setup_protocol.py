"""Correlated terminal routine status used by the motor Setup workflow."""
from dataclasses import dataclass
import math
import struct
from protocol_definitions import FOCState

@dataclass(frozen=True)
class SetupStatus:
    sequence: int
    routine_state: int
    status: int
    error: int
    state: int
    active_errors: int
    latched_errors: int
    voltage: float

    @classmethod
    def decode(cls, payload):
        if len(payload) != 20:
            raise ValueError("Malformed setup status")
        value = cls(*struct.unpack("<IBBBB IIf", payload))
        if (value.routine_state >= FOCState.FOC_STATE_COUNT or value.state >= FOCState.FOC_STATE_COUNT or
                value.status > 3 or value.error > 7 or not math.isfinite(value.voltage) or value.voltage <= 0):
            raise ValueError("Invalid setup status fields")
        return value

    def encode(self):
        return struct.pack("<IBBBB IIf", self.sequence, self.routine_state, self.status, self.error,
                           self.state, self.active_errors, self.latched_errors, self.voltage)
