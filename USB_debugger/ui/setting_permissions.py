"""Local editing is unrestricted by motor state; applying requires a safe state."""
from protocol_definitions import FOCState


def can_apply_setting(connected, state, entry):
    return (connected and state in (FOCState.FOC_STATE_IDLE, FOCState.FOC_STATE_ERROR)
            and bool(entry["states"] & (1 << int(state))))
