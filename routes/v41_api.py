"""NFL Analytics Hub v4.1 advanced-scouting API."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from scouting_v41 import cluster_team_styles, personnel_tendencies, player_similarity

v41_bp = Blueprint("v41_api", __name__, url_prefix="/api/v4.1")


def _json_object():
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else None


@v41_bp.get("/capabilities")
def capabilities():
    return jsonify(
        {
            "version": "4.1.0",
            "status": "active-development",
            "release": "advanced-scouting-intelligence",
            "features": {
                "player_similarity": True,
                "team_style_clustering": True,
                "personnel_tendencies": True,
                "formation_tendencies": True,
                "explainable_outputs": True,
                "matchup_intelligence": False,
            },
            "endpoints": {
                "player_similarity": "/api/v4.1/scouting/player-similarity",
                "team_style_clustering": "/api/v4.1/scouting/team-styles/cluster",
                "tendencies": "/api/v4.1/scouting/tendencies",
            },
        }
    )


@v41_bp.post("/scouting/player-similarity")
def scouting_player_similarity():
    payload = _json_object()
    if payload is None or not isinstance(payload.get("target"), dict):
        return jsonify({"error": "target must be a JSON object"}), 400
    if not isinstance(payload.get("candidates"), list):
        return jsonify({"error": "candidates must be a list"}), 400
    metrics = payload.get("metrics")
    if metrics is not None and not isinstance(metrics, list):
        return jsonify({"error": "metrics must be a list"}), 400
    try:
        limit = int(payload.get("limit", 5))
    except (TypeError, ValueError):
        return jsonify({"error": "limit must be an integer"}), 400
    return jsonify(
        player_similarity(payload["target"], payload["candidates"], metrics=metrics, limit=limit)
    )


@v41_bp.post("/scouting/team-styles/cluster")
def scouting_team_style_clusters():
    payload = _json_object()
    if payload is None or not isinstance(payload.get("teams"), list):
        return jsonify({"error": "teams must be a list"}), 400
    metrics = payload.get("metrics")
    if metrics is not None and not isinstance(metrics, list):
        return jsonify({"error": "metrics must be a list"}), 400
    try:
        cluster_count = int(payload.get("cluster_count", 3))
    except (TypeError, ValueError):
        return jsonify({"error": "cluster_count must be an integer"}), 400
    return jsonify(
        cluster_team_styles(payload["teams"], metrics=metrics, cluster_count=cluster_count)
    )


@v41_bp.post("/scouting/tendencies")
def scouting_tendencies():
    payload = _json_object()
    if payload is None or not isinstance(payload.get("plays"), list):
        return jsonify({"error": "plays must be a list"}), 400
    try:
        min_snaps = int(payload.get("min_snaps", 1))
    except (TypeError, ValueError):
        return jsonify({"error": "min_snaps must be an integer"}), 400
    return jsonify(personnel_tendencies(payload["plays"], min_snaps=min_snaps))
