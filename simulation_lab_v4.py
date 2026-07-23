"""Distribution-based simulation laboratory for NFL Analytics Hub v4.0."""
from __future__ import annotations

import math
import random
from collections.abc import Iterable, Mapping
from statistics import mean, pstdev
from typing import Any

_ALLOWED_FACTORS = {
    "home_offense",
    "away_offense",
    "home_defense",
    "away_defense",
    "pace",
    "turnovers",
    "weather",
    "home_injuries",
    "away_injuries",
    "home_lineup",
    "away_lineup",
    "market",
}


def _number(data: Mapping[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float(data.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def normalize_game_profile(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize the baseline inputs used by the simulator."""
    simulations = int(_clamp(_number(payload, "simulations", 5000), 100, 50_000))
    return {
        "home_team": str(payload.get("home_team") or "Home")[:80],
        "away_team": str(payload.get("away_team") or "Away")[:80],
        "home_points": _clamp(_number(payload, "home_points", 23.0), 0.0, 60.0),
        "away_points": _clamp(_number(payload, "away_points", 21.0), 0.0, 60.0),
        "home_sd": _clamp(_number(payload, "home_sd", 10.5), 1.0, 25.0),
        "away_sd": _clamp(_number(payload, "away_sd", 10.5), 1.0, 25.0),
        "correlation": _clamp(_number(payload, "correlation", 0.12), -0.8, 0.8),
        "simulations": simulations,
        "seed": int(_number(payload, "seed", 40)),
        "overtime_tie_break": _clamp(_number(payload, "overtime_tie_break", 0.5), 0.0, 1.0),
    }


def normalize_adjustments(adjustments: Iterable[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    """Return bounded, recognized scenario adjustments."""
    normalized = []
    for raw in adjustments or ():
        factor = str(raw.get("factor") or "").strip().lower()
        if factor not in _ALLOWED_FACTORS:
            continue
        impact = _clamp(_number(raw, "impact"), -12.0, 12.0)
        side = str(raw.get("side") or "both").strip().lower()
        if side not in {"home", "away", "both", "total"}:
            side = "both"
        normalized.append(
            {
                "factor": factor,
                "side": side,
                "impact": round(impact, 3),
                "label": str(raw.get("label") or factor.replace("_", " "))[:120],
                "active": bool(raw.get("active", True)),
            }
        )
    return normalized[:30]


def _adjusted_means(profile: Mapping[str, Any], adjustments: list[dict[str, Any]]) -> tuple[float, float]:
    home = float(profile["home_points"])
    away = float(profile["away_points"])
    for item in adjustments:
        if not item["active"]:
            continue
        impact = float(item["impact"])
        side = item["side"]
        if side == "home":
            home += impact
        elif side == "away":
            away += impact
        elif side == "total":
            home += impact / 2
            away += impact / 2
        else:
            home += impact / 2
            away += impact / 2
    return max(0.0, home), max(0.0, away)


def simulate_game(
    profile_payload: Mapping[str, Any],
    adjustments: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run seeded correlated score simulations and summarize the distribution."""
    profile = normalize_game_profile(profile_payload)
    normalized_adjustments = normalize_adjustments(adjustments)
    home_mean, away_mean = _adjusted_means(profile, normalized_adjustments)
    rng = random.Random(profile["seed"])
    correlation = float(profile["correlation"])
    residual_scale = math.sqrt(max(0.0, 1.0 - correlation**2))
    home_scores: list[float] = []
    away_scores: list[float] = []
    margins: list[float] = []
    totals: list[float] = []
    home_wins = away_wins = ties = 0

    for _ in range(int(profile["simulations"])):
        shared = rng.gauss(0.0, 1.0)
        home_noise = shared * correlation + rng.gauss(0.0, 1.0) * residual_scale
        away_noise = shared * correlation + rng.gauss(0.0, 1.0) * residual_scale
        home_score = max(0.0, home_mean + home_noise * float(profile["home_sd"]))
        away_score = max(0.0, away_mean + away_noise * float(profile["away_sd"]))
        margin = home_score - away_score
        if margin > 0:
            home_wins += 1
        elif margin < 0:
            away_wins += 1
        else:
            ties += 1
        home_scores.append(home_score)
        away_scores.append(away_score)
        margins.append(margin)
        totals.append(home_score + away_score)

    count = int(profile["simulations"])
    tie_share = ties / count
    home_probability = (home_wins + ties * float(profile["overtime_tie_break"])) / count
    away_probability = 1.0 - home_probability
    return {
        "version": "4.0",
        "teams": {"home": profile["home_team"], "away": profile["away_team"]},
        "simulations": count,
        "seed": profile["seed"],
        "adjustments": normalized_adjustments,
        "means": {"home": round(home_mean, 3), "away": round(away_mean, 3)},
        "win_probability": {"home": round(home_probability, 4), "away": round(away_probability, 4)},
        "tie_rate": round(tie_share, 4),
        "projected_score": {"home": round(mean(home_scores), 2), "away": round(mean(away_scores), 2)},
        "margin": {
            "mean": round(mean(margins), 2),
            "sd": round(pstdev(margins), 2),
            "p10": round(_percentile(margins, 0.10), 2),
            "p50": round(_percentile(margins, 0.50), 2),
            "p90": round(_percentile(margins, 0.90), 2),
        },
        "total": {
            "mean": round(mean(totals), 2),
            "sd": round(pstdev(totals), 2),
            "p10": round(_percentile(totals, 0.10), 2),
            "p50": round(_percentile(totals, 0.50), 2),
            "p90": round(_percentile(totals, 0.90), 2),
        },
        "decision": "home" if home_probability >= 0.5 else "away",
        "confidence": round(abs(home_probability - 0.5) * 2, 4),
    }


def compare_scenarios(
    profile: Mapping[str, Any],
    scenarios: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Compare named scenario runs against a common deterministic baseline."""
    baseline = simulate_game(profile)
    rows = []
    for index, raw in enumerate(scenarios):
        name = str(raw.get("name") or f"Scenario {index + 1}")[:100]
        adjustments = raw.get("adjustments", [])
        if not isinstance(adjustments, list):
            adjustments = []
        result = simulate_game(profile, adjustments)
        delta = result["win_probability"]["home"] - baseline["win_probability"]["home"]
        rows.append(
            {
                "name": name,
                "home_win_probability": result["win_probability"]["home"],
                "home_probability_delta": round(delta, 4),
                "projected_score": result["projected_score"],
                "mean_margin": result["margin"]["mean"],
                "mean_total": result["total"]["mean"],
                "decision": result["decision"],
                "adjustments": result["adjustments"],
            }
        )
    rows.sort(key=lambda item: abs(item["home_probability_delta"]), reverse=True)
    return {"version": "4.0", "baseline": baseline, "scenarios": rows[:25]}


def sensitivity_analysis(
    profile: Mapping[str, Any],
    factors: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Measure one-factor-at-a-time probability sensitivity around the baseline."""
    baseline = simulate_game(profile)
    baseline_probability = baseline["win_probability"]["home"]
    rows = []
    for raw in factors:
        factor = str(raw.get("factor") or "").strip().lower()
        if factor not in _ALLOWED_FACTORS:
            continue
        side = str(raw.get("side") or "home").strip().lower()
        step = abs(_clamp(_number(raw, "step", 1.0), 0.1, 12.0))
        negative = simulate_game(profile, [{"factor": factor, "side": side, "impact": -step}])
        positive = simulate_game(profile, [{"factor": factor, "side": side, "impact": step}])
        negative_probability = negative["win_probability"]["home"]
        positive_probability = positive["win_probability"]["home"]
        rows.append(
            {
                "factor": factor,
                "side": side,
                "step": round(step, 3),
                "negative_probability": negative_probability,
                "baseline_probability": baseline_probability,
                "positive_probability": positive_probability,
                "swing": round(positive_probability - negative_probability, 4),
                "local_slope": round((positive_probability - negative_probability) / (2 * step), 6),
            }
        )
    rows.sort(key=lambda item: abs(item["swing"]), reverse=True)
    return {"version": "4.0", "baseline": baseline, "sensitivity": rows[:25]}
