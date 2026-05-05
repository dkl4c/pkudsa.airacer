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
import sqlite3
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

from server.config.config import (
    ADMIN_PASSWORD,
    DB_PATH,
    RECORDINGS_DIR,
    SUBMISSIONS_DIR,
)
from server.database.action import (
    db_create_zone,
    db_delete_zone,
    db_ensure_default_zone,
    db_get_running_session,
    db_get_teams_with_code,
    db_get_waiting_session,
    db_get_zone,
    db_get_zone_team_count,
    db_get_zone_team_ids,
    db_get_zone_standings,
    db_get_zone_teams,
    db_list_zones,
    db_mark_session_aborted,
    db_mark_session_finished,
    db_mark_session_running,
    db_upsert_session,
)
from server.database.models import get_db
from server.race.bracket import compute_bracket
from server.race.state_machine import (
    RaceState,
    get_zone_sm,
    remove_zone_sm,
)
from server.utils.simnode_client import (
    SIMNODE_URL as _SIMNODE_URL,
    cancel_race as simnode_cancel_race,
    get_race_result as simnode_get_result,
    get_race_status as simnode_get_status,
    list_races as simnode_list_races,
    start_race as simnode_start_race,
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
    id: str
    name: str
    description: str = ""
    total_laps: int = 3


class SetSessionBody(BaseModel):
    session_type: str
    session_id: str
    team_ids: list[str]
    total_laps: int


class ZoneSetSessionBody(BaseModel):
    session_type: str
    session_id: str
    team_ids: Optional[list[str]] = None  # if None, auto-select from zone
    total_laps: Optional[int] = None  # if None, use zone default


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _running_state_for(session_type: str) -> RaceState:
    mapping = {
        "qualifying": RaceState.QUALIFYING_RUNNING,
        "group_race": RaceState.GROUP_RACE_RUNNING,
        "semi": RaceState.SEMI_RUNNING,
        "final": RaceState.FINAL_RUNNING,
    }
    state = mapping.get(session_type.lower())
    if state is None:
        raise HTTPException(
            status_code=400, detail=f"Unknown session_type '{session_type}'"
        )
    return state


def _finished_state_for(session_type: str) -> RaceState:
    return {
        "qualifying": RaceState.QUALIFYING_FINISHED,
        "group_race": RaceState.GROUP_RACE_FINISHED,
        "semi": RaceState.SEMI_FINISHED,
        "final": RaceState.FINAL_FINISHED,
    }.get(session_type.lower(), RaceState.IDLE)


def _aborted_state_for(session_type: str) -> RaceState:
    return {
        "qualifying": RaceState.QUALIFYING_ABORTED,
        "group_race": RaceState.GROUP_RACE_ABORTED,
        "semi": RaceState.SEMI_ABORTED,
        "final": RaceState.IDLE,
    }.get(session_type.lower(), RaceState.IDLE)


def _rank_to_points(rank: Optional[int]) -> int:
    return {1: 10, 2: 7, 3: 5, 4: 3}.get(rank, 1)


async def _broadcast(
    state: str,
    zone_id: str = "default",
    session_id: Optional[str] = None,
    pid: Optional[int] = None,
    recording_path: Optional[str] = None,
):
    from server.ws.admin import broadcast_state

    await broadcast_state(
        state,
        zone_id=zone_id,
        session_id=session_id,
        webots_pid=pid,
        recording_path=recording_path,
    )


def _build_cars(teams_data: list) -> list:
    """Pure file I/O: read each team's code file and base64-encode it."""
    template = (
        pathlib.Path(__file__).resolve().parent.parent.parent / "sdk" / "team_controller.py"
    )
    cars = []
    for idx, t in enumerate(teams_data):
        code_path = t.get("code_path")
        if code_path and pathlib.Path(code_path).exists():
            code_b64 = base64.b64encode(pathlib.Path(code_path).read_bytes()).decode()
        else:
            code_b64 = base64.b64encode(template.read_bytes()).decode() if template.exists() else ""
        cars.append({
            "car_slot": f"car_{idx + 1}",
            "team_id": t["id"],
            "team_name": t["name"],
            "code_b64": code_b64,
        })
    return cars


# Track current running session per zone
_zone_running_session: dict[str, str] = {}


def _get_running_session_id(zone_id: str) -> Optional[str]:
    return _zone_running_session.get(zone_id)


# ---------------------------------------------------------------------------
# Zone CRUD
# ---------------------------------------------------------------------------


@router.get("/zones")
async def list_zones(_auth=Depends(require_admin)):
    with get_db(DB_PATH) as conn:
        rows = db_list_zones(conn)
    result = []
    for r in rows:
        sm = get_zone_sm(r["id"])
        result.append({
            "zone_id":         r["id"],
            "id":              r["id"],
            "name":            r["name"],
            "description":     r["description"],
            "total_laps":      r["total_laps"],
            "created_at":      r["created_at"],
            "team_count":      r["team_count"],
            "state":           sm.state.value,
            "running_session": _zone_running_session.get(r["id"]),
        })
    return result


@router.post("/zones")
async def create_zone(body: ZoneCreateBody, _auth=Depends(require_admin)):
    import re

    if not re.match(r"^[a-zA-Z0-9_-]{2,32}$", body.id):
        raise HTTPException(
            status_code=400, detail="Zone ID: 字母/数字/下划线/连字符，2-32字符"
        )
    now = datetime.datetime.now().isoformat()
    try:
        with get_db(DB_PATH) as conn:
            db_create_zone(conn, body.id, body.name, body.description, body.total_laps, now)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail=f"赛区ID已存在: {body.id}")
    return {"status": "created", "zone_id": body.id}


@router.delete("/zones/{zone_id}")
async def delete_zone(zone_id: str, _auth=Depends(require_admin)):
    sm = get_zone_sm(zone_id)
    if sm.is_running():
        raise HTTPException(status_code=409, detail="赛区有比赛正在进行，无法删除")
    with get_db(DB_PATH) as conn:
        if not db_delete_zone(conn, zone_id):
            raise HTTPException(status_code=404, detail=f"赛区未找到: {zone_id}")
    remove_zone_sm(zone_id)
    return {"status": "deleted", "zone_id": zone_id}


@router.get("/zones/{zone_id}/teams")
async def get_zone_teams(zone_id: str, _auth=Depends(require_admin)):
    with get_db(DB_PATH) as conn:
        return db_get_zone_teams(conn, zone_id)


@router.get("/zones/{zone_id}/standings")
async def get_zone_standings(zone_id: str, _auth=Depends(require_admin)):
    with get_db(DB_PATH) as conn:
        return db_get_zone_standings(conn, zone_id)


@router.get("/zones/{zone_id}/bracket")
async def get_zone_bracket(zone_id: str, _auth=Depends(require_admin)):
    with get_db(DB_PATH) as conn:
        count = db_get_zone_team_count(conn, zone_id)
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
    _running_state_for(body.session_type)  # validate early

    with get_db(DB_PATH) as conn:
        zone = db_get_zone(conn, zone_id)
        if zone is None:
            raise HTTPException(status_code=404, detail=f"赛区未找到: {zone_id}")

        total_laps = body.total_laps if body.total_laps is not None else zone["total_laps"]
        team_ids = body.team_ids if body.team_ids is not None else db_get_zone_team_ids(conn, zone_id)

        try:
            teams_data = db_get_teams_with_code(conn, team_ids)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

        db_upsert_session(conn, body.session_id, body.session_type, team_ids, total_laps, zone_id)

    return {
        "status": "ready",
        "session_id": body.session_id,
        "zone_id": zone_id,
        "cars_count": len(teams_data),
    }


@router.post("/zones/{zone_id}/start-race")
async def zone_start_race(zone_id: str, _auth=Depends(require_admin)):
    sm = get_zone_sm(zone_id)

    with get_db(DB_PATH) as conn:
        row = db_get_waiting_session(conn, zone_id)

    if row is None:
        raise HTTPException(
            status_code=409,
            detail="该赛区没有 'waiting' 阶段的场次，请先调用 set-session",
        )

    session_id = row["id"]
    session_type = row["type"]
    total_laps = row["total_laps"]
    target_state = _running_state_for(session_type)

    try:
        sm.transition(target_state)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    with get_db(DB_PATH) as conn:
        teams_data = db_get_teams_with_code(conn, row["team_ids"])
    cars = _build_cars(teams_data)
    if not cars:
        sm.reset()
        raise HTTPException(
            status_code=409, detail="No team code available. Call set-session first."
        )

    try:
        resp = await asyncio.to_thread(
            simnode_start_race, session_id, session_type, total_laps, cars
        )
    except RuntimeError as exc:
        sm.reset()
        raise HTTPException(status_code=503, detail=f"Sim Node unreachable: {exc}")

    now = datetime.datetime.now().isoformat()
    with get_db(DB_PATH) as conn:
        db_mark_session_running(conn, session_id, now)

    _zone_running_session[zone_id] = session_id
    asyncio.create_task(_watch_simnode(session_id, session_type, zone_id))

    await _broadcast("running", zone_id=zone_id, session_id=session_id)
    return {
        "status": "running",
        "session_id": session_id,
        "zone_id": zone_id,
        "stream_url": resp.get("stream_ws_url"),
    }


@router.post("/zones/{zone_id}/stop-race")
async def zone_stop_race(zone_id: str, _auth=Depends(require_admin)):
    with get_db(DB_PATH) as conn:
        row = db_get_running_session(conn, zone_id)
    session_id = row["id"] if row else None
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
            status_code=409, detail=f"当前状态 '{current}' 不支持 finalize 操作"
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


async def _handle_finished(
    session_id: str, session_type: str, zone_id: str = "default"
):
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
    result["zone_id"] = zone_id

    meta_file.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    now = datetime.datetime.now().isoformat()
    with get_db(DB_PATH) as conn:
        db_mark_session_finished(conn, session_id, now)

    _zone_running_session.pop(zone_id, None)
    await _broadcast(
        "recording_ready",
        zone_id=zone_id,
        session_id=session_id,
        recording_path=recording_path,
    )


async def _handle_aborted(session_id: str, session_type: str, zone_id: str = "default"):
    sm = get_zone_sm(zone_id)
    try:
        sm.transition(_aborted_state_for(session_type))
    except ValueError:
        pass
    now = datetime.datetime.now().isoformat()

    rec_dir = pathlib.Path(RECORDINGS_DIR) / session_id
    meta_file = rec_dir / "metadata.json"
    telemetry_file = rec_dir / "telemetry.jsonl"
    if telemetry_file.exists() and not meta_file.exists():
        rec_dir.mkdir(parents=True, exist_ok=True)
        meta_file.write_text(
            json.dumps(
                {
                    "session_id":     session_id,
                    "session_type":   session_type,
                    "zone_id":        zone_id,
                    "finish_reason":  "aborted",
                    "recorded_at":    now,
                    "teams":          [],
                    "final_rankings": [],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        db_phase = "recording_ready"
    else:
        db_phase = "aborted"

    with get_db(DB_PATH) as conn:
        db_mark_session_aborted(conn, session_id, db_phase, now)
    _zone_running_session.pop(zone_id, None)
    await _broadcast("aborted", zone_id=zone_id, session_id=session_id)


# ---------------------------------------------------------------------------
# Legacy endpoints (default zone, backward compat)
# ---------------------------------------------------------------------------


@router.post("/lock-submissions")
async def lock_submissions(_auth=Depends(require_admin)):
    import server.blueprints.submission as sub_module

    sub_module.submissions_locked = True
    sub_module._save_lock_state(True)

    # Transition all zones from REGISTRATION → IDLE
    from server.race.state_machine import RaceState, all_zone_ids, get_zone_sm
    for zone_id in all_zone_ids():
        sm = get_zone_sm(zone_id)
        if sm.state == RaceState.REGISTRATION:
            sm.transition(RaceState.IDLE)

    return {"status": "locked"}


@router.post("/unlock-submissions")
async def unlock_submissions(_auth=Depends(require_admin)):
    from server.blueprints.submission import _save_lock_state
    import server.blueprints.submission as sub_module

    sub_module.submissions_locked = False
    _save_lock_state(False)

    # Transition all zones from IDLE → REGISTRATION
    from server.race.state_machine import RaceState, all_zone_ids, get_zone_sm
    for zone_id in all_zone_ids():
        sm = get_zone_sm(zone_id)
        if sm.state == RaceState.IDLE:
            sm.transition(RaceState.REGISTRATION)

    return {"status": "unlocked"}


@router.post("/set-session")
async def set_session(body: SetSessionBody, _auth=Depends(require_admin)):
    zone_body = ZoneSetSessionBody(
        session_type=body.session_type,
        session_id=body.session_id,
        team_ids=body.team_ids,
        total_laps=body.total_laps,
    )
    with get_db(DB_PATH) as conn:
        db_ensure_default_zone(conn, datetime.datetime.now().isoformat())
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
