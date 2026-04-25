"""
Admin REST endpoints.

All routes require HTTP Basic Auth (username ignored; password == ADMIN_PASSWORD).

Prefix: /api/admin
"""

import asyncio
import datetime
import json
import pathlib
import secrets
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

from config import (
    ADMIN_PASSWORD,
    DB_PATH,
    RACE_CONFIG_PATH,
    RECORDINGS_DIR,
    SUBMISSIONS_DIR,
    WEBOTS_BINARY,
    WORLD_FILE,
)
from db.models import get_db
from race.state_machine import RaceState, state_machine
from race.session import (
    kill_current_proc,
    monitor_webots,
    set_current_proc,
    start_webots,
    write_race_config,
)

router = APIRouter(prefix="/api/admin")

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

_security = HTTPBasic()


def require_admin(
    credentials: HTTPBasicCredentials = Depends(_security),
) -> None:
    ok = secrets.compare_digest(
        credentials.password.encode(), ADMIN_PASSWORD.encode()
    )
    if not ok:
        raise HTTPException(status_code=401, detail="Unauthorized")


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class SetSessionBody(BaseModel):
    session_type: str
    session_id:   str
    team_ids:     list[str]
    total_laps:   int


class OverrideScheduleBody(BaseModel):
    group_id:  str
    team_ids:  list[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _running_state_for(session_type: str) -> RaceState:
    mapping = {
        "qualifying":  RaceState.QUALIFYING_RUNNING,
        "group_race":  RaceState.GROUP_RACE_RUNNING,
        "semi":        RaceState.SEMI_RUNNING,
        "final":       RaceState.FINAL_RUNNING,
    }
    state = mapping.get(session_type.lower())
    if state is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown session_type '{session_type}'. "
                   f"Valid: {list(mapping.keys())}",
        )
    return state


def _finished_state_for(session_type: str) -> RaceState:
    mapping = {
        "qualifying": RaceState.QUALIFYING_FINISHED,
        "group_race": RaceState.GROUP_RACE_FINISHED,
        "semi":       RaceState.SEMI_FINISHED,
        "final":      RaceState.FINAL_FINISHED,
    }
    state = mapping.get(session_type.lower())
    if state is None:
        raise HTTPException(status_code=400, detail=f"Unknown session_type '{session_type}'")
    return state


def _aborted_state_for(session_type: str) -> RaceState:
    mapping = {
        "qualifying": RaceState.QUALIFYING_ABORTED,
        "group_race": RaceState.GROUP_RACE_ABORTED,
        "semi":       RaceState.SEMI_ABORTED,
        # FINAL has no aborted — map to IDLE as fallback
        "final":      RaceState.IDLE,
    }
    return mapping.get(session_type.lower(), RaceState.IDLE)


# ---------------------------------------------------------------------------
# Async broadcast helpers (imported lazily to avoid circular imports at
# module load time, since ws.admin imports nothing from api.admin)
# ---------------------------------------------------------------------------

async def _broadcast(state: str, session_id: Optional[str] = None, pid: Optional[int] = None):
    from ws.admin import broadcast_state
    await broadcast_state(state, session_id=session_id, webots_pid=pid)


# ---------------------------------------------------------------------------
# POST /api/admin/lock-submissions
# ---------------------------------------------------------------------------

@router.post("/lock-submissions")
async def lock_submissions(_auth=Depends(require_admin)):
    import api.submission as sub_module
    sub_module.submissions_locked = True
    return {"status": "locked"}


# ---------------------------------------------------------------------------
# POST /api/admin/set-session
# ---------------------------------------------------------------------------

@router.post("/set-session")
async def set_session(body: SetSessionBody, _auth=Depends(require_admin)):
    # Validate state machine allows starting this type
    target_running = _running_state_for(body.session_type)
    try:
        # Peek without committing (just validate)
        allowed = __import__(
            "race.state_machine", fromlist=["ALLOWED"]
        ).ALLOWED
        if target_running not in allowed.get(state_machine.state, set()):
            raise ValueError(f"Cannot transition {state_machine.state} -> {target_running}")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    # Resolve code paths for each team from DB
    with get_db(DB_PATH) as conn:
        cars = []
        for idx, team_id in enumerate(body.team_ids):
            team_row = conn.execute(
                "SELECT id, name FROM teams WHERE id = ?", (team_id,)
            ).fetchone()
            if team_row is None:
                raise HTTPException(
                    status_code=404, detail=f"Team '{team_id}' not found"
                )
            sub_row = conn.execute(
                """SELECT code_path FROM submissions
                   WHERE team_id = ? AND is_active = 1
                   ORDER BY submitted_at DESC LIMIT 1""",
                (team_id,),
            ).fetchone()
            code_path = sub_row["code_path"] if sub_row else ""
            cars.append({
                "car_node_id":    f"car_{idx + 1}",
                "team_id":        team_id,
                "team_name":      team_row["name"],
                "code_path":      code_path,
                "start_position": idx,
            })

    recording_path = str(pathlib.Path(RECORDINGS_DIR) / body.session_id)

    await asyncio.to_thread(
        write_race_config,
        body.session_id,
        body.session_type,
        body.total_laps,
        cars,
        recording_path,
        RACE_CONFIG_PATH,
    )

    # Insert / replace race_sessions row with phase="waiting"
    now = datetime.datetime.now().isoformat()
    with get_db(DB_PATH) as conn:
        conn.execute(
            """INSERT INTO race_sessions
               (id, type, team_ids, total_laps, started_at, finished_at, phase, result)
               VALUES (?, ?, ?, ?, NULL, NULL, 'waiting', NULL)
               ON CONFLICT(id) DO UPDATE SET
                 type=excluded.type,
                 team_ids=excluded.team_ids,
                 total_laps=excluded.total_laps,
                 phase='waiting',
                 started_at=NULL,
                 finished_at=NULL,
                 result=NULL""",
            (
                body.session_id,
                body.session_type,
                json.dumps(body.team_ids),
                body.total_laps,
            ),
        )

    return {"status": "ready", "session_id": body.session_id}


# ---------------------------------------------------------------------------
# POST /api/admin/start-race
# ---------------------------------------------------------------------------

@router.post("/start-race")
async def start_race(_auth=Depends(require_admin)):
    # Find current pending session from DB
    with get_db(DB_PATH) as conn:
        row = conn.execute(
            "SELECT id, type FROM race_sessions WHERE phase = 'waiting' ORDER BY rowid DESC LIMIT 1"
        ).fetchone()

    if row is None:
        raise HTTPException(
            status_code=409, detail="No session in 'waiting' phase. Call set-session first."
        )

    session_id   = row["id"]
    session_type = row["type"]
    target_state = _running_state_for(session_type)

    # Transition state machine
    try:
        state_machine.transition(target_state)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    # Launch Webots
    proc = await asyncio.to_thread(
        start_webots, WEBOTS_BINARY, WORLD_FILE, RACE_CONFIG_PATH, minimize=False
    )
    set_current_proc(proc, session_id)

    # Update DB
    now = datetime.datetime.now().isoformat()
    with get_db(DB_PATH) as conn:
        conn.execute(
            "UPDATE race_sessions SET phase = 'running', started_at = ? WHERE id = ?",
            (now, session_id),
        )

    # Callbacks for when Webots exits
    def _on_finished(sid: str):
        from race.scoring import extract_session_results
        asyncio.run_coroutine_threadsafe(
            _handle_finished(sid, session_type), _get_event_loop()
        )

    def _on_aborted(sid: str):
        asyncio.run_coroutine_threadsafe(
            _handle_aborted(sid, session_type), _get_event_loop()
        )

    monitor_webots(proc, session_id, RECORDINGS_DIR, _on_finished, _on_aborted)

    await _broadcast("running", session_id=session_id, pid=proc.pid)
    return {"status": "running", "session_id": session_id, "pid": proc.pid}


def _get_event_loop():
    """Return the running event loop (set by main.py at startup)."""
    import main as _main
    return _main._event_loop


async def _handle_finished(session_id: str, session_type: str):
    finished_state = _finished_state_for(session_type)
    try:
        state_machine.transition(finished_state)
    except ValueError:
        pass  # already transitioned elsewhere

    now = datetime.datetime.now().isoformat()

    # Try to read results and store in DB
    try:
        from race.scoring import extract_session_results
        results = await asyncio.to_thread(
            extract_session_results, session_id, RECORDINGS_DIR
        )
        result_json = json.dumps(results)

        # Write race_points
        with get_db(DB_PATH) as conn:
            conn.execute(
                "UPDATE race_sessions SET phase='recording_ready', finished_at=?, result=? WHERE id=?",
                (now, result_json, session_id),
            )
            for entry in results.get("final_rankings", []):
                tid   = entry.get("team_id")
                rank  = entry.get("rank")
                pts   = _rank_to_points(rank)
                if tid:
                    conn.execute(
                        """INSERT INTO race_points (team_id, session_id, rank, points)
                           VALUES (?, ?, ?, ?)
                           ON CONFLICT(team_id, session_id) DO UPDATE SET rank=excluded.rank, points=excluded.points""",
                        (tid, session_id, rank, pts),
                    )
    except Exception:
        with get_db(DB_PATH) as conn:
            conn.execute(
                "UPDATE race_sessions SET phase='recording_ready', finished_at=? WHERE id=?",
                (now, session_id),
            )

    await _broadcast("recording_ready", session_id=session_id)


async def _handle_aborted(session_id: str, session_type: str):
    aborted_state = _aborted_state_for(session_type)
    try:
        state_machine.transition(aborted_state)
    except ValueError:
        pass

    now = datetime.datetime.now().isoformat()
    with get_db(DB_PATH) as conn:
        conn.execute(
            "UPDATE race_sessions SET phase='aborted', finished_at=? WHERE id=?",
            (now, session_id),
        )

    await _broadcast("aborted", session_id=session_id)


def _rank_to_points(rank: Optional[int]) -> int:
    """Simple points table: 1st=10, 2nd=7, 3rd=5, 4th=3, rest=1."""
    table = {1: 10, 2: 7, 3: 5, 4: 3}
    return table.get(rank, 1)


# ---------------------------------------------------------------------------
# POST /api/admin/stop-race
# ---------------------------------------------------------------------------

@router.post("/stop-race")
async def stop_race(_auth=Depends(require_admin)):
    kill_current_proc()
    # The monitor thread will call _on_aborted; if we want instant feedback:
    current_state = state_machine.state
    # Find current session
    with get_db(DB_PATH) as conn:
        row = conn.execute(
            "SELECT id FROM race_sessions WHERE phase = 'running' ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
    session_id = row["id"] if row else None

    await _broadcast("aborted", session_id=session_id)
    return {"status": "stopping"}


# ---------------------------------------------------------------------------
# POST /api/admin/reset-track
# ---------------------------------------------------------------------------

@router.post("/reset-track")
async def reset_track(_auth=Depends(require_admin)):
    kill_current_proc()
    set_current_proc(None, None)
    state_machine.reset()
    await _broadcast("idle")
    return {"status": "idle"}


# ---------------------------------------------------------------------------
# GET /api/admin/standings
# ---------------------------------------------------------------------------

@router.get("/standings")
async def get_standings(_auth=Depends(require_admin)):
    with get_db(DB_PATH) as conn:
        teams = conn.execute("SELECT id, name FROM teams ORDER BY name").fetchall()

        standings = []
        for team in teams:
            tid = team["id"]

            # Best qualifying time from any qualifying session result
            qual_row = conn.execute(
                """SELECT rp.rank, rs.result
                   FROM race_points rp
                   JOIN race_sessions rs ON rs.id = rp.session_id
                   WHERE rp.team_id = ? AND rs.type = 'qualifying'
                   ORDER BY rp.rank ASC
                   LIMIT 1""",
                (tid,),
            ).fetchone()

            best_qual_time = None
            if qual_row and qual_row["result"]:
                try:
                    result = json.loads(qual_row["result"])
                    for entry in result.get("final_rankings", []):
                        if entry.get("team_id") == tid:
                            best_qual_time = entry.get("best_lap_time")
                            break
                except (json.JSONDecodeError, KeyError):
                    pass

            # Total group points
            points_row = conn.execute(
                """SELECT COALESCE(SUM(rp.points), 0) AS total
                   FROM race_points rp
                   JOIN race_sessions rs ON rs.id = rp.session_id
                   WHERE rp.team_id = ? AND rs.type = 'group'""",
                (tid,),
            ).fetchone()
            group_points = points_row["total"] if points_row else 0

            standings.append({
                "team_id":        tid,
                "team_name":      team["name"],
                "best_qual_time": best_qual_time,
                "group_points":   group_points,
            })

    return standings


# ---------------------------------------------------------------------------
# Phase finalization helpers
# ---------------------------------------------------------------------------

@router.post("/finalize-qualifying")
async def finalize_qualifying(_auth=Depends(require_admin)):
    try:
        state_machine.transition(RaceState.QUALIFYING_DONE)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await _broadcast("idle")
    return {"state": state_machine.state}


@router.post("/finalize-group")
async def finalize_group(_auth=Depends(require_admin)):
    try:
        state_machine.transition(RaceState.GROUP_DONE)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await _broadcast("idle")
    return {"state": state_machine.state}


@router.post("/finalize-semi")
async def finalize_semi(_auth=Depends(require_admin)):
    try:
        state_machine.transition(RaceState.SEMI_DONE)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await _broadcast("idle")
    return {"state": state_machine.state}


@router.post("/close-event")
async def close_event(_auth=Depends(require_admin)):
    try:
        state_machine.transition(RaceState.CLOSED)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await _broadcast("idle")
    return {"state": state_machine.state}


# ---------------------------------------------------------------------------
# POST /api/admin/override-schedule
# ---------------------------------------------------------------------------

@router.post("/override-schedule")
async def override_schedule(body: OverrideScheduleBody, _auth=Depends(require_admin)):
    with get_db(DB_PATH) as conn:
        row = conn.execute(
            "SELECT id FROM race_sessions WHERE id = ?", (body.group_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"Session '{body.group_id}' not found"
            )
        conn.execute(
            "UPDATE race_sessions SET team_ids = ? WHERE id = ?",
            (json.dumps(body.team_ids), body.group_id),
        )
    return {"status": "updated", "group_id": body.group_id, "team_ids": body.team_ids}
