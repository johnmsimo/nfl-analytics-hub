"""NFL Analytics Hub v4.1 advanced-scouting API."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from matchup_v411 import compare_matchup_profiles, compare_tendencies, matchup_brief
from scouting_v41 import cluster_team_styles, personnel_tendencies, player_similarity

v41_bp = Blueprint("v41_api", __name__, url_prefix="/api/v4.1")


def _json_object():
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else None


def _integer(payload, key, default):
    try:
        return int(payload.get(key, default))
    except (TypeError, ValueError):
        return None


def _matchup_profiles(payload):
    if not isinstance(payload.get("offense"), dict):
        return None, "offense must be a JSON object"
    if not isinstance(payload.get("defense"), dict):
        return None, "defense must be a JSON object"
    metrics = payload.get("metrics")
    if not isinstance(metrics, list) or not metrics:
        return None, "metrics must be a non-empty list"
    if not all(isinstance(metric, dict) for metric in metrics):
        return None, "each metric must be a JSON object"
    return metrics, None


@v41_bp.get("/capabilities")
def capabilities():
    return jsonify(
        {
            "version": "4.1.1",
            "status": "active-development",
            "release": "advanced-scouting-intelligence",
            "features": {
                "player_similarity": True,
                "team_style_clustering": True,
                "personnel_tendencies": True,
                "formation_tendencies": True,
                "explainable_outputs": True,
                "matchup_intelligence": True,
                "profile_matchup_comparison": True,
                "tendency_matchup_comparison": True,
                "evidence_ranked_matchup_briefs": True,
            },
            "endpoints": {
                "player_similarity": "/api/v4.1/scouting/player-similarity",
                "team_style_clustering": "/api/v4.1/scouting/team-styles/cluster",
                "tendencies": "/api/v4.1/scouting/tendencies",
                "matchup_comparison": "/api/v4.1/scouting/matchups/compare",
                "matchup_tendencies": "/api/v4.1/scouting/matchups/tendencies",
                "matchup_brief": "/api/v4.1/scouting/matchups/brief",
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
    limit = _integer(payload, "limit", 5)
    if limit is None:
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
    cluster_count = _integer(payload, "cluster_count", 3)
    if cluster_count is None:
        return jsonify({"error": "cluster_count must be an integer"}), 400
    return jsonify(
        cluster_team_styles(payload["teams"], metrics=metrics, cluster_count=cluster_count)
    )


@v41_bp.post("/scouting/tendencies")
def scouting_tendencies():
    payload = _json_object()
    if payload is None or not isinstance(payload.get("plays"), list):
        return jsonify({"error": "plays must be a list"}), 400
    min_snaps = _integer(payload, "min_snaps", 1)
    if min_snaps is None:
        return jsonify({"error": "min_snaps must be an integer"}), 400
    return jsonify(personnel_tendencies(payload["plays"], min_snaps=min_snaps))


@v41_bp.post("/scouting/matchups/compare")
def scouting_matchup_compare():
    payload = _json_object()
    if payload is None:
        return jsonify({"error": "request body must be a JSON object"}), 400
    metrics, error = _matchup_profiles(payload)
    if error:
        return jsonify({"error": error}), 400
    min_sample = _integer(payload, "min_sample", 20)
    limit = _integer(payload, "limit", 10)
    if min_sample is None:
        return jsonify({"error": "min_sample must be an integer"}), 400
    if limit is None:
        return jsonify({"error": "limit must be an integer"}), 400
    return jsonify(
        compare_matchup_profiles(
            payload["offense"],
            payload["defense"],
            metrics,
            min_sample=min_sample,
            limit=limit,
        )
    )


@v41_bp.post("/scouting/matchups/tendencies")
def scouting_matchup_tendencies():
    payload = _json_object()
    if payload is None or not isinstance(payload.get("offense"), list):
        return jsonify({"error": "offense must be a list"}), 400
    if not isinstance(payload.get("defense"), list):
        return jsonify({"error": "defense must be a list"}), 400
    metrics = payload.get("metrics")
    if metrics is not None and not isinstance(metrics, list):
        return jsonify({"error": "metrics must be a list"}), 400
    min_snaps = _integer(payload, "min_snaps", 10)
    limit = _integer(payload, "limit", 10)
    if min_snaps is None:
        return jsonify({"error": "min_snaps must be an integer"}), 400
    if limit is None:
        return jsonify({"error": "limit must be an integer"}), 400
    return jsonify(
        compare_tendencies(
            payload["offense"],
            payload["defense"],
            metrics=metrics,
            min_snaps=min_snaps,
            limit=limit,
        )
    )


@v41_bp.post("/scouting/matchups/brief")
def scouting_matchup_brief():
    payload = _json_object()
    if payload is None:
        return jsonify({"error": "request body must be a JSON object"}), 400
    metrics, error = _matchup_profiles(payload)
    if error:
        return jsonify({"error": error}), 400
    offense_tendencies = payload.get("offense_tendencies", [])
    defense_tendencies = payload.get("defense_tendencies", [])
    if not isinstance(offense_tendencies, list):
        return jsonify({"error": "offense_tendencies must be a list"}), 400
    if not isinstance(defense_tendencies, list):
        return jsonify({"error": "defense_tendencies must be a list"}), 400
    tendency_metrics = payload.get("tendency_metrics")
    if tendency_metrics is not None and not isinstance(tendency_metrics, list):
        return jsonify({"error": "tendency_metrics must be a list"}), 400
    min_sample = _integer(payload, "min_sample", 20)
    min_snaps = _integer(payload, "min_snaps", 10)
    limit = _integer(payload, "limit", 5)
    if min_sample is None:
        return jsonify({"error": "min_sample must be an integer"}), 400
    if min_snaps is None:
        return jsonify({"error": "min_snaps must be an integer"}), 400
    if limit is None:
        return jsonify({"error": "limit must be an integer"}), 400
    profile_result = compare_matchup_profiles(
        payload["offense"],
        payload["defense"],
        metrics,
        min_sample=min_sample,
        limit=limit,
    )
    tendency_result = compare_tendencies(
        offense_tendencies,
        defense_tendencies,
        metrics=tendency_metrics,
        min_snaps=min_snaps,
        limit=limit,
    )
    return jsonify(matchup_brief(profile_result, tendency_result, limit=limit))
