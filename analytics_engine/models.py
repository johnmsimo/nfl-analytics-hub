"""Explainable NFL analytics models with deterministic defaults.

These functions are intentionally framework-independent so they can be tested,
benchmarked, and reused by Flask routes, scheduled jobs, and notebooks.
"""
from __future__ import annotations

import math
import random
from statistics import fmean, pstdev
from typing import Iterable, Mapping


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _logistic(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-_clamp(value, -30.0, 30.0)))


def live_win_probability(
    score_diff: float,
    seconds_remaining: int,
    possession: int = 0,
    pregame_home_edge: float = 0.0,
) -> dict[str, float]:
    """Estimate home-team win probability from an explainable baseline."""
    seconds = int(_clamp(seconds_remaining, 0, 3600))
    time_fraction = seconds / 3600.0
    score_weight = 0.17 + 0.72 * (1.0 - time_fraction) ** 1.45
    clock_pressure = 1.0 + (1.0 - time_fraction) * 0.35
    logit = (
        score_diff * score_weight * clock_pressure
        + _clamp(possession, -1, 1) * (0.22 + 0.18 * time_fraction)
        + pregame_home_edge * 0.55
    )
    probability = _clamp(_logistic(logit), 0.002, 0.998)
    return {
        "home_win_probability": round(probability, 4),
        "away_win_probability": round(1.0 - probability, 4),
        "score_weight": round(score_weight, 4),
        "seconds_remaining": seconds,
    }


def epa_summary(plays: Iterable[Mapping[str, object]]) -> dict[str, float | int]:
    values = [float(play.get("epa") or 0.0) for play in plays]
    if not values:
        return {"plays": 0, "total_epa": 0.0, "epa_per_play": 0.0, "success_rate": 0.0}
    successes = sum(value > 0.0 for value in values)
    return {
        "plays": len(values),
        "total_epa": round(sum(values), 3),
        "epa_per_play": round(fmean(values), 3),
        "success_rate": round(successes / len(values), 4),
    }


def drive_success_summary(drives: Iterable[Mapping[str, object]]) -> dict[str, float | int]:
    rows = list(drives)
    if not rows:
        return {"drives": 0, "scoring_rate": 0.0, "touchdown_rate": 0.0, "points_per_drive": 0.0}
    points = [float(row.get("points") or 0.0) for row in rows]
    touchdowns = sum(bool(row.get("touchdown")) or value >= 6 for row, value in zip(rows, points))
    scoring = sum(value > 0 for value in points)
    return {
        "drives": len(rows),
        "scoring_rate": round(scoring / len(rows), 4),
        "touchdown_rate": round(touchdowns / len(rows), 4),
        "points_per_drive": round(fmean(points), 3),
    }


def monte_carlo_game(
    home_mean: float,
    away_mean: float,
    home_sd: float = 10.5,
    away_sd: float = 10.5,
    simulations: int = 10_000,
    seed: int = 13,
) -> dict[str, float | int]:
    simulations = int(_clamp(simulations, 100, 100_000))
    rng = random.Random(seed)
    home_wins = ties = 0
    margins: list[float] = []
    totals: list[float] = []
    for _ in range(simulations):
        home = max(0.0, rng.gauss(home_mean, max(home_sd, 0.1)))
        away = max(0.0, rng.gauss(away_mean, max(away_sd, 0.1)))
        margin = home - away
        margins.append(margin)
        totals.append(home + away)
        if margin > 0:
            home_wins += 1
        elif margin == 0:
            ties += 1
    return {
        "simulations": simulations,
        "home_win_probability": round((home_wins + ties * 0.5) / simulations, 4),
        "projected_margin": round(fmean(margins), 2),
        "projected_total": round(fmean(totals), 2),
        "margin_volatility": round(pstdev(margins), 2),
        "seed": seed,
    }


def power_rating(
    offense_epa: float,
    defense_epa_allowed: float,
    special_teams_epa: float = 0.0,
    schedule_strength: float = 0.0,
    recent_form: float = 0.0,
) -> dict[str, float]:
    components = {
        "offense": offense_epa * 45.0,
        "defense": -defense_epa_allowed * 45.0,
        "special_teams": special_teams_epa * 20.0,
        "schedule": schedule_strength * 6.0,
        "recent_form": recent_form * 8.0,
    }
    raw = sum(components.values())
    return {
        "rating": round(50.0 + raw, 2),
        **{key: round(value, 2) for key, value in components.items()},
    }


_POSITION_WEIGHTS = {
    "QB": 1.0,
    "LT": 0.7,
    "EDGE": 0.65,
    "CB": 0.55,
    "WR": 0.48,
    "TE": 0.36,
    "RB": 0.30,
}
_STATUS_WEIGHTS = {
    "out": 1.0,
    "doubtful": 0.8,
    "questionable": 0.45,
    "limited": 0.2,
    "active": 0.0,
}


def injury_impact(injuries: Iterable[Mapping[str, object]]) -> dict[str, object]:
    details = []
    total = 0.0
    for item in injuries:
        position = str(item.get("position") or "").upper()
        status = str(item.get("status") or "active").lower()
        role = _clamp(float(item.get("role_share") or 1.0), 0.0, 1.0)
        impact = _POSITION_WEIGHTS.get(position, 0.25) * _STATUS_WEIGHTS.get(status, 0.35) * role
        total += impact
        details.append(
            {
                "player": item.get("player"),
                "position": position,
                "status": status,
                "impact": round(impact, 3),
            }
        )
    return {
        "rating_penalty": round(total * 8.0, 2),
        "severity": round(_clamp(total, 0.0, 1.0), 3),
        "players": details,
    }


def player_similarity(
    target: Mapping[str, float],
    candidates: Mapping[str, Mapping[str, float]],
    limit: int = 5,
) -> list[dict[str, object]]:
    features = sorted(target)
    if not features:
        return []
    distances = []
    for player, vector in candidates.items():
        squared = sum((float(target[key]) - float(vector.get(key, 0.0))) ** 2 for key in features)
        distance = math.sqrt(squared / len(features))
        score = 1.0 / (1.0 + distance)
        distances.append(
            {
                "player": player,
                "similarity": round(score, 4),
                "distance": round(distance, 4),
            }
        )
    return sorted(
        distances,
        key=lambda row: (-float(row["similarity"]), str(row["player"])),
    )[: max(1, limit)]


def matchup_intelligence(
    home: Mapping[str, float],
    away: Mapping[str, float],
) -> dict[str, object]:
    home_offense = float(home.get("offense_epa") or 0.0)
    away_offense = float(away.get("offense_epa") or 0.0)
    home_defense = float(home.get("defense_epa_allowed") or 0.0)
    away_defense = float(away.get("defense_epa_allowed") or 0.0)
    edge = (home_offense - away_defense) - (away_offense - home_defense)
    confidence = _clamp(abs(edge) * 1.8, 0.05, 0.95)
    reasons = [
        {"factor": "offense-vs-defense EPA", "home_edge": round(edge, 3)},
        {"factor": "home field", "home_edge": 1.5},
    ]
    return {
        "favored_team": "home" if edge + 0.03 >= 0 else "away",
        "estimated_point_edge": round(edge * 21.0 + 1.5, 2),
        "confidence": round(confidence, 3),
        "reasons": reasons,
    }
