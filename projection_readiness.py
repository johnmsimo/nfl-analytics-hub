"""Projection-readiness metrics for the current roster.

The full preseason roster contains rookies and other cold-start players who
cannot possibly have three prior-season NFL games. P3.2 therefore reports two
coverage measures:

* overall coverage across every current roster-verified skill player; and
* returning-player coverage across current skill players with at least one
  game of historical evidence.

The production gate uses the returning-player measure while retaining an
absolute minimum projection-ready player count. Cold-start players remain
visible in diagnostics and are handled by later role/rookie modeling rather
than being treated as missing historical data.
"""
from __future__ import annotations

from typing import Any

import projection_data as pd


def projection_pool_snapshot(target_season: int) -> dict[str, Any]:
    """Return aggregate-only projection coverage metrics safe for CI logs."""
    evidence = pd.stats_season(target_season)
    logs = pd.player_game_logs(evidence)
    index = pd.player_index(target_season, evidence)

    roster_verified = {
        key: meta for key, meta in index.items() if bool(meta.get("rosterVerified"))
    }
    skill = {
        key: meta
        for key, meta in roster_verified.items()
        if str(meta.get("position") or "").upper() in pd.SKILL_POSITIONS
    }
    returning = {
        key: meta for key, meta in skill.items() if len(logs.get(key, [])) >= 1
    }
    ready = {
        key: meta for key, meta in returning.items() if len(logs.get(key, [])) >= 3
    }

    overall_coverage = round(len(ready) / len(skill), 4) if skill else 0.0
    returning_coverage = (
        round(len(ready) / len(returning), 4) if returning else 0.0
    )
    cold_start = len(skill) - len(returning)

    return {
        "target_season": target_season,
        "evidence_season": evidence,
        "evidence_rows": sum(len(rows) for rows in logs.values()),
        "evidence_players": len(logs),
        "current_roster_players": len(index),
        "roster_verified_players": len(roster_verified),
        "current_skill_players": len(skill),
        "returning_skill_players": len(returning),
        "cold_start_skill_players": cold_start,
        "projection_ready_skill_players": len(ready),
        "projection_ready_skill_coverage": overall_coverage,
        "projection_ready_returning_skill_coverage": returning_coverage,
        "current_regular_weeks": pd.regular_weeks_with_stats(target_season),
    }
