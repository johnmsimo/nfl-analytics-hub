"""NFL Analytics Hub v3.1 explainable game-intelligence primitives.

The module is deterministic and framework-independent. It turns normalized
team, injury, weather, and market inputs into a structured matchup report that
can be consumed by the web UI, scheduled jobs, or API clients.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .models import injury_impact, matchup_intelligence, monte_carlo_game, power_rating


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _number(data: Mapping[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float(data.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def _team_name(data: Mapping[str, Any], fallback: str) -> str:
    value = str(data.get("name") or data.get("team") or fallback).strip()
    return value or fallback


def _weather_adjustment(weather: Mapping[str, Any] | None) -> dict[str, Any]:
    weather = weather or {}
    wind_mph = max(0.0, _number(weather, "wind_mph"))
    precipitation = _clamp(_number(weather, "precipitation_probability"), 0.0, 1.0)
    temperature_f = _number(weather, "temperature_f", 65.0)

    passing_penalty = max(0.0, wind_mph - 12.0) * 0.09 + precipitation * 1.2
    kicking_penalty = max(0.0, wind_mph - 15.0) * 0.06
    cold_penalty = max(0.0, 28.0 - temperature_f) * 0.025
    total_penalty = passing_penalty + kicking_penalty + cold_penalty

    if total_penalty >= 2.5:
        impact = "high"
    elif total_penalty >= 1.0:
        impact = "moderate"
    else:
        impact = "low"

    return {
        "impact": impact,
        "projected_total_adjustment": round(-total_penalty, 2),
        "wind_mph": round(wind_mph, 1),
        "temperature_f": round(temperature_f, 1),
        "precipitation_probability": round(precipitation, 3),
    }


def _coaching_edge(home: Mapping[str, Any], away: Mapping[str, Any]) -> float:
    home_score = (
        _number(home, "fourth_down_aggressiveness") * 0.35
        + _number(home, "timeout_efficiency") * 0.25
        + _number(home, "late_game_efficiency") * 0.40
    )
    away_score = (
        _number(away, "fourth_down_aggressiveness") * 0.35
        + _number(away, "timeout_efficiency") * 0.25
        + _number(away, "late_game_efficiency") * 0.40
    )
    return round(_clamp((home_score - away_score) * 2.5, -3.0, 3.0), 2)


def _rating(team: Mapping[str, Any]) -> dict[str, float]:
    return power_rating(
        offense_epa=_number(team, "offense_epa"),
        defense_epa_allowed=_number(team, "defense_epa_allowed"),
        special_teams_epa=_number(team, "special_teams_epa"),
        schedule_strength=_number(team, "schedule_strength"),
        recent_form=_number(team, "recent_form"),
    )


def _rank_factors(factors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        factors,
        key=lambda item: (-abs(float(item["home_edge"])), str(item["factor"])),
    )
    for index, factor in enumerate(ordered, start=1):
        factor["rank"] = index
    return ordered


def game_intelligence(
    home: Mapping[str, Any],
    away: Mapping[str, Any],
    *,
    home_injuries: Iterable[Mapping[str, Any]] = (),
    away_injuries: Iterable[Mapping[str, Any]] = (),
    weather: Mapping[str, Any] | None = None,
    market: Mapping[str, Any] | None = None,
    simulations: int = 10_000,
    seed: int = 31,
) -> dict[str, Any]:
    """Build a complete, explainable v3.1 matchup intelligence report."""
    home_name = _team_name(home, "Home")
    away_name = _team_name(away, "Away")
    home_rating = _rating(home)
    away_rating = _rating(away)
    base_matchup = matchup_intelligence(home, away)

    home_injury = injury_impact(home_injuries)
    away_injury = injury_impact(away_injuries)
    injury_edge = float(away_injury["rating_penalty"]) - float(home_injury["rating_penalty"])
    coaching_edge = _coaching_edge(home, away)
    rating_edge = float(home_rating["rating"]) - float(away_rating["rating"])
    home_field = _number(home, "home_field_points", 1.5)

    weather_result = _weather_adjustment(weather)
    model_margin = (
        float(base_matchup["estimated_point_edge"]) * 0.45
        + rating_edge * 0.18
        + injury_edge
        + coaching_edge
        + home_field * 0.35
    )

    market = market or {}
    market_spread = _number(market, "home_spread", 0.0)
    market_edge = model_margin + market_spread

    home_mean = _number(home, "projected_points", 23.0) + model_margin / 2.0
    away_mean = _number(away, "projected_points", 21.5) - model_margin / 2.0
    total_adjustment = float(weather_result["projected_total_adjustment"])
    home_mean += total_adjustment / 2.0
    away_mean += total_adjustment / 2.0

    simulation = monte_carlo_game(
        home_mean=max(0.0, home_mean),
        away_mean=max(0.0, away_mean),
        home_sd=max(0.1, _number(home, "scoring_sd", 10.5)),
        away_sd=max(0.1, _number(away, "scoring_sd", 10.5)),
        simulations=simulations,
        seed=seed,
    )

    home_win_probability = float(simulation["home_win_probability"])
    favored_side = "home" if home_win_probability >= 0.5 else "away"
    favored_team = home_name if favored_side == "home" else away_name
    underdog_team = away_name if favored_side == "home" else home_name
    confidence_score = round(
        _clamp(50.0 + abs(home_win_probability - 0.5) * 100.0, 50.0, 99.0),
        1,
    )
    upset_probability = round(min(home_win_probability, 1.0 - home_win_probability), 4)

    factors = _rank_factors(
        [
            {"factor": "power rating", "home_edge": round(rating_edge * 0.18, 2)},
            {
                "factor": "offense vs defense",
                "home_edge": round(float(base_matchup["estimated_point_edge"]) * 0.45, 2),
            },
            {"factor": "injury availability", "home_edge": round(injury_edge, 2)},
            {"factor": "coaching", "home_edge": coaching_edge},
            {"factor": "home field", "home_edge": round(home_field * 0.35, 2)},
            {"factor": "market disagreement", "home_edge": round(market_edge, 2)},
        ]
    )

    strongest = factors[0]
    factor_beneficiary = home_name if float(strongest["home_edge"]) >= 0 else away_name
    risk_factors = [
        factor
        for factor in factors
        if (float(factor["home_edge"]) < 0) == (favored_side == "home")
    ]
    biggest_risk = (
        risk_factors[0]
        if risk_factors
        else {"factor": "simulation volatility", "home_edge": 0.0}
    )

    if abs(model_margin) < 2.5:
        script = "one-score game with late-possession leverage"
    elif model_margin > 0:
        script = f"{home_name} plays from ahead and forces {away_name} into higher-volume passing"
    else:
        script = f"{away_name} controls the game script and pressures {home_name} to chase points"

    summary = (
        f"The model favors {favored_team} over {underdog_team} with {confidence_score:.1f}% confidence. "
        f"The strongest projected advantage is {strongest['factor']} for {factor_beneficiary}."
    )

    return {
        "version": "3.1",
        "home_team": home_name,
        "away_team": away_name,
        "favored_team": favored_team,
        "favored_side": favored_side,
        "confidence_score": confidence_score,
        "home_win_probability": round(home_win_probability, 4),
        "away_win_probability": round(1.0 - home_win_probability, 4),
        "upset_probability": upset_probability,
        "model_home_margin": round(model_margin, 2),
        "market_home_spread": round(market_spread, 2),
        "market_edge": round(market_edge, 2),
        "projected_score": {"home": round(home_mean, 1), "away": round(away_mean, 1)},
        "summary": summary,
        "game_script": script,
        "biggest_risk": biggest_risk,
        "key_factors": factors,
        "ratings": {"home": home_rating, "away": away_rating},
        "injuries": {"home": home_injury, "away": away_injury},
        "weather": weather_result,
        "simulation": simulation,
    }
