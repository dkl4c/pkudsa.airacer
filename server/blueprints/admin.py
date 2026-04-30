"""
Admin REST endpoints.

All routes require HTTP Basic Auth (password == ADMIN_PASSWORD).

Prefix: /api/admin

Zone-scoped race control:
  POST /api/admin/zones/{zone_id}/set-session
  POST /api/admin/zones/{zone_id}/start-race
  POST /api/admin/zones/{zone_id}/stop-race
  POST /api/admin/zones/{zone_id}/finalize
  GET  /api/admin/zones/{zone_id}/standings
  GET  /api/admin/zones/{zone_id}/bracket

Zone CRUD:
  GET    /api/admin/zones
  POST   /api/admin/zones
  DELETE /api/admin/zones/{zone_id}
  GET    /api/admin/zones/{zone_id}/teams

Legacy (default zone) endpoints kept for backward compatibility.
"""

import asyncio
import base64
import datetime
import json
import pathlib
import secrets
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

from server.config.config import ADMIN_PASSWORD, DB_PATH, RECORDINGS_DIR, SUBMISSIONS_DIR
from server.database.models import get_db
from server.race.bracket import compute_bracket
from server.race.state_machine import (
    RaceState,
    get_zone_sm,
    remove_zone_sm,
)
from server.utils.simnode_client import (
    start_race as simnode_start_race,
    cancel_race as simnode_cancel_race,
    get_race_status as simnode_get_status,
    get_race_result as simnode_get_result,
    list_races as simnode_list_races,
    SIMNODE_URL as _SIMNODE_URL,
)

router = APIRouter(prefix="/api/admin")
_security = HTTPBasic()

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def require_admin(credentials: HTTPBasicCredentials = Depends(_security)) -> None:
    ok = secrets.compare_digest(credentials.password.encode(), ADMIN_PASSWORD.encode())
    if not ok:
        raise HTTPException(status_code=401, detail="Unauthorized")


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class ZoneCreateBody(BaseModel):
    id:          str
    name:        str
    description: str = ""
    total_laps:  int = 3


class SetSessionBody(BaseModel):
    session_type: str
    session_id:   str
    team_ids:     list[str]
    total_laps:   int


class ZoneSetSessionBody(BaseModel):
    session_type: str
    session_id:   str
    team_ids:     Optional[list[str]] = None  # if None, auto-select from zone
    total_laps:   Optional[int] = None        # if None, use zone default


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
        raise HTTPException(status_code=400, detail=f"Unknown session_type '{session_type}'")
    return state


def _finished_state_for(session_type: str) -> RaceState:
    return {
        "qualifying": RaceState.QUALIFYING_FINISHED,
        "group_race": RaceState.GROUP_RACE_FINISHED,
        "semi":       RaceState.SEMI_FINISHED,
        "final":      RaceState.FINAL_FINISHED,
    }.get(session_type.lower(), RaceState.IDLE)


def _aborted_state_for(session_type: str) -> RaceState:
    return {
        "qualifying": RaceState.QUALIFYING_ABORTED,
        "group_race": RaceState.GROUP_RACE_ABORTED,
        "semi":       RaceState.SEMI_ABORTED,
        "final":      RaceState.IDLE,
    }.get(session_type.lower(), RaceState.IDLE)


def _rank_to_points(rank: Optional[int]) -> int:
    return {1: 10, 2: 7, 3: 5, 4: 3}.get(rank, 1)


async def _broadcast(state: str, zone_id: str = "default",
                     session_id: Optional[str] = None,
                     pid: Optional[int] = None,
                     recording_path: Optional[str] = None):
    from server.ws.admin import broadcast_state
    await broadcast_state(state, zone_id=zone_id, session_id=session_id,
                          webots_pid=pid, recording_path=recording_path)


# In-memory store: session_id → cars list (zone_id stored too)
_pending_cars: dict[str, list] = {}
# Track which zone owns which session
_session_zone: dict[str, str] = {}
# Track current running session per zone
_zone_running_session: dict[str, str] = {}


def _get_running_session_id(zone_id: str) -> Optional[str]:
    return _zone_running_session.get(zone_id)


# ---------------------------------------------------------------------------
# Zone CRUD
# ---------------------------------------------------------------------------

@router.get("/zones")
async def list_zones(_auth=Depends(require_admin)):
    from server.race.state_machine import get_zone_sm
    with get_db(DB_PATH) as conn:
        rows = conn.execute("""
            SELECT z.id, z.name, z.description, z.total_laps, z.created_at,
                   COUNT(t.id) AS team_count
            FROM zones z
            LEFT JOIN teams t ON t.zone_id = z.id
            GROUP BY z.id
            ORDER BY z.created_at
        """).fetchall()

    result = []
    for r in rows:
        sm = get_zone_sm(r["id"])
        # Current running session
        running_session = _zone_running_session.get(r["id"])
        result.append({
            "zone_id":         r["id"],
            "id":              r["id"],
            "name":            r["name"],
            "description":     r["description"],
            "total_laps":      r["total_laps"],
            "created_at":      r["created_at"],
            "team_count":      r["team_count"],
            "state":           sm.state.value,
            "running_session": running_session,
        })
    return result


@router.post("/zones")
async def create_zone(body: ZoneCreateBody, _auth=Depends(require_admin)):
    import re
    if not re.match(r'^[a-zA-Z0-9_-]{2,32}$', body.id):
        raise HTTPException(status_code=400, detail="Zone ID: 字母/数字/下划线/连字符，2-32字符")
    now = datetime.datetime.now().isoformat()
    with get_db(DB_PATH) as conn:
        existing = conn.execute("SELECT id FROM zones WHERE id=?", (body.id,)).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail=f"赛区ID已存在: {body.id}")
        conn.execute(
            "INSERT INTO zones (id, name, description, total_laps, created_at) VALUES (?,?,?,?,?)",
            (body.id, body.name, body.description, body.total_laps, now),
        )
    return {"status": "created", "zone_id": body.id}


@router.delete("/zones/{zone_id}")
async def delete_zone(zone_id: str, _auth=Depends(require_admin)):
    sm = get_zone_sm(zone_id)
    if sm.is_running():
        raise HTTPException(status_code=409, detail="赛区有比赛正在进行，无法删除")
    with get_db(DB_PATH) as conn:
        row = conn.execute("SELECT id FROM zones WHERE id=?", (zone_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"赛区未找到: {zone_id}")
        conn.execute("DELETE FROM zones WHERE id=?", (zone_id,))
    remove_zone_sm(zone_id)
    return {"status": "deleted", "zone_id": zone_id}


@router.get("/zones/{zone_id}/teams")
async def get_zone_teams(zone_id: str, _auth=Depends(require_admin)):
    with get_db(DB_PATH) as conn:
        teams = conn.execute(
            "SELECT id, name, created_at FROM teams WHERE zone_id=? ORDER BY name",
            (zone_id,),
        ).fetchall()
        # For each team, show which slot is race-active
        result = []
        for t in teams:
            active_sub = conn.execute(
                """SELECT slot_name, submitted_at FROM submissions
                   WHERE team_id=? AND is_race_active=1 LIMIT 1""",
                (t["id"],),
            ).fetchone()
            result.append({
                "id":                t["id"],
                "name":              t["name"],
                "created_at":        t["created_at"],
                "active_slot":       active_sub["slot_name"] if active_sub else None,
                "active_version":    active_sub["submitted_at"] if active_sub else None,
            })
    return result


@router.get("/zones/{zone_id}/standings")
async def get_zone_standings(zone_id: str, _auth=Depends(require_admin)):
    with get_db(DB_PATH) as conn:
        rows = conn.execute("""
            SELECT rp.team_id, t.name, SUM(rp.points) AS total_points
            FROM race_points rp
            JOIN teams t ON rp.team_id = t.id
            JOIN race_sessions rs ON rp.session_id = rs.id
            WHERE rs.zone_id = ?
            GROUP BY rp.team_id
            ORDER BY total_points DESC
        """, (zone_id,)).fetchall()
    return [dict(r) for r in rows]


@router.get("/zones/{zone_id}/bracket")
async def get_zone_bracket(zone_id: str, _auth=Depends(require_admin)):
    with get_db(DB_PATH) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM teams WHERE zone_id=?", (zone_id,)
        ).fetchone()[0]
    return compute_bracket(count)


# ---------------------------------------------------------------------------
# Zone-scoped race control
# ---------------------------------------------------------------------------

@router.post("/zones/{zone_id}/set-session")
async def zone_set_session(
    zone_id: str,
    body: ZoneSetSessionBody,
    _auth=Depends(require_admin),
):
    target_running = _running_state_for(body.session_type)

    with get_db(DB_PATH) as conn:
        zone_row = conn.execute(
            "SELECT id, total_laps FROM zones WHERE id=?", (zone_id,)
        ).fetchone()
        if zone_row is None:
            raise HTTPException(status_code=404, detail=f"赛区未找到: {zone_id}")

        total_laps = body.total_laps if body.total_laps is not None else zone_row["total_laps"]

        # Determine team_ids
        if body.team_ids is not None:
            team_ids = body.team_ids
        else:
            # Auto-select all teams in the zone for qualifying; for later stages, use standings
            rows = conn.execute(
                "SELECT id FROM teams WHERE zone_id=? ORDER BY name",
                (zone_id,),
            ).fetchall()
            team_ids = [r["id"] for r in rows]

        # Build cars list
        cars = []
        for idx, team_id in enumerate(team_ids):
            team_row = conn.execute(
                "SELECT id, name FROM teams WHERE id=?", (team_id,)
            ).fetchone()
            if team_row is None:
                raise HTTPException(status_code=404, detail=f"Team '{team_id}' not found")

            # Prefer race-active slot; fall back to main active slot
            sub_row = conn.execute(
                """SELECT code_path FROM submissions
                   WHERE team_id=? AND is_race_active=1 AND is_active=1
                   LIMIT 1""",
                (team_id,),
            ).fetchone()
            if sub_row is None:
                sub_row = conn.execute(
                    """SELECT code_path FROM submissions
                       WHERE team_id=? AND slot_name='main' AND is_active=1
                       ORDER BY submitted_at DESC LIMIT 1""",
                    (team_id,),
                ).fetchone()

            if sub_row and pathlib.Path(sub_row["code_path"]).exists():
                code_bytes = pathlib.Path(sub_row["code_path"]).read_bytes()
                code_b64 = base64.b64encode(code_bytes).decode()
            else:
                template = pathlib.Path(__file__).resolve().parent.parent.parent / "sdk" / "team_controller.py"
                code_b64 = base64.b64encode(template.read_bytes()).decode() if template.exists() else ""

            cars.append({
                "car_slot":  f"car_{idx + 1}",
                "team_id":   team_id,
                "team_name": team_row["name"],
                "code_b64":  code_b64,
            })

        # Persist session record
        conn.execute(
            """INSERT INTO race_sessions
               (id, type, team_ids, total_laps, started_at, finished_at, phase, result, zone_id)
               VALUES (?, ?, ?, ?, NULL, NULL, 'waiting', NULL, ?)
               ON CONFLICT(id) DO UPDATE SET
                 type=excluded.type, team_ids=excluded.team_ids,
                 total_laps=excluded.total_laps, phase='waiting',
                 started_at=NULL, finished_at=NULL, result=NULL,
                 zone_id=excluded.zone_id""",
            (body.session_id, body.session_type, json.dumps(team_ids), total_laps, zone_id),
        )

    _pending_cars[body.session_id] = cars
    _session_zone[body.session_id] = zone_id
    return {"status": "ready", "session_id": body.session_id, "zone_id": zone_id, "cars_count": len(cars)}


@router.post("/zones/{zone_id}/start-race")
async def zone_start_race(zone_id: str, _auth=Depends(require_admin)):
    sm = get_zone_sm(zone_id)

    with get_db(DB_PATH) as conn:
        row = conn.execute(
            """SELECT id, type, total_laps FROM race_sessions
               WHERE phase='waiting' AND zone_id=?
               ORDER BY rowid DESC LIMIT 1""",
            (zone_id,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=409, detail="该赛区没有 'waiting' 阶段的场次，请先调用 set-session")

    session_id   = row["id"]
    session_type = row["type"]
    total_laps   = row["total_laps"]
    target_state = _running_state_for(session_type)

    try:
        sm.transition(target_state)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    cars = _pending_cars.pop(session_id, [])
    if not cars:
        sm.reset()
        raise HTTPException(status_code=409, detail="Car codes missing. Call set-session again.")

    try:
        resp = await asyncio.to_thread(
            simnode_start_race, session_id, session_type, total_laps, cars
        )
    except RuntimeError as exc:
        sm.reset()
        raise HTTPException(status_code=503, detail=f"Sim Node unreachable: {exc}")

    now = datetime.datetime.now().isoformat()
    with get_db(DB_PATH) as conn:
        conn.execute(
            "UPDATE race_sessions SET phase='running', started_at=? WHERE id=?",
            (now, session_id),
        )

    _zone_running_session[zone_id] = session_id
    asyncio.create_task(_watch_simnode(session_id, session_type, zone_id))

    await _broadcast("running", zone_id=zone_id, session_id=session_id)
    return {"status": "running", "session_id": session_id, "zone_id": zone_id,
            "stream_url": resp.get("stream_ws_url")}


@router.post("/zones/{zone_id}/stop-race")
async def zone_stop_race(zone_id: str, _auth=Depends(require_admin)):
    with get_db(DB_PATH) as conn:
        row = conn.execute(
            "SELECT id, type FROM race_sessions WHERE phase='running' AND zone_id=? ORDER BY rowid DESC LIMIT 1",
            (zone_id,),
        ).fetchone()
    session_id   = row["id"] if row else None
    session_type = row["type"] if row else "qualifying"

    if session_id:
        await asyncio.to_thread(simnode_cancel_race, session_id)
        status = await asyncio.to_thread(simnode_get_status, session_id)
        if status == "completed":
            await _handle_finished(session_id, session_type, zone_id)
        else:
            await _handle_aborted(session_id, session_type, zone_id)

    return {"status": "stopping", "zone_id": zone_id}


@router.post("/zones/{zone_id}/reset")
async def zone_reset(zone_id: str, _auth=Depends(require_admin)):
    sm = get_zone_sm(zone_id)
    sm.reset()
    _zone_running_session.pop(zone_id, None)
    await _broadcast("idle", zone_id=zone_id)
    return {"status": "idle", "zone_id": zone_id}


@router.post("/zones/{zone_id}/finalize")
async def zone_finalize(zone_id: str, _auth=Depends(require_admin)):
    """Advance the zone to the next stage, automatically selecting qualifying teams."""
    sm = get_zone_sm(zone_id)
    current = sm.state.value

    # Determine next state mapping
    next_state_map = {
        "QUALIFYING_FINISHED": RaceState.QUALIFYING_DONE,
        "QUALIFYING_ABORTED":  RaceState.QUALIFYING_DONE,
        "GROUP_RACE_FINISHED": RaceState.GROUP_DONE,
        "GROUP_RACE_ABORTED":  RaceState.GROUP_DONE,
        "SEMI_FINISHED":       RaceState.SEMI_DONE,
        "SEMI_ABORTED":        RaceState.SEMI_DONE,
        "FINAL_FINISHED":      RaceState.CLOSED,
    }
    next_state = next_state_map.get(current)
    if next_state is None:
        raise HTTPException(
            status_code=409,
            detail=f"当前状态 '{current}' 不支持 finalize 操作"
        )

    try:
        sm.transition(next_state)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    await _broadcast("idle", zone_id=zone_id)
    return {"state": sm.state.value, "zone_id": zone_id}


# ---------------------------------------------------------------------------
# Watchers and handlers
# ---------------------------------------------------------------------------

async def _watch_simnode(session_id: str, session_type: str, zone_id: str = "default"):
    """Poll Sim Node status every 5s until race ends."""
    none_strikes = 0
    while True:
        await asyncio.sleep(5)
        status = await asyncio.to_thread(simnode_get_status, session_id)
        if status is None:
            none_strikes += 1
            if none_strikes >= 3:
                break
            continue
        none_strikes = 0
        if status == "completed":
            await _handle_finished(session_id, session_type, zone_id)
            break
        if status in ("error", "cancelled"):
            await _handle_aborted(session_id, session_type, zone_id)
            break


async def _handle_finished(session_id: str, session_type: str, zone_id: str = "default"):
    sm = get_zone_sm(zone_id)
    try:
        sm.transition(_finished_state_for(session_type))
    except ValueError:
        pass

    rec_dir = pathlib.Path(RECORDINGS_DIR) / session_id
    meta_file = rec_dir / "metadata.json"
    recording_path = str(rec_dir.resolve())

    rec_dir.mkdir(parents=True, exist_ok=True)
    if meta_file.exists():
        try:
            result = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            result = {}
    else:
        result = await asyncio.to_thread(simnode_get_result, session_id) or {}

    result.setdefault("session_id", session_id)
    result.setdefault("session_type", session_type)
    result.setdefault("recording_path", recording_path)
    result.setdefault("recorded_at", datetime.datetime.now().isoformat())
    result["zone_id"] = zone_id  # always overwrite to ensure it's correct

    meta_file.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    now = datetime.datetime.now().isoformat()
    with get_db(DB_PATH) as conn:
        conn.execute(
            "UPDATE race_sessions SET phase='recording_ready', finished_at=? WHERE id=?",
            (now, session_id),
        )

    _zone_running_session.pop(zone_id, None)
    await _broadcast("recording_ready", zone_id=zone_id,
                     session_id=session_id, recording_path=recording_path)


async def _handle_aborted(session_id: str, session_type: str, zone_id: str = "default"):
    sm = get_zone_sm(zone_id)
    try:
        sm.transition(_aborted_state_for(session_type))
    except ValueError:
        pass
    now = datetime.datetime.now().isoformat()

    # Save partial recording if Webots wrote any telemetry data
    rec_dir = pathlib.Path(RECORDINGS_DIR) / session_id
    meta_file = rec_dir / "metadata.json"
    telemetry_file = rec_dir / "telemetry.jsonl"
    if telemetry_file.exists() and not meta_file.exists():
        rec_dir.mkdir(parents=True, exist_ok=True)
        meta_file.write_text(
            json.dumps({
                "session_id":    session_id,
                "session_type":  session_type,
                "zone_id":       zone_id,
                "finish_reason": "aborted",
                "recorded_at":   now,
                "teams":         [],
                "final_rankings": [],
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        db_phase = "recording_ready"
    else:
        db_phase = "aborted"

    with get_db(DB_PATH) as conn:
        conn.execute(
            "UPDATE race_sessions SET phase=?, finished_at=? WHERE id=?",
            (db_phase, now, session_id),
        )
    _zone_running_session.pop(zone_id, None)
    await _broadcast("aborted", zone_id=zone_id, session_id=session_id)


# ---------------------------------------------------------------------------
# Legacy endpoints (default zone, backward compat)
# ---------------------------------------------------------------------------

@router.post("/lock-submissions")
async def lock_submissions(_auth=Depends(require_admin)):
    import server.blueprints.submission as sub_module
    sub_module.submissions_locked = True
    return {"status": "locked"}


@router.post("/set-session")
async def set_session(body: SetSessionBody, _auth=Depends(require_admin)):
    zone_body = ZoneSetSessionBody(
        session_type=body.session_type,
        session_id=body.session_id,
        team_ids=body.team_ids,
        total_laps=body.total_laps,
    )
    # Create default zone if not exists
    with get_db(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO zones (id, name, description, total_laps, created_at) VALUES ('default','Default Zone','',3,?)",
            (datetime.datetime.now().isoformat(),)
        )
    return await zone_set_session("default", zone_body, _auth)


@router.post("/start-race")
async def start_race(_auth=Depends(require_admin)):
    return await zone_start_race("default", _auth)


@router.post("/stop-race")
async def stop_race(_auth=Depends(require_admin)):
    return await zone_stop_race("default", _auth)


@router.post("/reset-track")
async def reset_track(_auth=Depends(require_admin)):
    return await zone_reset("default", _auth)


@router.get("/standings")
async def get_standings(_auth=Depends(require_admin)):
    return await get_zone_standings("default", _auth)


@router.post("/finalize-qualifying")
async def finalize_qualifying(_auth=Depends(require_admin)):
    sm = get_zone_sm("default")
    try:
        sm.transition(RaceState.QUALIFYING_DONE)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await _broadcast("idle", zone_id="default")
    return {"state": sm.state}


@router.post("/finalize-group")
async def finalize_group(_auth=Depends(require_admin)):
    sm = get_zone_sm("default")
    try:
        sm.transition(RaceState.GROUP_DONE)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await _broadcast("idle", zone_id="default")
    return {"state": sm.state}


@router.post("/finalize-semi")
async def finalize_semi(_auth=Depends(require_admin)):
    sm = get_zone_sm("default")
    try:
        sm.transition(RaceState.SEMI_DONE)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await _broadcast("idle", zone_id="default")
    return {"state": sm.state}


@router.post("/close-event")
async def close_event(_auth=Depends(require_admin)):
    sm = get_zone_sm("default")
    try:
        sm.transition(RaceState.CLOSED)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await _broadcast("idle", zone_id="default")
    return {"state": sm.state}


# ---------------------------------------------------------------------------
# GET /api/admin/live-frame/{session_id} — proxy overhead camera JPEG
# ---------------------------------------------------------------------------

@router.get("/live-frame/{session_id}")
async def get_live_frame(session_id: str, _auth=Depends(require_admin)):
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{_SIMNODE_URL}/race/{session_id}/frame",
                timeout=3.0,
            )
        if resp.status_code == 404:
            raise HTTPException(status_code=404, detail="No frame available yet")
        resp.raise_for_status()
        return Response(
            content=resp.content,
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store"},
        )
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Simnode unreachable")
