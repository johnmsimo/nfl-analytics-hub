#!/usr/bin/env python3
"""Sanitized, read-only P4.0 game prediction production verification."""
from __future__ import annotations

import json
from collections import Counter
from typing import Any

from database import db
import nfl_data
import p40_game_intelligence as p40


def _probability_integrity(rows: list[dict[str, Any]]) -> bool:
    for row in rows:
        home = row.get("homeWinProbability")
        away = row.get("awayWinProbability")
        selected = row.get("selectedProbability")
        simulation = row.get("simulationHomeWinProbability")
        agreement = row.get("simulationAgreement")
        if not all(isinstance(value, (int, float)) for value in (home, away, selected, simulation, agreement)):
            return False
        if not 0.0 <= float(home) <= 1.0 or not 0.0 <= float(away) <= 1.0:
            return False
        if abs(float(home) + float(away) - 1.0) > 1e-5:
            return False
        if not 0.5 <= float(selected) <= 1.0:
            return False
        if not 0.0 <= float(simulation) <= 1.0 or not 0.0 <= float(agreement) <= 1.0:
            return False
    return True


def _synthetic_contract() -> dict[str, bool]:
    game = {
        "game_id": "synthetic",
        "season": 2026,
        "season_type": "REG",
        "week": 1,
        "date": "2026-09-10T00:00:00Z",
        "home_team": "HOME",
        "away_team": "AWAY",
    }
    home = {
        "rating": 7.0,
        "evidenceQuality": 1.0,
        "evidenceMode": "prior-season-fallback",
        "advanced": {"available": True},
    }
    away = {
        "rating": -5.0,
        "evidenceQuality": 1.0,
        "evidenceMode": "prior-season-fallback",
        "advanced": {"available": True},
    }
    strong = p40.predict_game(game, home, away)
    weak = p40.predict_game(
        game,
        {**home, "evidenceQuality": 0.30},
        {**away, "evidenceQuality": 0.30},
    )
    return {
        "strong_edge_produces_model_pick": strong.get("decisionGrade") in {"Strong Play", "Play", "Lean"},
        "weak_evidence_shrinks_probability": abs(float(weak["homeWinProbability"]) - 0.5)
        < abs(float(strong["homeWinProbability"]) - 0.5),
        "model_pick_never_becomes_actionable": strong.get("actionable") is False
        and weak.get("actionable") is False,
    }


def main() -> int:
    from app import app

    season = nfl_data.default_season()
    target_week = 1
    target_type = "REG"
    with app.app_context():
        report = p40.build_week_report(season, target_week, target_type)
        db.session.rollback()

    decisions = list(report.get("decisions") or [])
    grades = Counter(str(row.get("decisionGrade")) for row in decisions)
    evidence_modes = Counter(
        str(profile.get("evidenceMode"))
        for row in decisions
        for profile in (row.get("evidence") or {}).values()
        if isinstance(profile, dict)
    )
    synthetic = _synthetic_contract()
    gates = {
        "regular_season_week_one_available": report.get("available") is True,
        "complete_week_one_slate": int(report.get("gameCount") or 0) == 16,
        "complete_decision_coverage": int(report.get("decisionCount") or 0) == int(report.get("gameCount") or 0),
        "no_missing_team_evidence": int(report.get("skippedCount") or 0) == 0,
        "probability_integrity": _probability_integrity(decisions),
        "model_only_actionability": int(report.get("actionableCount") or 0) == 0
        and all(row.get("actionable") is False and row.get("priceStatus") == "model-only" for row in decisions),
        "decision_contract_integrity": all(
            row.get("decisionGrade") in {"Strong Play", "Play", "Lean", "Pass"}
            and row.get("selectedSide") in {"home", "away"}
            and row.get("selectedTeam") in {row.get("homeTeam"), row.get("awayTeam")}
            for row in decisions
        ),
        "useful_pick_pool": int(report.get("leanOrBetterCount") or 0) >= 4,
        "evidence_is_explicit": all(
            isinstance(row.get("evidence"), dict)
            and isinstance(row["evidence"].get("home"), dict)
            and isinstance(row["evidence"].get("away"), dict)
            for row in decisions
        ),
        **synthetic,
    }
    blockers = [name for name, passed in gates.items() if not passed]
    selected_probabilities = [float(row["selectedProbability"]) for row in decisions if isinstance(row.get("selectedProbability"), (int, float))]
    payload = {
        "ok": not blockers,
        "phase": "P4.0",
        "mode": "read-only-model-only",
        "blockingFailures": blockers,
        "gates": gates,
        "gameIntelligence": {
            "model": report.get("model"),
            "modelVersion": report.get("modelVersion"),
            "season": report.get("season"),
            "seasonType": report.get("seasonType"),
            "week": report.get("week"),
            "gameCount": report.get("gameCount"),
            "decisionCount": report.get("decisionCount"),
            "leanOrBetterCount": report.get("leanOrBetterCount"),
            "skippedCount": report.get("skippedCount"),
            "actionableCount": report.get("actionableCount"),
            "decisionGrades": dict(sorted(grades.items())),
            "evidenceModes": dict(sorted(evidence_modes.items())),
            "selectedProbabilityRange": {
                "min": round(min(selected_probabilities), 6) if selected_probabilities else None,
                "max": round(max(selected_probabilities), 6) if selected_probabilities else None,
            },
        },
    }
    print(json.dumps(payload, sort_keys=True))
    return 0 if not blockers else 1


if __name__ == "__main__":
    raise SystemExit(main())
