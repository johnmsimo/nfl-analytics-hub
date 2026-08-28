"""Read-only P3.5 Quick Props / My Hub production verification."""
from __future__ import annotations

import time
from typing import Any

import decision_delivery as dd
import nfl_data
from routes.props import _build_week_rows


def readiness_snapshot(
    target_season: int = 2026,
    *,
    week: int | None = None,
    season_type: str | None = None,
) -> dict[str, Any]:
    """Verify the live delivery contract without calling sportsbook providers."""
    current = nfl_data.current_week()
    selected_week = int(week if week is not None else current["week"])
    selected_type = str(season_type or current.get("season_type") or "REG")
    started = time.perf_counter()
    rows, errors, game_count = _build_week_rows(
        target_season,
        selected_week,
        selected_type,
        include_odds=False,
    )
    delivery = dd.build_delivery(
        rows,
        limit=8,
        game_errors=errors,
        expected_games=game_count,
    )
    elapsed = time.perf_counter() - started
    integrity = dd.verify_delivery_contract(delivery)
    thresholds = {
        "minimumGames": 1,
        "minimumDecisionRows": 50,
        "minimumLeanOrBetter": 3,
        "minimumDelivered": 3,
        "maximumGameErrors": 0,
        "maximumBuildSeconds": 20.0,
    }
    summary = delivery["summary"]
    gates = {
        **integrity["gates"],
        "ready_state": delivery["state"] == "ready",
        "game_coverage": game_count >= thresholds["minimumGames"],
        "decision_volume": summary["rows"] >= thresholds["minimumDecisionRows"],
        "lean_or_better_pool": summary["leanOrBetter"] >= thresholds["minimumLeanOrBetter"],
        "delivered_pick_pool": summary["delivered"] >= thresholds["minimumDelivered"],
        "game_errors": errors <= thresholds["maximumGameErrors"],
        "bounded_build_time": elapsed <= thresholds["maximumBuildSeconds"],
    }
    return {
        "phase": "P3.5",
        "mode": "read-only",
        "targetSeason": target_season,
        "week": selected_week,
        "seasonType": selected_type,
        "pricing": "disabled",
        "games": game_count,
        "buildSeconds": round(elapsed, 3),
        "delivery": {
            "state": delivery["state"],
            "terminal": delivery["terminal"],
            "message": delivery["message"],
            "summary": summary,
            "modelVersion": delivery["modelVersion"],
        },
        "thresholds": thresholds,
        "gates": gates,
        "ok": all(gates.values()),
    }
