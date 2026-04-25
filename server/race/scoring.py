"""
Result extraction helpers.

These functions read files written by Webots after a session ends:
  recordings/{session_id}/metadata.json
  recordings/{session_id}/telemetry.jsonl

They are called synchronously from async route handlers via asyncio.to_thread.
"""

import json
import pathlib


# ---------------------------------------------------------------------------
# Race session results (multi-car)
# ---------------------------------------------------------------------------

def extract_session_results(session_id: str, recordings_dir: str) -> dict:
    """
    Read metadata.json for a completed race session.

    Returns:
        {
            "final_rankings": [...],
            "duration_sim":   <float>,
            "finish_reason":  <str>,
            "teams":          [...],
        }
    """
    path = pathlib.Path(recordings_dir) / session_id / "metadata.json"
    with open(path, encoding="utf-8") as f:
        meta = json.load(f)

    return {
        "final_rankings": meta.get("final_rankings", []),
        "duration_sim":   meta.get("duration_sim", 0),
        "finish_reason":  meta.get("finish_reason", "unknown"),
        "teams":          meta.get("teams", []),
    }


# ---------------------------------------------------------------------------
# Single-car test run results
# ---------------------------------------------------------------------------

def extract_test_results(session_id: str, recordings_dir: str) -> dict:
    """
    For a single-car test run, parse metadata.json + telemetry.jsonl and
    return a flat report dict suitable for storing in test_runs.

    Returns:
        {
            "laps_completed":    <int>,
            "best_lap_time":     <float | None>,
            "collisions_minor":  <int>,
            "collisions_major":  <int>,
            "timeout_warnings":  <int>,
            "finish_reason":     <str>,
        }

    telemetry.jsonl event shapes relevant here:
        {"type": "collision", "severity": "minor"|"major", ...}
        {"type": "timeout_warn", ...}
    """
    session_dir = pathlib.Path(recordings_dir) / session_id

    # --- metadata ---
    meta_path = session_dir / "metadata.json"
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)

    finish_reason = meta.get("finish_reason", "unknown")

    # Pull lap info from the first (and only) entry in final_rankings
    rankings = meta.get("final_rankings", [])
    laps_completed = 0
    best_lap_time:  float | None = None

    if rankings:
        first = rankings[0]
        laps_completed = first.get("laps_completed", 0)
        # best_lap_time may be stored directly or derived from lap_times list
        if "best_lap_time" in first:
            best_lap_time = first["best_lap_time"]
        elif "lap_times" in first:
            lap_times = [t for t in first["lap_times"] if t is not None]
            best_lap_time = min(lap_times) if lap_times else None

    # --- telemetry event counts ---
    collisions_minor  = 0
    collisions_major  = 0
    timeout_warnings  = 0

    telemetry_path = session_dir / "telemetry.jsonl"
    if telemetry_path.exists():
        with open(telemetry_path, encoding="utf-8") as f:
            for raw_line in f:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    frame = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue

                for event in frame.get("events", []):
                    etype = event.get("type")
                    if etype == "collision":
                        severity = event.get("severity", "minor")
                        if severity == "major":
                            collisions_major += 1
                        else:
                            collisions_minor += 1
                    elif etype == "timeout_warn":
                        timeout_warnings += 1

    return {
        "laps_completed":   laps_completed,
        "best_lap_time":    best_lap_time,
        "collisions_minor": collisions_minor,
        "collisions_major": collisions_major,
        "timeout_warnings": timeout_warnings,
        "finish_reason":    finish_reason,
    }
