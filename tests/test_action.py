"""
Tests for server/database/action.py

每个测试函数通过 conn fixture 获得独立的临时 SQLite 数据库（tmp_path 隔离），
无需手动清理上一次的数据。
"""

import json
import sqlite3

import pytest

from server.database.models import init_db
from server.database.action import (
    # Zone
    db_list_zones,
    db_get_zone,
    db_create_zone,
    db_delete_zone,
    db_ensure_default_zone,
    db_get_zone_teams,
    db_get_zone_standings,
    db_get_zone_team_count,
    # Zone session preparation
    db_get_zone_team_ids,
    db_get_teams_with_code,
    db_upsert_session,
    db_get_waiting_session,
    db_mark_session_running,
    db_get_running_session,
    db_mark_session_finished,
    db_mark_session_aborted,
    # Teams
    create_team,
    get_team,
    list_teams,
    # Submissions
    create_submission,
    get_active_submission,
    # TestRuns
    create_test_run,
    update_test_run,
    get_latest_test_run,
    # RaceSessions
    create_race_session,
    update_race_session,
    get_race_session,
    # RacePoints
    upsert_race_points,
    get_standings,
)

NOW = "2026-01-01T12:00:00"


@pytest.fixture
def conn(tmp_path):
    """每个测试独立的临时数据库，包含完整 schema。"""
    db_path = tmp_path / "test.db"
    init_db(db_path)
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    yield c
    c.rollback()
    c.close()


# ---------------------------------------------------------------------------
# 辅助函数：在测试中插入有 zone_id 的 team（create_team 不支持 zone_id）
# ---------------------------------------------------------------------------

def _insert_team(conn, team_id, name, zone_id):
    conn.execute(
        "INSERT INTO teams (id, name, password_hash, zone_id) VALUES (?, ?, ?, ?)",
        (team_id, name, "hash", zone_id),
    )


def _insert_submission(conn, sub_id, team_id, code_path, *, is_active=1, slot_name="main", is_race_active=0):
    conn.execute(
        "INSERT INTO submissions (id, team_id, code_path, submitted_at, is_active, slot_name, is_race_active) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (sub_id, team_id, code_path, NOW, is_active, slot_name, is_race_active),
    )


def _insert_race_session(conn, sess_id, zone_id, phase="finished"):
    conn.execute(
        "INSERT INTO race_sessions (id, type, team_ids, total_laps, phase, zone_id) VALUES (?, ?, ?, ?, ?, ?)",
        (sess_id, "qualifying", '[]', 3, phase, zone_id),
    )


# ===========================================================================
# Zone
# ===========================================================================

class TestDbListZones:
    def test_empty_database(self, conn):
        assert db_list_zones(conn) == []

    def test_returns_zone_fields(self, conn):
        db_create_zone(conn, "z1", "Zone One", "desc", 5, NOW)
        rows = db_list_zones(conn)
        assert len(rows) == 1
        assert rows[0]["id"] == "z1"
        assert rows[0]["name"] == "Zone One"
        assert rows[0]["total_laps"] == 5

    def test_team_count_aggregated(self, conn):
        db_create_zone(conn, "z1", "Zone 1", "", 3, NOW)
        _insert_team(conn, "t1", "T1", "z1")
        _insert_team(conn, "t2", "T2", "z1")
        rows = db_list_zones(conn)
        assert rows[0]["team_count"] == 2

    def test_zone_without_teams_has_zero_count(self, conn):
        db_create_zone(conn, "z1", "Zone 1", "", 3, NOW)
        rows = db_list_zones(conn)
        assert rows[0]["team_count"] == 0

    def test_ordered_by_created_at(self, conn):
        db_create_zone(conn, "z1", "Zone 1", "", 3, "2026-01-01T10:00:00")
        db_create_zone(conn, "z2", "Zone 2", "", 3, "2026-01-01T11:00:00")
        rows = db_list_zones(conn)
        assert [r["id"] for r in rows] == ["z1", "z2"]


class TestDbGetZone:
    def test_returns_zone_when_found(self, conn):
        db_create_zone(conn, "z1", "Zone 1", "desc", 5, NOW)
        result = db_get_zone(conn, "z1")
        assert result is not None
        assert result["id"] == "z1"
        assert result["total_laps"] == 5

    def test_returns_none_when_missing(self, conn):
        assert db_get_zone(conn, "nonexistent") is None


class TestDbCreateZone:
    def test_creates_zone(self, conn):
        db_create_zone(conn, "z1", "Zone 1", "desc", 3, NOW)
        row = conn.execute("SELECT * FROM zones WHERE id='z1'").fetchone()
        assert row["name"] == "Zone 1"
        assert row["description"] == "desc"

    def test_duplicate_id_raises_integrity_error(self, conn):
        db_create_zone(conn, "z1", "Zone 1", "", 3, NOW)
        with pytest.raises(sqlite3.IntegrityError):
            db_create_zone(conn, "z1", "Zone 1 Dup", "", 3, NOW)


class TestDbDeleteZone:
    def test_deletes_existing_zone(self, conn):
        db_create_zone(conn, "z1", "Zone 1", "", 3, NOW)
        result = db_delete_zone(conn, "z1")
        assert result is True
        assert conn.execute("SELECT id FROM zones WHERE id='z1'").fetchone() is None

    def test_returns_false_for_nonexistent(self, conn):
        assert db_delete_zone(conn, "nonexistent") is False


class TestDbEnsureDefaultZone:
    def test_creates_default_zone(self, conn):
        db_ensure_default_zone(conn, NOW)
        row = conn.execute("SELECT id FROM zones WHERE id='default'").fetchone()
        assert row is not None

    def test_idempotent_on_second_call(self, conn):
        db_ensure_default_zone(conn, NOW)
        db_ensure_default_zone(conn, NOW)
        count = conn.execute("SELECT COUNT(*) FROM zones WHERE id='default'").fetchone()[0]
        assert count == 1


class TestDbGetZoneTeams:
    def test_returns_teams_in_zone(self, conn):
        db_create_zone(conn, "z1", "Zone 1", "", 3, NOW)
        _insert_team(conn, "t1", "Team 1", "z1")
        rows = db_get_zone_teams(conn, "z1")
        assert len(rows) == 1
        assert rows[0]["id"] == "t1"

    def test_active_slot_from_race_active_submission(self, conn):
        db_create_zone(conn, "z1", "Zone 1", "", 3, NOW)
        _insert_team(conn, "t1", "Team 1", "z1")
        _insert_submission(conn, "s1", "t1", "/code.py", slot_name="dev", is_race_active=1)
        rows = db_get_zone_teams(conn, "z1")
        assert rows[0]["active_slot"] == "dev"

    def test_active_slot_none_without_race_active_submission(self, conn):
        db_create_zone(conn, "z1", "Zone 1", "", 3, NOW)
        _insert_team(conn, "t1", "Team 1", "z1")
        _insert_submission(conn, "s1", "t1", "/code.py", is_race_active=0)
        rows = db_get_zone_teams(conn, "z1")
        assert rows[0]["active_slot"] is None

    def test_ordered_by_name(self, conn):
        db_create_zone(conn, "z1", "Zone 1", "", 3, NOW)
        _insert_team(conn, "t2", "Beta", "z1")
        _insert_team(conn, "t1", "Alpha", "z1")
        rows = db_get_zone_teams(conn, "z1")
        assert [r["id"] for r in rows] == ["t1", "t2"]

    def test_excludes_teams_from_other_zones(self, conn):
        db_create_zone(conn, "z1", "Zone 1", "", 3, NOW)
        db_create_zone(conn, "z2", "Zone 2", "", 3, NOW)
        _insert_team(conn, "t1", "Team 1", "z1")
        _insert_team(conn, "t2", "Team 2", "z2")
        rows = db_get_zone_teams(conn, "z1")
        assert len(rows) == 1
        assert rows[0]["id"] == "t1"


class TestDbGetZoneStandings:
    def test_returns_standings_with_total_points(self, conn):
        db_create_zone(conn, "z1", "Zone 1", "", 3, NOW)
        _insert_team(conn, "t1", "Team 1", "z1")
        _insert_race_session(conn, "sess1", "z1")
        upsert_race_points(conn, "sess1", "t1", 1, 10)
        rows = db_get_zone_standings(conn, "z1")
        assert len(rows) == 1
        assert rows[0]["total_points"] == 10

    def test_sums_across_multiple_sessions(self, conn):
        db_create_zone(conn, "z1", "Zone 1", "", 3, NOW)
        _insert_team(conn, "t1", "Team 1", "z1")
        _insert_race_session(conn, "sess1", "z1")
        _insert_race_session(conn, "sess2", "z1")
        upsert_race_points(conn, "sess1", "t1", 1, 10)
        upsert_race_points(conn, "sess2", "t1", 2, 7)
        rows = db_get_zone_standings(conn, "z1")
        assert rows[0]["total_points"] == 17

    def test_ordered_by_total_points_desc(self, conn):
        db_create_zone(conn, "z1", "Zone 1", "", 3, NOW)
        _insert_team(conn, "t1", "Team 1", "z1")
        _insert_team(conn, "t2", "Team 2", "z1")
        _insert_race_session(conn, "sess1", "z1")
        upsert_race_points(conn, "sess1", "t1", 2, 7)
        upsert_race_points(conn, "sess1", "t2", 1, 10)
        rows = db_get_zone_standings(conn, "z1")
        assert rows[0]["team_id"] == "t2"
        assert rows[1]["team_id"] == "t1"

    def test_excludes_other_zones_sessions(self, conn):
        db_create_zone(conn, "z1", "Zone 1", "", 3, NOW)
        db_create_zone(conn, "z2", "Zone 2", "", 3, NOW)
        _insert_team(conn, "t1", "Team 1", "z1")
        _insert_race_session(conn, "sess1", "z2")  # 属于 z2
        upsert_race_points(conn, "sess1", "t1", 1, 10)
        rows = db_get_zone_standings(conn, "z1")
        assert rows == []


class TestDbGetZoneTeamCount:
    def test_counts_teams(self, conn):
        db_create_zone(conn, "z1", "Zone 1", "", 3, NOW)
        _insert_team(conn, "t1", "T1", "z1")
        _insert_team(conn, "t2", "T2", "z1")
        assert db_get_zone_team_count(conn, "z1") == 2

    def test_empty_zone_returns_zero(self, conn):
        db_create_zone(conn, "z1", "Zone 1", "", 3, NOW)
        assert db_get_zone_team_count(conn, "z1") == 0


# ===========================================================================
# Zone session preparation
# ===========================================================================

class TestDbGetZoneTeamIds:
    def test_returns_ids_ordered_by_name(self, conn):
        db_create_zone(conn, "z1", "Zone 1", "", 3, NOW)
        _insert_team(conn, "t2", "Beta", "z1")
        _insert_team(conn, "t1", "Alpha", "z1")
        ids = db_get_zone_team_ids(conn, "z1")
        assert ids == ["t1", "t2"]

    def test_empty_zone(self, conn):
        db_create_zone(conn, "z1", "Zone 1", "", 3, NOW)
        assert db_get_zone_team_ids(conn, "z1") == []


class TestDbGetTeamsWithCode:
    def test_returns_race_active_slot_first(self, conn, tmp_path):
        code_file = tmp_path / "code.py"
        code_file.write_text("# code")
        db_create_zone(conn, "z1", "Zone 1", "", 3, NOW)
        _insert_team(conn, "t1", "Team 1", "z1")
        _insert_submission(conn, "s1", "t1", str(code_file), is_race_active=1)
        rows = db_get_teams_with_code(conn, ["t1"])
        assert rows[0]["code_path"] == str(code_file)

    def test_falls_back_to_main_slot(self, conn, tmp_path):
        code_file = tmp_path / "code.py"
        code_file.write_text("# code")
        db_create_zone(conn, "z1", "Zone 1", "", 3, NOW)
        _insert_team(conn, "t1", "Team 1", "z1")
        _insert_submission(conn, "s1", "t1", str(code_file), slot_name="main", is_race_active=0)
        rows = db_get_teams_with_code(conn, ["t1"])
        assert rows[0]["code_path"] == str(code_file)

    def test_no_submission_returns_null_code_path(self, conn):
        db_create_zone(conn, "z1", "Zone 1", "", 3, NOW)
        _insert_team(conn, "t1", "Team 1", "z1")
        rows = db_get_teams_with_code(conn, ["t1"])
        assert rows[0]["code_path"] is None

    def test_missing_team_raises_value_error(self, conn):
        with pytest.raises(ValueError, match="Teams not found"):
            db_get_teams_with_code(conn, ["nonexistent"])

    def test_preserves_input_order(self, conn):
        db_create_zone(conn, "z1", "Zone 1", "", 3, NOW)
        for tid, name in [("t1", "Alpha"), ("t2", "Beta"), ("t3", "Gamma")]:
            _insert_team(conn, tid, name, "z1")
        rows = db_get_teams_with_code(conn, ["t3", "t1", "t2"])
        assert [r["id"] for r in rows] == ["t3", "t1", "t2"]

    def test_empty_list_returns_empty(self, conn):
        assert db_get_teams_with_code(conn, []) == []


class TestDbUpsertSession:
    def test_inserts_new_session(self, conn):
        db_create_zone(conn, "z1", "Zone 1", "", 3, NOW)
        db_upsert_session(conn, "sess1", "qualifying", ["t1"], 3, "z1")
        row = conn.execute("SELECT * FROM race_sessions WHERE id='sess1'").fetchone()
        assert row["phase"] == "waiting"
        assert row["zone_id"] == "z1"
        assert json.loads(row["team_ids"]) == ["t1"]

    def test_updates_on_conflict(self, conn):
        db_create_zone(conn, "z1", "Zone 1", "", 3, NOW)
        db_upsert_session(conn, "sess1", "qualifying", ["t1"], 3, "z1")
        db_upsert_session(conn, "sess1", "group_race", ["t1", "t2"], 5, "z1")
        row = conn.execute("SELECT type, total_laps, phase FROM race_sessions WHERE id='sess1'").fetchone()
        assert row["type"] == "group_race"
        assert row["total_laps"] == 5
        assert row["phase"] == "waiting"  # 重置为 waiting


class TestDbGetWaitingSession:
    def test_returns_session_in_waiting_phase(self, conn):
        db_create_zone(conn, "z1", "Zone 1", "", 3, NOW)
        db_upsert_session(conn, "sess1", "qualifying", [], 3, "z1")
        result = db_get_waiting_session(conn, "z1")
        assert result is not None
        assert result["id"] == "sess1"

    def test_returns_none_when_no_waiting(self, conn):
        db_create_zone(conn, "z1", "Zone 1", "", 3, NOW)
        assert db_get_waiting_session(conn, "z1") is None

    def test_ignores_non_waiting_phases(self, conn):
        db_create_zone(conn, "z1", "Zone 1", "", 3, NOW)
        _insert_race_session(conn, "sess1", "z1", phase="running")
        assert db_get_waiting_session(conn, "z1") is None

    def test_returns_latest_when_multiple(self, conn):
        db_create_zone(conn, "z1", "Zone 1", "", 3, NOW)
        db_upsert_session(conn, "sess1", "qualifying", [], 3, "z1")
        db_upsert_session(conn, "sess2", "group_race", [], 3, "z1")
        result = db_get_waiting_session(conn, "z1")
        assert result["id"] == "sess2"


class TestDbMarkSessionRunning:
    def test_sets_phase_and_started_at(self, conn):
        db_create_zone(conn, "z1", "Zone 1", "", 3, NOW)
        db_upsert_session(conn, "sess1", "qualifying", [], 3, "z1")
        db_mark_session_running(conn, "sess1", NOW)
        row = conn.execute("SELECT phase, started_at FROM race_sessions WHERE id='sess1'").fetchone()
        assert row["phase"] == "running"
        assert row["started_at"] == NOW


class TestDbGetRunningSession:
    def test_returns_running_session(self, conn):
        db_create_zone(conn, "z1", "Zone 1", "", 3, NOW)
        _insert_race_session(conn, "sess1", "z1", phase="running")
        result = db_get_running_session(conn, "z1")
        assert result is not None
        assert result["id"] == "sess1"

    def test_returns_none_when_no_running(self, conn):
        db_create_zone(conn, "z1", "Zone 1", "", 3, NOW)
        assert db_get_running_session(conn, "z1") is None

    def test_ignores_waiting_phase(self, conn):
        db_create_zone(conn, "z1", "Zone 1", "", 3, NOW)
        db_upsert_session(conn, "sess1", "qualifying", [], 3, "z1")
        assert db_get_running_session(conn, "z1") is None


class TestDbMarkSessionFinished:
    def test_sets_phase_to_recording_ready(self, conn):
        db_create_zone(conn, "z1", "Zone 1", "", 3, NOW)
        _insert_race_session(conn, "sess1", "z1", phase="running")
        db_mark_session_finished(conn, "sess1", NOW)
        row = conn.execute("SELECT phase, finished_at FROM race_sessions WHERE id='sess1'").fetchone()
        assert row["phase"] == "recording_ready"
        assert row["finished_at"] == NOW


class TestDbMarkSessionAborted:
    def test_sets_specified_phase(self, conn):
        db_create_zone(conn, "z1", "Zone 1", "", 3, NOW)
        _insert_race_session(conn, "sess1", "z1", phase="running")
        db_mark_session_aborted(conn, "sess1", "aborted", NOW)
        row = conn.execute("SELECT phase, finished_at FROM race_sessions WHERE id='sess1'").fetchone()
        assert row["phase"] == "aborted"
        assert row["finished_at"] == NOW

    def test_can_set_recording_ready_phase(self, conn):
        db_create_zone(conn, "z1", "Zone 1", "", 3, NOW)
        _insert_race_session(conn, "sess1", "z1", phase="running")
        db_mark_session_aborted(conn, "sess1", "recording_ready", NOW)
        row = conn.execute("SELECT phase FROM race_sessions WHERE id='sess1'").fetchone()
        assert row["phase"] == "recording_ready"


# ===========================================================================
# Teams
# ===========================================================================

class TestCreateTeam:
    def test_creates_team(self, conn):
        create_team(conn, "t1", "Team 1", "hashed_pw")
        row = conn.execute("SELECT * FROM teams WHERE id='t1'").fetchone()
        assert row is not None
        assert row["name"] == "Team 1"
        assert row["password_hash"] == "hashed_pw"

    def test_duplicate_id_raises(self, conn):
        create_team(conn, "t1", "Team 1", "h")
        with pytest.raises(sqlite3.IntegrityError):
            create_team(conn, "t1", "Team 1 Dup", "h")


class TestGetTeam:
    def test_returns_team_when_found(self, conn):
        create_team(conn, "t1", "Team 1", "h")
        result = get_team(conn, "t1")
        assert result is not None
        assert result["id"] == "t1"
        assert result["name"] == "Team 1"

    def test_returns_none_when_missing(self, conn):
        assert get_team(conn, "nonexistent") is None


class TestListTeams:
    def test_returns_all_teams_ordered_by_name(self, conn):
        create_team(conn, "t2", "Beta", "h")
        create_team(conn, "t1", "Alpha", "h")
        teams = list_teams(conn)
        assert [t["id"] for t in teams] == ["t1", "t2"]

    def test_empty_when_no_teams(self, conn):
        assert list_teams(conn) == []


# ===========================================================================
# Submissions
# ===========================================================================

class TestCreateSubmission:
    def test_creates_active_submission(self, conn):
        create_team(conn, "t1", "Team 1", "h")
        sub_id = create_submission(conn, "t1", "/code/v1.py", NOW)
        row = conn.execute("SELECT * FROM submissions WHERE id=?", (sub_id,)).fetchone()
        assert row is not None
        assert row["is_active"] == 1
        assert row["code_path"] == "/code/v1.py"

    def test_deactivates_previous_submissions(self, conn):
        create_team(conn, "t1", "Team 1", "h")
        id1 = create_submission(conn, "t1", "/code/v1.py", NOW)
        id2 = create_submission(conn, "t1", "/code/v2.py", NOW)
        assert conn.execute("SELECT is_active FROM submissions WHERE id=?", (id1,)).fetchone()["is_active"] == 0
        assert conn.execute("SELECT is_active FROM submissions WHERE id=?", (id2,)).fetchone()["is_active"] == 1

    def test_returns_unique_id_each_time(self, conn):
        create_team(conn, "t1", "Team 1", "h")
        id1 = create_submission(conn, "t1", "/code/v1.py", NOW)
        id2 = create_submission(conn, "t1", "/code/v2.py", NOW)
        assert id1 != id2


class TestGetActiveSubmission:
    def test_returns_active_submission(self, conn):
        create_team(conn, "t1", "Team 1", "h")
        sub_id = create_submission(conn, "t1", "/code/v1.py", NOW)
        result = get_active_submission(conn, "t1")
        assert result["id"] == sub_id

    def test_returns_none_when_no_submission(self, conn):
        create_team(conn, "t1", "Team 1", "h")
        assert get_active_submission(conn, "t1") is None

    def test_returns_latest_after_multiple(self, conn):
        create_team(conn, "t1", "Team 1", "h")
        create_submission(conn, "t1", "/code/v1.py", "2026-01-01T10:00:00")
        id2 = create_submission(conn, "t1", "/code/v2.py", "2026-01-01T11:00:00")
        result = get_active_submission(conn, "t1")
        assert result["id"] == id2


# ===========================================================================
# TestRuns
# ===========================================================================

class TestCreateTestRun:
    def test_creates_queued_run(self, conn):
        create_team(conn, "t1", "Team 1", "h")
        sub_id = create_submission(conn, "t1", "/code.py", NOW)
        run_id = create_test_run(conn, sub_id, NOW)
        row = conn.execute("SELECT * FROM test_runs WHERE id=?", (run_id,)).fetchone()
        assert row["status"] == "queued"
        assert row["submission_id"] == sub_id

    def test_returns_integer_rowid(self, conn):
        create_team(conn, "t1", "Team 1", "h")
        sub_id = create_submission(conn, "t1", "/code.py", NOW)
        run_id = create_test_run(conn, sub_id, NOW)
        assert isinstance(run_id, int)


class TestUpdateTestRun:
    def test_updates_allowed_fields(self, conn):
        create_team(conn, "t1", "Team 1", "h")
        sub_id = create_submission(conn, "t1", "/code.py", NOW)
        run_id = create_test_run(conn, sub_id, NOW)
        update_test_run(conn, run_id, status="done", laps_completed=3, best_lap_time=45.2)
        row = conn.execute("SELECT * FROM test_runs WHERE id=?", (run_id,)).fetchone()
        assert row["status"] == "done"
        assert row["laps_completed"] == 3
        assert row["best_lap_time"] == pytest.approx(45.2)

    def test_ignores_unknown_fields(self, conn):
        create_team(conn, "t1", "Team 1", "h")
        sub_id = create_submission(conn, "t1", "/code.py", NOW)
        run_id = create_test_run(conn, sub_id, NOW)
        update_test_run(conn, run_id, unknown_field="should_be_ignored")
        row = conn.execute("SELECT status FROM test_runs WHERE id=?", (run_id,)).fetchone()
        assert row["status"] == "queued"  # 未被改动

    def test_noop_when_no_kwargs(self, conn):
        create_team(conn, "t1", "Team 1", "h")
        sub_id = create_submission(conn, "t1", "/code.py", NOW)
        run_id = create_test_run(conn, sub_id, NOW)
        update_test_run(conn, run_id)  # 不应抛出异常


class TestGetLatestTestRun:
    def test_returns_most_recent(self, conn):
        create_team(conn, "t1", "Team 1", "h")
        sub_id = create_submission(conn, "t1", "/code.py", NOW)
        create_test_run(conn, sub_id, NOW)
        run_id2 = create_test_run(conn, sub_id, NOW)
        result = get_latest_test_run(conn, sub_id)
        assert result["id"] == run_id2

    def test_returns_none_when_not_found(self, conn):
        assert get_latest_test_run(conn, "nonexistent") is None


# ===========================================================================
# RaceSessions
# ===========================================================================

class TestCreateRaceSession:
    def test_creates_session(self, conn):
        create_race_session(conn, "sess1", "qualifying", ["t1", "t2"], 3, "waiting", NOW)
        row = conn.execute("SELECT * FROM race_sessions WHERE id='sess1'").fetchone()
        assert row is not None
        assert json.loads(row["team_ids"]) == ["t1", "t2"]
        assert row["phase"] == "waiting"


class TestUpdateRaceSession:
    def test_updates_phase_and_finished_at(self, conn):
        create_race_session(conn, "sess1", "qualifying", [], 3, "waiting", NOW)
        update_race_session(conn, "sess1", phase="finished", finished_at=NOW)
        row = conn.execute("SELECT phase, finished_at FROM race_sessions WHERE id='sess1'").fetchone()
        assert row["phase"] == "finished"
        assert row["finished_at"] == NOW

    def test_serializes_result_dict_to_json(self, conn):
        create_race_session(conn, "sess1", "qualifying", [], 3, "waiting", NOW)
        update_race_session(conn, "sess1", result={"winner": "t1", "laps": 3})
        row = conn.execute("SELECT result FROM race_sessions WHERE id='sess1'").fetchone()
        assert json.loads(row["result"]) == {"winner": "t1", "laps": 3}

    def test_noop_when_no_kwargs(self, conn):
        create_race_session(conn, "sess1", "qualifying", [], 3, "waiting", NOW)
        update_race_session(conn, "sess1")  # 不应抛出异常

    def test_ignores_unknown_fields(self, conn):
        create_race_session(conn, "sess1", "qualifying", [], 3, "waiting", NOW)
        update_race_session(conn, "sess1", unknown_field="value")
        row = conn.execute("SELECT phase FROM race_sessions WHERE id='sess1'").fetchone()
        assert row["phase"] == "waiting"


class TestGetRaceSession:
    def test_deserializes_team_ids(self, conn):
        create_race_session(conn, "sess1", "qualifying", ["t1", "t2"], 3, "waiting", NOW)
        result = get_race_session(conn, "sess1")
        assert result["team_ids"] == ["t1", "t2"]

    def test_returns_none_when_not_found(self, conn):
        assert get_race_session(conn, "nonexistent") is None


# ===========================================================================
# RacePoints
# ===========================================================================

class TestUpsertRacePoints:
    def test_inserts_new_record(self, conn):
        upsert_race_points(conn, "sess1", "t1", 1, 10)
        row = conn.execute(
            "SELECT rank, points FROM race_points WHERE team_id='t1' AND session_id='sess1'"
        ).fetchone()
        assert row["rank"] == 1
        assert row["points"] == 10

    def test_updates_on_conflict(self, conn):
        upsert_race_points(conn, "sess1", "t1", 1, 10)
        upsert_race_points(conn, "sess1", "t1", 2, 7)  # 同一 (team, session)
        row = conn.execute(
            "SELECT rank, points FROM race_points WHERE team_id='t1' AND session_id='sess1'"
        ).fetchone()
        assert row["rank"] == 2
        assert row["points"] == 7


class TestGetStandings:
    def test_aggregates_and_orders_descending(self, conn):
        create_team(conn, "t1", "Alpha", "h")
        create_team(conn, "t2", "Beta", "h")
        upsert_race_points(conn, "sess1", "t1", 1, 10)
        upsert_race_points(conn, "sess1", "t2", 2, 7)
        upsert_race_points(conn, "sess2", "t2", 1, 10)
        rows = get_standings(conn)
        # t2: 17 分，t1: 10 分
        assert rows[0]["team_id"] == "t2"
        assert rows[0]["total_points"] == 17
        assert rows[1]["team_id"] == "t1"
        assert rows[1]["total_points"] == 10

    def test_sums_multiple_sessions(self, conn):
        create_team(conn, "t1", "Team 1", "h")
        for sess_id, pts in [("s1", 10), ("s2", 7), ("s3", 5)]:
            upsert_race_points(conn, sess_id, "t1", 1, pts)
        rows = get_standings(conn)
        assert rows[0]["total_points"] == 22

    def test_empty_when_no_points(self, conn):
        assert get_standings(conn) == []
