"""
Thread-safe state machine for AI Racer competition phases.
Supports per-zone independent state machines; the module-level
`state_machine` is kept for backward compatibility (zone "default").

State is persisted to the database on every transition so it survives restarts.
"""

import sqlite3
import threading
from enum import Enum
from pathlib import Path
from typing import Callable, Optional


class RaceState(str, Enum):
    """赛事状态机，以Zone为单位"""

    # 报名阶段（初始状态，赛程正式开始前）
    REGISTRATION = "REGISTRATION"
    # 空闲待命
    IDLE = "IDLE"
    # 排位赛
    PLACEMENT_RUNNING = "PLACEMENT_RUNNING"  # 正在进行比赛（仿真运行中）
    PLACEMENT_FINISHED = "PLACEMENT_FINISHED"  # 某场比赛结束
    PLACEMENT_ABORTED = "PLACEMENT_ABORTED"  # 某场比赛被终止
    PLACEMENT_DONE = "PLACEMENT_DONE"  # 当前赛程所有比赛结束
    # 分组赛
    GROUP_STAGE_RUNNING = "GROUP_STAGE_RUNNING"
    GROUP_STAGE_FINISHED = "GROUP_STAGE_FINISHED"
    GROUP_STAGE_ABORTED = "GROUP_STAGE_ABORTED"
    GROUP_STAGE_DONE = "GROUP_STAGE_DONE"
    # 半决赛
    SEMI_RUNNING = "SEMI_RUNNING"
    SEMI_FINISHED = "SEMI_FINISHED"
    SEMI_ABORTED = "SEMI_ABORTED"
    SEMI_DONE = "SEMI_DONE"
    # 决赛
    FINAL_RUNNING = "FINAL_RUNNING"
    FINAL_FINISHED = "FINAL_FINISHED"
    # 已关闭
    CLOSED = "CLOSED"


# Any state can transition to IDLE (reset-track).
# The dict below lists non-IDLE legal targets from each source.
# 状态转换规则（IDLE 状态可以转换到任何其他状态）
_ALLOWED_NON_IDLE: dict[RaceState, set[RaceState]] = {
    RaceState.REGISTRATION: {
        RaceState.IDLE,
    },
    RaceState.IDLE: {
        RaceState.REGISTRATION,
        RaceState.PLACEMENT_RUNNING,
        RaceState.GROUP_STAGE_RUNNING,
        RaceState.SEMI_RUNNING,
        RaceState.FINAL_RUNNING,
    },
    RaceState.PLACEMENT_RUNNING: {
        RaceState.PLACEMENT_FINISHED,
        RaceState.PLACEMENT_ABORTED,
    },
    RaceState.PLACEMENT_FINISHED: {
        RaceState.PLACEMENT_DONE,
        RaceState.PLACEMENT_RUNNING,
    },
    RaceState.PLACEMENT_ABORTED: {
        RaceState.PLACEMENT_DONE,
        RaceState.PLACEMENT_RUNNING,
    },
    RaceState.PLACEMENT_DONE: {
        RaceState.GROUP_STAGE_RUNNING,
        RaceState.SEMI_RUNNING,  # small zones may skip group_stage
        RaceState.FINAL_RUNNING,  # tiny zones go straight to final
    },
    RaceState.GROUP_STAGE_RUNNING: {
        RaceState.GROUP_STAGE_FINISHED,
        RaceState.GROUP_STAGE_ABORTED,
    },
    RaceState.GROUP_STAGE_FINISHED: {
        RaceState.GROUP_STAGE_DONE,
        RaceState.GROUP_STAGE_RUNNING,
    },
    RaceState.GROUP_STAGE_ABORTED: {
        RaceState.GROUP_STAGE_DONE,
        RaceState.GROUP_STAGE_RUNNING,
    },
    RaceState.GROUP_STAGE_DONE: {
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

"""追加IDLE规则"""
ALLOWED: dict[RaceState, set[RaceState]] = {
    state: targets | {RaceState.IDLE} for state, targets in _ALLOWED_NON_IDLE.items()
}

_RUNNING_STATES = {
    RaceState.PLACEMENT_RUNNING,
    RaceState.GROUP_STAGE_RUNNING,
    RaceState.SEMI_RUNNING,
    RaceState.FINAL_RUNNING,
}


class StateMachine:
    def __init__(
        self,
        initial_state: RaceState = RaceState.REGISTRATION,
        persist_cb: Optional[Callable[["RaceState"], None]] = None,
    ) -> None:
        self._state = initial_state
        self._lock = threading.Lock()  # 线程安全
        self._persist_cb = persist_cb

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
        # Persist outside the lock to avoid blocking other threads during I/O
        if self._persist_cb:
            self._persist_cb(to)

    def is_running(self) -> bool:
        with self._lock:
            return self._state in _RUNNING_STATES

    def reset(self) -> None:
        with self._lock:
            self._state = RaceState.IDLE
        if self._persist_cb:
            self._persist_cb(RaceState.IDLE)


# ---------------------------------------------------------------------------
# DB helpers (inline to avoid circular imports)
# ---------------------------------------------------------------------------

_DB_PATH: Optional[Path] = None


def set_db_path(db_path: str | Path) -> None:
    """Configure the database path used for state persistence."""
    global _DB_PATH
    _DB_PATH = Path(db_path)


def _db_save_state(zone_id: str, state: RaceState) -> None:
    """Write zone state to the database (synchronous, called from transition)."""
    if _DB_PATH is None:
        return
    try:
        with sqlite3.connect(str(_DB_PATH)) as conn:
            conn.execute(
                "UPDATE zones SET state = ? WHERE id = ?",
                (state.value, zone_id),
            )
            conn.commit()
    except Exception:
        pass  # best-effort; state is still in memory


def _db_load_state(zone_id: str) -> Optional[RaceState]:
    """Read zone state from the database. Returns None if not found."""
    if _DB_PATH is None or not _DB_PATH.exists():
        return None
    try:
        with sqlite3.connect(str(_DB_PATH)) as conn:
            row = conn.execute(
                "SELECT state FROM zones WHERE id = ?", (zone_id,)
            ).fetchone()
        if row:
            return RaceState(row[0])
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Per-zone registry
# ---------------------------------------------------------------------------

_zone_machines: dict[str, StateMachine] = {}
_zone_registry_lock = threading.Lock()


def get_zone_sm(zone_id: str) -> StateMachine:
    """Return (creating if necessary) the StateMachine for zone_id.

    On first creation, restores the last-known state from the database.
    Every subsequent transition is persisted back to the database.
    """
    with _zone_registry_lock:
        if zone_id not in _zone_machines:
            db_state = _db_load_state(zone_id)
            initial = db_state if db_state is not None else RaceState.REGISTRATION
            _zone_machines[zone_id] = StateMachine(
                initial_state=initial,
                persist_cb=lambda s: _db_save_state(zone_id, s),
            )
        return _zone_machines[zone_id]


def all_running_zones() -> list[tuple[str, StateMachine]]:
    """Return [(zone_id, sm), ...] for all zones currently in a running state."""
    with _zone_registry_lock:
        return [(zid, sm) for zid, sm in _zone_machines.items() if sm.is_running()]


def all_zone_ids() -> list[str]:
    """Return all registered zone IDs."""
    with _zone_registry_lock:
        return list(_zone_machines.keys())


def remove_zone_sm(zone_id: str) -> None:
    """Remove the state machine for a deleted zone."""
    with _zone_registry_lock:
        _zone_machines.pop(zone_id, None)


# Backward-compatible singleton (zone "default")
# 状态机单例
state_machine = get_zone_sm("default")
