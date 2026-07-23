"""Flask adapter for NFL Analytics Hub v3.1 game intelligence."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from analytics_engine import game_intelligence


game_intelligence_bp = Blueprint(
    "game_intelligence_api",
    __name__,
    url_prefix="/api/v3/game-intelligence",
)


@game_intelligence_bp.post("")
def build_game_intelligence():
    data = request.get_json(silent=True) or {}
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
