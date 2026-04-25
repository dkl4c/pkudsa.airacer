"""
Thread-safe state machine for AI Racer competition phases.
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
        # Allow direct jump to any *_RUNNING for testing
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
        RaceState.QUALIFYING_RUNNING,   # next batch
    },
    RaceState.QUALIFYING_ABORTED: {
        RaceState.QUALIFYING_DONE,
        RaceState.QUALIFYING_RUNNING,   # retry
    },
    RaceState.QUALIFYING_DONE: {
        RaceState.GROUP_RACE_RUNNING,
    },
    RaceState.GROUP_RACE_RUNNING: {
        RaceState.GROUP_RACE_FINISHED,
        RaceState.GROUP_RACE_ABORTED,
    },
    RaceState.GROUP_RACE_FINISHED: {
        RaceState.GROUP_DONE,
        RaceState.GROUP_RACE_RUNNING,   # next match
    },
    RaceState.GROUP_RACE_ABORTED: {
        RaceState.GROUP_DONE,
        RaceState.GROUP_RACE_RUNNING,   # retry
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

# Merge IDLE as a legal target from every state
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
        """
        Move to a new state.

        Raises ValueError if the transition is not legal from the current state.
        """
        with self._lock:
            allowed = ALLOWED.get(self._state, {RaceState.IDLE})
            if to not in allowed:
                raise ValueError(
                    f"Illegal transition: {self._state} -> {to}. "
                    f"Allowed: {sorted(s.value for s in allowed)}"
                )
            self._state = to

    def is_running(self) -> bool:
        """Return True if the current state is any *_RUNNING state."""
        with self._lock:
            return self._state in _RUNNING_STATES

    def reset(self) -> None:
        """Force state to IDLE (used by reset-track admin action)."""
        with self._lock:
            self._state = RaceState.IDLE


# Module-level singleton
state_machine = StateMachine()
