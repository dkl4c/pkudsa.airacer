"""
Race bracket (tournament format) auto-computation based on team count.

Given N teams in a zone, computes:
- Which stages are needed (qualifying / group_race / semi / final)
- How many teams advance from each stage
- Sessions (matches) per stage
"""

import math


def _compute_advancement(team_count: int, stages: list[str]) -> dict[str, int]:
    """Return {stage: teams_that_advance_to_next_stage}."""
    adv: dict[str, int] = {}

    if stages == ["qualifying", "final"]:
        adv["qualifying"] = min(team_count, 4)

    elif stages == ["qualifying", "semi", "final"]:
        adv["qualifying"] = min(team_count, 4)
        adv["semi"] = 2

    else:  # qualifying + group_race + semi + final
        # Top 75% of qualifying advance to group_race (at least 4)
        group_count = max(4, math.ceil(team_count * 0.75))
        group_count = min(group_count, team_count)
        adv["qualifying"]  = group_count
        adv["group_race"]  = min(4, group_count)
        adv["semi"]        = 2

    return adv


def compute_bracket(team_count: int) -> dict:
    """
    Compute tournament bracket for a zone with team_count teams.

    Returns a dict with:
      stages:          list of stage names in order
      team_count:      input value
      cars_per_session: always 4 (Webots world limit)
      advancement:     {stage → how many teams advance to next stage}
      sessions_per_stage: {stage → number of race sessions needed}
    """
    if team_count <= 0:
        return {
            "stages": [],
            "team_count": team_count,
            "cars_per_session": 4,
            "advancement": {},
            "sessions_per_stage": {},
        }

    if team_count <= 4:
        stages = ["qualifying", "final"]
    elif team_count <= 8:
        stages = ["qualifying", "semi", "final"]
    else:
        stages = ["qualifying", "group_race", "semi", "final"]

    advancement = _compute_advancement(team_count, stages)

    sessions: dict[str, int] = {}
    current = team_count
    for stage in stages:
        # Qualifying: all cars can race simultaneously if ≤ 4; otherwise batches
        sessions[stage] = max(1, math.ceil(current / 4))
        current = advancement.get(stage, 2)

    return {
        "stages":             stages,
        "team_count":         team_count,
        "cars_per_session":   4,
        "advancement":        advancement,
        "sessions_per_stage": sessions,
    }
