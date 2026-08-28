"""P4.0 transparent game prediction intelligence.

P3.x established the player-prop decision, pricing, ledger, learning, and
calibration-governance loop. P4.0 starts the game-level intelligence track with
a deterministic, warehouse-backed moneyline model.

This phase is model-only. It does not call an odds provider and it never marks a
selection actionable. Sportsbook price/EV actionability remains a separate
market layer.
"""
from __future__ import annotations

import math
import os
from typing import Any

from database import db
from db_models import Team, TeamAdvancedSeasonStat, TeamSeasonStat
import nfl_data
from team_identity import normalize_team

MODEL_NAME = "p4.0-game-intelligence"
MODEL_VERSION = "p40-transparent-v1"


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _env_float(name: str, default: float, low: float, high: float) -> float:
    value = float(os.environ.get(name, default))
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return value


def policy() -> dict[str, Any]:
    return {
        "currentSeasonGameFloor": int(os.environ.get("P40_CURRENT_SEASON_GAME_FLOOR", "4")),
        "homeFieldPoints": _env_float("P40_HOME_FIELD_POINTS", 1.5, 0.0, 4.0),
        "logisticScale": _env_float("P40_LOGISTIC_SCALE", 6.5, 3.0, 15.0),
        "simulationMarginSd": _env_float("P40_MARGIN_SD", 13.5, 7.0, 24.0),
    }


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _basic_rating(row: TeamSeasonStat) -> tuple[float, dict[str, float]]:
    games = max(0, int(row.games or 0))
    if games:
        point_diff_pg = _number(row.point_differential) / games
    else:
        point_diff_pg = _number(row.ppg) - _number(row.papg)
    win_pct = _number(row.win_pct, 0.5)
    score = point_diff_pg * 0.75 + (win_pct - 0.5) * 6.0
    return score, {
        "games": float(games),
        "pointDifferentialPerGame": round(point_diff_pg, 3),
        "winPct": round(win_pct, 4),
        "ppg": round(_number(row.ppg), 3),
        "papg": round(_number(row.papg), 3),
    }


def _advanced_adjustment(row: TeamAdvancedSeasonStat | None) -> tuple[float, dict[str, Any]]:
    if row is None:
        return 0.0, {"available": False}
    off_epa = _number(row.offensive_epa_per_play)
    def_epa = _number(row.defensive_epa_per_play)
    off_success = _number(row.offensive_success_rate)
    def_success = _number(row.defensive_success_rate)
    # Positive offensive EPA helps; lower defensive EPA allowed helps.
    epa_edge = off_epa - def_epa
    success_edge = off_success - def_success
    adjustment = _clamp(epa_edge * 12.0 + success_edge * 5.0, -4.0, 4.0)
    return adjustment, {
        "available": True,
        "offensiveEpaPerPlay": round(off_epa, 4),
        "defensiveEpaPerPlay": round(def_epa, 4),
        "offensiveSuccessRate": round(off_success, 4),
        "defensiveSuccessRate": round(def_success, 4),
        "adjustment": round(adjustment, 3),
    }


def build_team_profile(team_abbreviation: str, target_season: int) -> dict[str, Any] | None:
    """Build one transparent team-strength profile from warehouse facts.

    Early in a season, prior-season REG evidence is preferred until the current
    season reaches the configured game floor. This prevents preseason/Week 1
    records from being treated as mature evidence.
    """
    canonical = normalize_team(team_abbreviation)
    if canonical is None:
        return None
    team = db.session.scalar(db.select(Team).where(Team.abbreviation == canonical))
    if team is None:
        return None

    floor = max(1, min(12, int(policy()["currentSeasonGameFloor"])))
    current = db.session.scalar(
        db.select(TeamSeasonStat).where(
            TeamSeasonStat.team_id == team.id,
            TeamSeasonStat.season == target_season,
            TeamSeasonStat.season_type == "REG",
        )
    )
    previous = db.session.scalar(
        db.select(TeamSeasonStat).where(
            TeamSeasonStat.team_id == team.id,
            TeamSeasonStat.season == target_season - 1,
            TeamSeasonStat.season_type == "REG",
        )
    )
    if current is not None and int(current.games or 0) >= floor:
        basic = current
        evidence_season = target_season
        evidence_mode = "current-season"
    elif previous is not None:
        basic = previous
        evidence_season = target_season - 1
        evidence_mode = "prior-season-fallback"
    elif current is not None and int(current.games or 0) > 0:
        basic = current
        evidence_season = target_season
        evidence_mode = "thin-current-season"
    else:
        return None

    advanced = db.session.scalar(
        db.select(TeamAdvancedSeasonStat).where(
            TeamAdvancedSeasonStat.team_id == team.id,
            TeamAdvancedSeasonStat.season == evidence_season,
            TeamAdvancedSeasonStat.season_type == "REG",
        )
    )
    basic_score, basic_components = _basic_rating(basic)
    advanced_score, advanced_components = _advanced_adjustment(advanced)
    games = int(basic.games or 0)
    game_support = _clamp(games / 12.0, 0.0, 1.0)
    advanced_support = 1.0 if advanced is not None else 0.0
    evidence_quality = _clamp(0.30 + game_support * 0.50 + advanced_support * 0.20, 0.30, 1.0)
    rating = _clamp(basic_score + advanced_score, -12.0, 12.0)

    return {
        "team": team.abbreviation,
        "teamName": team.name,
        "targetSeason": target_season,
        "evidenceSeason": evidence_season,
        "evidenceMode": evidence_mode,
        "evidenceQuality": round(evidence_quality, 4),
        "rating": round(rating, 4),
        "basic": basic_components,
        "advanced": advanced_components,
    }


def _logistic_probability(margin: float, scale: float) -> float:
    return 1.0 / (1.0 + math.exp(-margin / scale))


def _normal_margin_probability(margin: float, sd: float) -> float:
    return 0.5 * (1.0 + math.erf(margin / (sd * math.sqrt(2.0))))


def _confidence_grade(score: float) -> str:
    if score >= 80:
        return "A"
    if score >= 70:
        return "B"
    if score >= 60:
        return "C"
    return "D"


def _decision_grade(probability: float, confidence: float) -> str:
    selected = max(probability, 1.0 - probability)
    if selected >= 0.68 and confidence >= 75:
        return "Strong Play"
    if selected >= 0.62 and confidence >= 65:
        return "Play"
    if selected >= 0.56 and confidence >= 55:
        return "Lean"
    return "Pass"


def predict_game(
    game: dict[str, Any],
    home_profile: dict[str, Any],
    away_profile: dict[str, Any],
) -> dict[str, Any]:
    """Return a deterministic model-only moneyline decision contract."""
    active = policy()
    home_field = float(active["homeFieldPoints"])
    rating_edge = _number(home_profile.get("rating")) - _number(away_profile.get("rating"))
    model_margin = rating_edge + home_field
    raw_home = _clamp(
        _logistic_probability(model_margin, float(active["logisticScale"])),
        0.08,
        0.92,
    )
    evidence_quality = (
        _number(home_profile.get("evidenceQuality"), 0.3)
        + _number(away_profile.get("evidenceQuality"), 0.3)
    ) / 2.0
    # Weak evidence is deliberately pulled toward 50%, while preserving the
    # raw probability for auditability.
    shrink = 0.50 + 0.50 * _clamp(evidence_quality, 0.0, 1.0)
    calibrated_home = 0.5 + (raw_home - 0.5) * shrink
    simulation_home = _normal_margin_probability(model_margin, float(active["simulationMarginSd"]))
    consensus_home = _clamp(calibrated_home * 0.75 + simulation_home * 0.25, 0.08, 0.92)
    agreement = 1.0 - abs(calibrated_home - simulation_home)
    confidence = _clamp(
        evidence_quality * 72.0 + abs(consensus_home - 0.5) * 55.0 + agreement * 8.0,
        35.0,
        99.0,
    )

    selected_side = "home" if consensus_home >= 0.5 else "away"
    selected_team = game.get("home_team") if selected_side == "home" else game.get("away_team")
    selected_probability = consensus_home if selected_side == "home" else 1.0 - consensus_home
    grade = _decision_grade(consensus_home, confidence)
    reasons = [
        {
            "factor": "team-strength edge",
            "homeEdgePoints": round(rating_edge, 3),
        },
        {
            "factor": "home field",
            "homeEdgePoints": round(home_field, 3),
        },
        {
            "factor": "evidence support",
            "quality": round(evidence_quality, 4),
        },
    ]
    risks: list[str] = []
    if home_profile.get("evidenceMode") != "current-season" or away_profile.get("evidenceMode") != "current-season":
        risks.append("Early-season estimate uses prior-season or thin current-season evidence.")
    if not home_profile.get("advanced", {}).get("available") or not away_profile.get("advanced", {}).get("available"):
        risks.append("Advanced EPA/success-rate evidence is incomplete for at least one team.")
    if abs(calibrated_home - simulation_home) >= 0.05:
        risks.append("Distribution confirmation disagrees materially with the logistic estimate.")
    if not risks:
        risks.append("Normal game-to-game variance can overwhelm a modest model edge.")

    return {
        "gameId": str(game.get("game_id") or ""),
        "season": game.get("season"),
        "seasonType": game.get("season_type"),
        "week": game.get("week"),
        "kickoffAt": game.get("date"),
        "homeTeam": game.get("home_team"),
        "awayTeam": game.get("away_team"),
        "model": MODEL_NAME,
        "modelVersion": MODEL_VERSION,
        "market": "moneyline",
        "modelHomeMargin": round(model_margin, 3),
        "rawHomeWinProbability": round(raw_home, 6),
        "homeWinProbability": round(consensus_home, 6),
        "awayWinProbability": round(1.0 - consensus_home, 6),
        "simulationHomeWinProbability": round(simulation_home, 6),
        "simulationAgreement": round(agreement, 6),
        "evidenceQuality": round(evidence_quality, 4),
        "confidenceScore": round(confidence, 2),
        "confidenceGrade": _confidence_grade(confidence),
        "decisionGrade": grade,
        "selectedSide": selected_side,
        "selectedTeam": selected_team,
        "selectedProbability": round(selected_probability, 6),
        "recommendedAction": "MODEL PICK" if grade in {"Strong Play", "Play", "Lean"} else "PASS",
        "actionable": False,
        "priceStatus": "model-only",
        "reasons": reasons,
        "risks": risks,
        "evidence": {"home": home_profile, "away": away_profile},
    }


def build_week_report(season: int, week: int, season_type: str = "REG") -> dict[str, Any]:
    games = nfl_data.get_week_games(season, week, season_type, live=False)
    profile_cache: dict[str, dict[str, Any] | None] = {}
    decisions: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for game in games:
        home = str(game.get("home_team") or "")
        away = str(game.get("away_team") or "")
        if home not in profile_cache:
            profile_cache[home] = build_team_profile(home, season)
        if away not in profile_cache:
            profile_cache[away] = build_team_profile(away, season)
        home_profile, away_profile = profile_cache[home], profile_cache[away]
        if home_profile is None or away_profile is None:
            skipped.append(
                {
                    "gameId": game.get("game_id"),
                    "homeTeam": home,
                    "awayTeam": away,
                    "reason": "missing_team_evidence",
                }
            )
            continue
        decisions.append(predict_game(game, home_profile, away_profile))

    grade_rank = {"Strong Play": 0, "Play": 1, "Lean": 2, "Pass": 3}
    decisions.sort(
        key=lambda row: (
            grade_rank.get(str(row.get("decisionGrade")), 9),
            -float(row.get("confidenceScore") or 0.0),
            -float(row.get("selectedProbability") or 0.0),
            str(row.get("gameId") or ""),
        )
    )
    lean_or_better = sum(row["decisionGrade"] != "Pass" for row in decisions)
    return {
        "available": bool(games),
        "model": MODEL_NAME,
        "modelVersion": MODEL_VERSION,
        "season": season,
        "seasonType": season_type,
        "week": week,
        "gameCount": len(games),
        "decisionCount": len(decisions),
        "leanOrBetterCount": lean_or_better,
        "skippedCount": len(skipped),
        "actionableCount": 0,
        "decisions": decisions,
        "skipped": skipped,
        "marketActionability": "disabled-in-p4.0",
    }
