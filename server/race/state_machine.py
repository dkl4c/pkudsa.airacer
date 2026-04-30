"""
Thread-safe state machine for AI Racer competition phases.
Supports per-zone independent state machines; the module-level
`state_machine` is kept for backward compatibility (zone "default").
"""

import threading
from enum import Enum


class RaceState(str, Enum):
    IDLE                 = "IDLE"
    QUALIFYING_RUNNING   = "QUALIFYING_RUNNING"
    QUALIFYING_FINISHED  = "QUALIFYING_FINISHED"
    QUALIFYING_ABORTED   = "QUALIFYING_ABORTED"
    QUALIFYING_DONE      = "QUALIFYING_DONE"
    GROUP_RACE_RUNNING   = "GROUP_RACE_RUNNING"
    GROUP_RACE_FINISHED  = "GROUP_RACE_FINISHED"
    GROUP_RACE_ABORTED   = "GROUP_RACE_ABORTED"
    GROUP_DONE           = "GROUP_DONE"
    SEMI_RUNNING         = "SEMI_RUNNING"
    SEMI_FINISHED        = "SEMI_FINISHED"
    SEMI_ABORTED         = "SEMI_ABORTED"
    SEMI_DONE            = "SEMI_DONE"
    FINAL_RUNNING        = "FINAL_RUNNING"
    FINAL_FINISHED       = "FINAL_FINISHED"
    CLOSED               = "CLOSED"


# Any state can transition to IDLE (reset-track).
# The dict below lists non-IDLE legal targets from each source.
_ALLOWED_NON_IDLE: dict[RaceState, set[RaceState]] = {
    RaceState.IDLE: {
        RaceState.QUALIFYING_RUNNING,
        RaceState.GROUP_RACE_RUNNING,
        RaceState.SEMI_RUNNING,
        RaceState.FINAL_RUNNING,
    },
    RaceState.QUALIFYING_RUNNING: {
        RaceState.QUALIFYING_FINISHED,
        RaceState.QUALIFYING_ABORTED,
    },
    RaceState.QUALIFYING_FINISHED: {
        RaceState.QUALIFYING_DONE,
        RaceState.QUALIFYING_RUNNING,
    },
    RaceState.QUALIFYING_ABORTED: {
        RaceState.QUALIFYING_DONE,
        RaceState.QUALIFYING_RUNNING,
    },
    RaceState.QUALIFYING_DONE: {
        RaceState.GROUP_RACE_RUNNING,
        RaceState.SEMI_RUNNING,   # small zones may skip group_race
        RaceState.FINAL_RUNNING,  # tiny zones go straight to final
    },
    RaceState.GROUP_RACE_RUNNING: {
        RaceState.GROUP_RACE_FINISHED,
        RaceState.GROUP_RACE_ABORTED,
    },
    RaceState.GROUP_RACE_FINISHED: {
        RaceState.GROUP_DONE,
        RaceState.GROUP_RACE_RUNNING,
    },
    RaceState.GROUP_RACE_ABORTED: {
        RaceState.GROUP_DONE,
        RaceState.GROUP_RACE_RUNNING,
    },
    RaceState.GROUP_DONE: {
        RaceState.SEMI_RUNNING,
    },
    RaceState.SEMI_RUNNING: {
        RaceState.SEMI_FINISHED,
        RaceState.SEMI_ABORTED,
    },
    RaceState.SEMI_FINISHED: {
        RaceState.SEMI_DONE,
        RaceState.SEMI_RUNNING,
    },
    RaceState.SEMI_ABORTED: {
        RaceState.SEMI_DONE,
        RaceState.SEMI_RUNNING,
    },
    RaceState.SEMI_DONE: {
        RaceState.FINAL_RUNNING,
    },
    RaceState.FINAL_RUNNING: {
        RaceState.FINAL_FINISHED,
    },
    RaceState.FINAL_FINISHED: {
        RaceState.CLOSED,
    },
    RaceState.CLOSED: set(),
}

ALLOWED: dict[RaceState, set[RaceState]] = {
    state: targets | {RaceState.IDLE}
    for state, targets in _ALLOWED_NON_IDLE.items()
}

_RUNNING_STATES = {
    RaceState.QUALIFYING_RUNNING,
    RaceState.GROUP_RACE_RUNNING,
    RaceState.SEMI_RUNNING,
    RaceState.FINAL_RUNNING,
}


class StateMachine:
    def __init__(self) -> None:
        self._state = RaceState.IDLE
        self._lock  = threading.Lock()

    @property
    def state(self) -> RaceState:
        with self._lock:
            return self._state

    def transition(self, to: RaceState) -> None:
        with self._lock:
            allowed = ALLOWED.get(self._state, {RaceState.IDLE})
            if to not in allowed:
                raise ValueError(
                    f"Illegal transition: {self._state} -> {to}. "
                    f"Allowed: {sorted(s.value for s in allowed)}"
                )
            self._state = to

    def is_running(self) -> bool:
        with self._lock:
            return self._state in _RUNNING_STATES

    def reset(self) -> None:
        with self._lock:
            self._state = RaceState.IDLE


# ---------------------------------------------------------------------------
# Per-zone registry
# ---------------------------------------------------------------------------

_zone_machines: dict[str, StateMachine] = {}
_zone_registry_lock = threading.Lock()


def get_zone_sm(zone_id: str) -> StateMachine:
    """Return (creating if necessary) the StateMachine for zone_id."""
    with _zone_registry_lock:
        if zone_id not in _zone_machines:
            _zone_machines[zone_id] = StateMachine()
        return _zone_machines[zone_id]


def all_running_zones() -> list[tuple[str, StateMachine]]:
    """Return [(zone_id, sm), ...] for all zones currently in a running state."""
    with _zone_registry_lock:
        return [
            (zid, sm)
            for zid, sm in _zone_machines.items()
            if sm.is_running()
        ]


def remove_zone_sm(zone_id: str) -> None:
    """Remove the state machine for a deleted zone."""
    with _zone_registry_lock:
        _zone_machines.pop(zone_id, None)


# Backward-compatible singleton (zone "default")
state_machine = get_zone_sm("default")
