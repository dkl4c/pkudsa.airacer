"""
Team and zone public endpoints.
No auth required — read-only public data.
"""

import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from server.config.config import DB_PATH
from server.database.models import get_db
from server.race.state_machine import get_zone_sm

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    zone_id:   str
    team_id:   str
    team_name: str
    password:  str


# ---------------------------------------------------------------------------
# GET /api/zones — public zone list
# ---------------------------------------------------------------------------

@router.get("/zones")
async def list_zones():
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
        result.append({
            "id":          r["id"],
            "name":        r["name"],
            "description": r["description"],
            "total_laps":  r["total_laps"],
            "created_at":  r["created_at"],
            "team_count":  r["team_count"],
            "state":       sm.state.value,
        })
    return result


# ---------------------------------------------------------------------------
# GET /api/zones/{zone_id} — single zone public detail
# ---------------------------------------------------------------------------

@router.get("/zones/{zone_id}")
async def get_zone(zone_id: str):
    with get_db(DB_PATH) as conn:
        row = conn.execute(
            "SELECT id, name, description, total_laps, created_at FROM zones WHERE id=?",
            (zone_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"赛区未找到: {zone_id}")

        teams = conn.execute(
            "SELECT id, name, created_at FROM teams WHERE zone_id=? ORDER BY created_at",
            (zone_id,)
        ).fetchall()

        standings = conn.execute("""
            SELECT rp.team_id, t.name, SUM(rp.points) AS total_points
            FROM race_points rp
            JOIN teams t ON rp.team_id = t.id
            JOIN race_sessions rs ON rp.session_id = rs.id
            WHERE rs.zone_id = ?
            GROUP BY rp.team_id
            ORDER BY total_points DESC
        """, (zone_id,)).fetchall()

    sm = get_zone_sm(zone_id)
    return {
        "id":          row["id"],
        "name":        row["name"],
        "description": row["description"],
        "total_laps":  row["total_laps"],
        "created_at":  row["created_at"],
        "state":       sm.state.value,
        "teams":       [{"id": t["id"], "name": t["name"]} for t in teams],
        "standings":   [dict(s) for s in standings],
    }


# ---------------------------------------------------------------------------
# POST /api/register — team self-registration
# ---------------------------------------------------------------------------

@router.post("/register")
async def register_team(body: RegisterRequest):
    import re
    import bcrypt as _bcrypt

    if not body.zone_id or not body.team_id or not body.team_name or not body.password:
        raise HTTPException(status_code=400, detail="所有字段均为必填")

    if not re.match(r'^[a-zA-Z0-9_]{2,20}$', body.team_id):
        raise HTTPException(
            status_code=400,
            detail="队伍ID只允许字母/数字/下划线，长度2-20"
        )

    password_hash = _bcrypt.hashpw(body.password.encode(), _bcrypt.gensalt()).decode()
    now = datetime.datetime.now().isoformat()

    with get_db(DB_PATH) as conn:
        zone = conn.execute(
            "SELECT id FROM zones WHERE id=?", (body.zone_id,)
        ).fetchone()
        if zone is None:
            raise HTTPException(status_code=404, detail=f"赛区不存在: {body.zone_id}")

        existing = conn.execute(
            "SELECT id FROM teams WHERE id=?", (body.team_id,)
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail=f"队伍ID已被占用: {body.team_id}")

        conn.execute(
            "INSERT INTO teams (id, name, password_hash, created_at, zone_id) VALUES (?,?,?,?,?)",
            (body.team_id, body.team_name, password_hash, now, body.zone_id),
        )

    return {"status": "registered", "team_id": body.team_id, "zone_id": body.zone_id}


# ---------------------------------------------------------------------------
# GET /api/teams — list all teams (optionally filtered by zone)
# ---------------------------------------------------------------------------

@router.get("/teams")
async def list_teams(zone_id: str = None):
    with get_db(DB_PATH) as conn:
        if zone_id:
            rows = conn.execute(
                "SELECT id, name, zone_id FROM teams WHERE zone_id=? ORDER BY name",
                (zone_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, name, zone_id FROM teams ORDER BY name"
            ).fetchall()
    return [{"id": r["id"], "name": r["name"], "zone_id": r["zone_id"]} for r in rows]
