"""Framework adapter for the dependency-light analytics engine."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from analytics_engine import (
    drive_success_summary,
    epa_summary,
    game_intelligence,
    injury_impact,
    live_win_probability,
    matchup_intelligence,
    monte_carlo_game,
    player_similarity,
    power_rating,
)

analytics_api_bp = Blueprint("analytics_api", __name__, url_prefix="/api/v3/analytics")


def _payload() -> dict:
    return request.get_json(silent=True) or {}


@analytics_api_bp.post("/win-probability")
def win_probability():
    data = _payload()
    return jsonify(
        live_win_probability(
            float(data.get("score_diff", 0)),
            int(data.get("seconds_remaining", 3600)),
            int(data.get("possession", 0)),
            float(data.get("pregame_home_edge", 0)),
        )
    )


@analytics_api_bp.post("/epa")
def epa():
    return jsonify(epa_summary(_payload().get("plays", [])))


@analytics_api_bp.post("/drives")
def drives():
    return jsonify(drive_success_summary(_payload().get("drives", [])))


@analytics_api_bp.post("/simulate")
def simulate():
    data = _payload()
    return jsonify(
        monte_carlo_game(
            float(data.get("home_mean", 23)),
            float(data.get("away_mean", 21)),
            float(data.get("home_sd", 10.5)),
            float(data.get("away_sd", 10.5)),
            int(data.get("simulations", 10000)),
            int(data.get("seed", 13)),
        )
    )


@analytics_api_bp.post("/power-rating")
def rating():
    return jsonify(power_rating(**_payload()))


@analytics_api_bp.post("/injury-impact")
def injuries():
    return jsonify(injury_impact(_payload().get("injuries", [])))


@analytics_api_bp.post("/player-similarity")
def similarity():
    data = _payload()
    return jsonify(
        {
            "matches": player_similarity(
                data.get("target", {}),
                data.get("candidates", {}),
                int(data.get("limit", 5)),
            )
        }
    )


@analytics_api_bp.post("/matchup")
def matchup():
    data = _payload()
    return jsonify(matchup_intelligence(data.get("home", {}), data.get("away", {})))


@analytics_api_bp.post("/game-intelligence")
def intelligence():
    data = _payload()
    home = data.get("home") or {}
    away = data.get("away") or {}
    if not isinstance(home, dict) or not isinstance(away, dict):
        return jsonify({"error": "home and away must be JSON objects"}), 400

    try:
        result = game_intelligence(
            home,
            away,
            home_injuries=data.get("home_injuries") or [],
            away_injuries=data.get("away_injuries") or [],
            weather=data.get("weather") or {},
            market=data.get("market") or {},
            simulations=int(data.get("simulations", 10_000)),
            seed=int(data.get("seed", 31)),
        )
    except (TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)
