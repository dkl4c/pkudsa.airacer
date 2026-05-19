"""
database/action.py — 数据库 CRUD 操作封装

职责：将所有原始 SQL 语句集中封装，使 blueprints/ 中的代码只调用函数，不直接写 SQL。
所有函数接受 conn 参数，不自行管理事务，由调用方通过 with get_db() 控制事务边界。
"""

import datetime
import json
import uuid
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Zone
# ---------------------------------------------------------------------------


def db_list_zones(conn) -> List[Dict]:
    rows = conn.execute("""
        SELECT z.id, z.name, z.description, z.total_laps, z.created_at,
               COUNT(t.id) AS team_count
        FROM zones z
        LEFT JOIN teams t ON t.zone_id = z.id
        GROUP BY z.id
        ORDER BY z.created_at
    """).fetchall()
    return [dict(r) for r in rows]


def db_get_zone(conn, zone_id: str) -> Optional[Dict]:
    row = conn.execute(
        "SELECT id, name, total_laps FROM zones WHERE id=?", (zone_id,)
    ).fetchone()
    return dict(row) if row else None


def db_create_zone(
    conn, id: str, name: str, description: str, total_laps: int, created_at: str
):
    conn.execute(
        "INSERT INTO zones (id, name, description, total_laps, created_at) VALUES (?,?,?,?,?)",
        (id, name, description, total_laps, created_at),
    )


def db_delete_zone(conn, zone_id: str) -> bool:
    row = conn.execute("SELECT id FROM zones WHERE id=?", (zone_id,)).fetchone()
    if row is None:
        return False

    # Collect all team IDs in this zone
    team_ids = [
        r[0]
        for r in conn.execute(
            "SELECT id FROM teams WHERE zone_id=?", (zone_id,)
        ).fetchall()
    ]

    if team_ids:
        placeholders = ",".join("?" * len(team_ids))
        params = list(team_ids)

        # Delete test_runs for these submissions
        sub_ids = [
            r[0]
            for r in conn.execute(
                f"SELECT id FROM submissions WHERE team_id IN ({placeholders})", params
            ).fetchall()
        ]
        if sub_ids:
            sub_placeholders = ",".join("?" * len(sub_ids))
            sub_params = list(sub_ids)
            # Delete test_runs for these submissions
            conn.execute(
                f"DELETE FROM test_runs WHERE submission_id IN ({sub_placeholders})",
                sub_params,
            )

        # Delete submissions for these teams
        conn.execute(
            f"DELETE FROM submissions WHERE team_id IN ({placeholders})", params
        )

        # Delete the teams
        conn.execute(f"DELETE FROM teams WHERE id IN ({placeholders})", params)

    # Delete races belonging to this zone
    conn.execute("DELETE FROM races WHERE zone_id=?", (zone_id,))

    # Delete race_points belonging to sessions in this zone
    conn.execute(
        "DELETE FROM race_points WHERE session_id IN (SELECT id FROM races WHERE zone_id=?)",
        (zone_id,),
    )

    # Finally delete the zone
    conn.execute("DELETE FROM zones WHERE id=?", (zone_id,))
    return True


def db_ensure_default_zone(conn, now: str):
    conn.execute(
        "INSERT OR IGNORE INTO zones (id, name, description, total_laps, created_at) VALUES ('default','Default Zone','',3,?)",
        (now,),
    )


def db_get_zone_teams(conn, zone_id: str) -> List[Dict]:
    rows = conn.execute(
        """SELECT t.id, t.name, t.created_at,
                  s.slot_name AS active_slot,
                  s.submitted_at AS active_version
           FROM teams t
           LEFT JOIN submissions s
             ON s.team_id = t.id AND s.is_race_active = 1
           WHERE t.zone_id = ?
           ORDER BY t.name""",
        (zone_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def db_get_zone_standings(conn, zone_id: str) -> List[Dict]:
    rows = conn.execute(
        """SELECT rp.team_id, t.name,
                  SUM(rp.points) AS total_score,
                  MIN(rp.best_lap_time) AS best_lap_time,
                  COUNT(DISTINCT rp.session_id) AS finished_sessions
           FROM race_points rp
           JOIN teams t ON rp.team_id = t.id
           JOIN races r ON rp.session_id = r.id
           WHERE r.zone_id = ?
           GROUP BY rp.team_id
           ORDER BY total_score DESC""",
        (zone_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def db_resource_exists(conn, table: str, id_value: str) -> bool:
    """验证资源是否存在。"""
    if table not in ("zones", "teams", "submissions", "race_sessions"):
        raise ValueError(f"不支持的表: {table}")

    # 兼容旧调用方：race_sessions → races
    actual_table = "races" if table == "race_sessions" else table

    result = conn.execute(
        f"SELECT 1 FROM {actual_table} WHERE id = ? LIMIT 1",
        (id_value,),
    ).fetchone()
    return result is not None


def db_get_zone_team_count(conn, zone_id: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM teams WHERE zone_id=?", (zone_id,)
    ).fetchone()[0]


def db_get_zone_detailed(conn, zone_id: str) -> Optional[Dict]:
    """获取赛区完整信息：基本信息、队伍列表、排行榜。"""
    zone = conn.execute(
        "SELECT id, name, description, total_laps, created_at FROM zones WHERE id = ?",
        (zone_id,),
    ).fetchone()

    if not zone:
        return None

    teams = conn.execute(
        "SELECT id, name, created_at FROM teams WHERE zone_id = ? ORDER BY created_at",
        (zone_id,),
    ).fetchall()

    standings = conn.execute(
        """SELECT rp.team_id, t.name, SUM(rp.points) AS points
           FROM race_points rp
           JOIN teams t ON rp.team_id = t.id
           JOIN races r ON rp.session_id = r.id
           WHERE r.zone_id = ?
           GROUP BY rp.team_id
           ORDER BY points DESC""",
        (zone_id,),
    ).fetchall()

    return {
        **dict(zone),
        "teams": [dict(t) for t in teams],
        "standings": [dict(s) for s in standings],
    }


def db_get_teams_by_zone(
    conn,
    zone_id: str,
    include_stats: bool = False,
) -> List[Dict]:
    """获取赛区所有队伍，可选包含统计数据。"""
    if not include_stats:
        rows = conn.execute(
            "SELECT id, name, zone_id, created_at FROM teams WHERE zone_id = ? ORDER BY created_at",
            (zone_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT t.id, t.name, t.created_at,
                      (SELECT COUNT(*) FROM submissions WHERE team_id = t.id) AS submissions_count,
                      (SELECT COUNT(*) FROM submissions WHERE team_id = t.id AND is_race_active = 1) AS active_race_count
               FROM teams t WHERE zone_id = ?
               ORDER BY t.created_at""",
            (zone_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Zone session preparation
# ---------------------------------------------------------------------------


def db_get_zone_team_ids(conn, zone_id: str) -> List[str]:
    rows = conn.execute(
        "SELECT id FROM teams WHERE zone_id=? ORDER BY name", (zone_id,)
    ).fetchall()
    return [r["id"] for r in rows]


def db_get_teams_with_code(conn, team_ids: List[str]) -> List[Dict]:
    """
    Return [{id, name, code_path}] for the given team_ids, preserving order.
    code_path prefers race-active slot, falls back to main active slot.
    Raises ValueError listing any team_ids not found.
    """
    if not team_ids:
        return []
    placeholders = ",".join("?" * len(team_ids))
    rows = conn.execute(
        f"""SELECT t.id, t.name,
                   COALESCE(
                       (SELECT code_path FROM submissions
                        WHERE team_id=t.id AND is_race_active=1 AND is_active=1 LIMIT 1),
                       (SELECT code_path FROM submissions
                        WHERE team_id=t.id AND slot_name='main' AND is_active=1
                        ORDER BY submitted_at DESC LIMIT 1)
                   ) AS code_path
            FROM teams t
            WHERE t.id IN ({placeholders})""",
        team_ids,
    ).fetchall()
    found = {r["id"] for r in rows}
    missing = [tid for tid in team_ids if tid not in found]
    if missing:
        raise ValueError(f"Teams not found: {missing}")
    order = {tid: idx for idx, tid in enumerate(team_ids)}
    return sorted([dict(r) for r in rows], key=lambda r: order[r["id"]])


# ---------------------------------------------------------------------------
# Races — unified table (test events + official races)
# ---------------------------------------------------------------------------


def create_race(
    conn,
    race_id: str,
    race_type: str,
    zone_id: str,
    participant_ids: List[str],
    status: str = "waiting",
    world_key: str = "complex",
    total_laps: int = 3,
    initiator: Optional[str] = None,
    name: Optional[str] = None,
    created_at: Optional[str] = None,
) -> None:
    """创建一条 race 记录。"""
    if created_at is None:
        created_at = datetime.datetime.now(datetime.UTC).isoformat()
    conn.execute(
        """INSERT INTO races
           (id, type, zone_id, initiator, participant_ids, status,
            world_key, total_laps, created_at, name)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            race_id,
            race_type,
            zone_id,
            initiator,
            json.dumps(participant_ids),
            status,
            world_key,
            total_laps,
            created_at,
            name,
        ),
    )


def update_race(conn, race_id: str, **kwargs) -> None:
    allowed = {
        "status",
        "started_at",
        "finished_at",
        "finish_reason",
        "result",
    }
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    if "result" in fields and not isinstance(fields["result"], str):
        fields["result"] = json.dumps(fields["result"])
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [race_id]
    conn.execute(f"UPDATE races SET {set_clause} WHERE id = ?", values)


def get_race(conn, race_id: str) -> Optional[Dict]:
    row = conn.execute("SELECT * FROM races WHERE id = ?", (race_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    if d.get("participant_ids"):
        d["participant_ids"] = json.loads(d["participant_ids"])
    return d


def get_waiting_race(conn, zone_id: str) -> Optional[Dict]:
    """获取指定赛区下一个待开始的 race。"""
    row = conn.execute(
        """SELECT id, type, total_laps, participant_ids, name FROM races
           WHERE status='waiting' AND zone_id=?
           ORDER BY rowid ASC LIMIT 1""",
        (zone_id,),
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    if d.get("participant_ids"):
        d["participant_ids"] = json.loads(d["participant_ids"])
    return d


def get_running_race(conn, zone_id: str) -> Optional[Dict]:
    """获取指定赛区正在进行的 race。"""
    row = conn.execute(
        "SELECT id, type FROM races WHERE status='running' AND zone_id=? ORDER BY rowid DESC LIMIT 1",
        (zone_id,),
    ).fetchone()
    return dict(row) if row else None


def list_races_by_participant(conn, team_id: str, limit: int = 20) -> List[Dict]:
    """查找某队伍参与的所有 race（发起或被邀请）。"""
    rows = conn.execute(
        """SELECT * FROM races
           WHERE participant_ids LIKE ?
           ORDER BY created_at DESC
           LIMIT ?""",
        (f"%{team_id}%", limit),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------


def create_team(
    conn,
    team_id: str,
    name: str,
    password_hash: str,
    zone_id: str = "default",
) -> None:
    conn.execute(
        "INSERT INTO teams (id, name, password_hash, zone_id, created_at) VALUES (?, ?, ?, ?, ?)",
        (
            team_id,
            name,
            password_hash,
            zone_id,
            datetime.datetime.now(datetime.UTC).isoformat(),
        ),
    )


def get_team(conn, team_id: str) -> Optional[Dict]:
    row = conn.execute(
        "SELECT id, name, password_hash, created_at FROM teams WHERE id = ?",
        (team_id,),
    ).fetchone()
    return dict(row) if row else None


def db_get_team_secure(conn, team_id: str) -> Optional[Dict]:
    """获取team完整信息（含 zone_id），用于鉴权和操作。"""
    row = conn.execute(
        "SELECT id, name, password_hash, zone_id, created_at FROM teams WHERE id = ?",
        (team_id,),
    ).fetchone()
    return dict(row) if row else None


def list_teams(conn) -> List[Dict]:
    rows = conn.execute("SELECT id, name, zone_id FROM teams ORDER BY name").fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Submissions
# ---------------------------------------------------------------------------


def create_submission(
    conn,
    team_id: str,
    code_path: str,
    submitted_at: str,
    slot_name: str = "main",
) -> str:
    """创建提交（委托给 db_create_submission_with_slot）。"""
    return db_create_submission_with_slot(
        conn,
        team_id,
        code_path,
        slot_name,
        submitted_at=submitted_at,
    )


def get_active_submission(conn, team_id: str) -> Optional[Dict]:
    row = conn.execute(
        """SELECT id, team_id, code_path, submitted_at FROM submissions
               WHERE team_id = ? AND is_active = 1
               ORDER BY submitted_at DESC LIMIT 1""",
        (team_id,),
    ).fetchone()
    return dict(row) if row else None


def db_get_submission_by_slot(conn, team_id: str, slot_name: str) -> Optional[Dict]:
    """获取指定 slot 的最新活跃提交。"""
    row = conn.execute(
        """SELECT id, code_path, submitted_at, is_race_active
           FROM submissions
           WHERE team_id = ? AND slot_name = ? AND is_active = 1
           ORDER BY submitted_at DESC LIMIT 1""",
        (team_id, slot_name),
    ).fetchone()
    return dict(row) if row else None


def db_create_submission_with_slot(
    conn,
    team_id: str,
    code_path: str,
    slot_name: str,
    submitted_at: Optional[str] = None,
) -> str:
    """创建提交：禁用该 slot 旧版本 + 创建新提交，自动处理 is_race_active。"""
    # 检查该 slot 是否已有竞速活跃提交
    has_race_active = conn.execute(
        """SELECT COUNT(*) AS cnt FROM submissions
           WHERE team_id = ? AND slot_name = ? AND is_race_active = 1""",
        (team_id, slot_name),
    ).fetchone()["cnt"]

    # 禁用该 slot 的所有旧提交
    conn.execute(
        "UPDATE submissions SET is_active = 0 WHERE team_id = ? AND slot_name = ?",
        (team_id, slot_name),
    )

    # 创建新提交
    submission_id = str(uuid.uuid4())
    now = submitted_at or datetime.datetime.now(datetime.UTC).isoformat()
    conn.execute(
        """INSERT INTO submissions
           (id, team_id, code_path, submitted_at, is_active, slot_name, is_race_active)
           VALUES (?, ?, ?, ?, 1, ?, ?)""",
        (
            submission_id,
            team_id,
            code_path,
            now,
            slot_name,
            1 if not has_race_active else 0,
        ),
    )

    # 如果该 team 没有任何 race_active 提交，则自动激活新提交
    any_race_active = conn.execute(
        "SELECT COUNT(*) FROM submissions WHERE team_id = ? AND is_race_active = 1",
        (team_id,),
    ).fetchone()[0]
    if not any_race_active:
        conn.execute(
            "UPDATE submissions SET is_race_active = 1 WHERE id = ?",
            (submission_id,),
        )

    return submission_id


def db_activate_submission_slot(
    conn,
    team_id: str,
    slot_name: str,
) -> bool:
    """激活指定 slot 为竞速提交（禁用同 team 其他 slot 的竞速活跃）。"""
    target = conn.execute(
        """SELECT id FROM submissions
           WHERE team_id = ? AND slot_name = ? AND is_active = 1
           ORDER BY submitted_at DESC LIMIT 1""",
        (team_id, slot_name),
    ).fetchone()

    if not target:
        return False

    # 禁用该 team 所有提交的竞速活跃
    conn.execute(
        "UPDATE submissions SET is_race_active = 0 WHERE team_id = ?",
        (team_id,),
    )

    # 激活目标 slot
    conn.execute(
        "UPDATE submissions SET is_race_active = 1 WHERE id = ?",
        (target["id"],),
    )

    return True


def db_get_submission_by_id(conn, submission_id: str) -> Optional[Dict]:
    """按 submission_id 查询单条提交记录。"""
    row = conn.execute(
        "SELECT id, team_id, code_path, submitted_at, slot_name, is_active, is_race_active "
        "FROM submissions WHERE id = ?",
        (submission_id,),
    ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# TestRuns
# ---------------------------------------------------------------------------


def create_test_run(
    conn, submission_id: str, queued_at: str, world_key: str = "complex"
) -> int:
    cur = conn.execute(
        "INSERT INTO test_runs (submission_id, status, queued_at, world_key) VALUES (?, 'queued', ?, ?)",
        (submission_id, queued_at, world_key),
    )
    return cur.lastrowid


def update_test_run(conn, test_run_id: int, **kwargs) -> None:
    allowed = {
        "status",
        "started_at",
        "finished_at",
        "laps_completed",
        "best_lap_time",
        "collisions_minor",
        "collisions_major",
        "timeout_warnings",
        "finish_reason",
    }
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [test_run_id]
    conn.execute(f"UPDATE test_runs SET {set_clause} WHERE id = ?", values)


def get_latest_test_run(conn, submission_id: str) -> Optional[Dict]:
    row = conn.execute(
        "SELECT * FROM test_runs WHERE submission_id = ? ORDER BY id DESC LIMIT 1",
        (submission_id,),
    ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# RacePoints
# ---------------------------------------------------------------------------


def upsert_race_points(
    conn,
    race_id: str,
    team_id: str,
    rank: int,
    points: int,
    best_lap_time: Optional[float] = None,
) -> None:
    conn.execute(
        """INSERT INTO race_points (team_id, session_id, rank, points, best_lap_time)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(team_id, session_id) DO UPDATE SET
               rank=excluded.rank, points=excluded.points, best_lap_time=excluded.best_lap_time""",
        (team_id, race_id, rank, points, best_lap_time),
    )


def get_standings(conn) -> List[Dict]:
    rows = conn.execute(
        """SELECT rp.team_id, t.name,
                  SUM(rp.points) as total_score,
                  MIN(rp.best_lap_time) AS best_lap_time,
                  COUNT(DISTINCT rp.session_id) AS finished_sessions
               FROM race_points rp
               JOIN teams t ON rp.team_id = t.id
               GROUP BY rp.team_id
               ORDER BY total_score DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Placement rankings & stage results (for bracket / grouping)
# ---------------------------------------------------------------------------


def db_get_placement_rankings(conn, zone_id: str) -> list[dict]:
    """
    Return teams ranked by placement lap time (ascending).

    Queries all finished placement sessions for the zone, parses result JSON,
    extracts best_lap_time per team, and sorts fastest-first.

    Returns [{team_id, best_lap_time}, ...]
    """
    rows = conn.execute(
        """SELECT result FROM races
           WHERE zone_id=? AND type='placement'
           AND status='done'
           AND result IS NOT NULL""",
        (zone_id,),
    ).fetchall()

    rankings: list[dict] = []
    seen: set[str] = set()
    for (result_json,) in rows:
        data = (
            json.loads(result_json)
            if isinstance(result_json, str)
            else (result_json or {})
        )
        for entry in data.get("final_rankings", []):
            tid = entry.get("team_id")
            if not tid or tid in seen:
                continue
            t = entry.get("best_lap_time") or entry.get("best_lap")
            if t is not None:
                seen.add(tid)
                rankings.append({"team_id": tid, "best_lap_time": t})

    rankings.sort(key=lambda r: r["best_lap_time"])
    return rankings


def db_get_stage_session_results(conn, zone_id: str, stage: str) -> list[dict]:
    """
    Return parsed results for all finished sessions of a given stage.

    Returns [{session_id, rankings: [{team_id, rank, finish_time, best_lap_time}]}, ...]
    """
    rows = conn.execute(
        """SELECT id, result FROM races
           WHERE zone_id=? AND type=? AND status='done'
           AND result IS NOT NULL""",
        (zone_id, stage),
    ).fetchall()

    results: list[dict] = []
    for sid, result_json in rows:
        data = (
            json.loads(result_json)
            if isinstance(result_json, str)
            else (result_json or {})
        )
        rankings: list[dict] = []
        for entry in data.get("final_rankings", []):
            rankings.append(
                {
                    "team_id": entry.get("team_id"),
                    "rank": entry.get("rank", 99),
                    "finish_time": (
                        entry.get("finish_time")
                        or entry.get("race_time")
                        or entry.get("total_time")
                    ),
                    "best_lap_time": (
                        entry.get("best_lap_time") or entry.get("best_lap")
                    ),
                }
            )
        results.append({"session_id": sid, "rankings": rankings})
    return results
