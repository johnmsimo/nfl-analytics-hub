"""NFL Analytics Hub v4.0 decision-intelligence API."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from ai_decision_v4 import decision_brief, ensemble_decision, scenario_decision

v4_bp = Blueprint("v4_api", __name__, url_prefix="/api/v4")


def _json_object():
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else None


@v4_bp.get("/capabilities")
def capabilities():
    return jsonify(
        {
            "version": "4.0",
            "status": "foundation",
            "features": {
                "reliability_weighted_ensemble": True,
                "automatic_primary_model": True,
                "scenario_analysis": True,
                "decision_explanations": True,
                "model_disagreement": True,
                "risk_classification": True,
            },
            "endpoints": {
                "ensemble": "/api/v4/decisions/ensemble",
                "scenario": "/api/v4/decisions/scenario",
                "brief": "/api/v4/decisions/brief",
            },
        }
    )


@v4_bp.post("/decisions/ensemble")
def ensemble():
    payload = _json_object()
    if payload is None or not isinstance(payload.get("models"), list):
        return jsonify({"error": "models must be a list"}), 400
    return jsonify(ensemble_decision(payload["models"]))


@v4_bp.post("/decisions/scenario")
def scenario():
    payload = _json_object()
    if payload is None or not isinstance(payload.get("baseline"), dict):
        return jsonify({"error": "baseline must be a JSON object"}), 400
    scenarios = payload.get("scenarios", [])
    if not isinstance(scenarios, list):
        return jsonify({"error": "scenarios must be a list"}), 400
    return jsonify(scenario_decision(payload["baseline"], scenarios))


@v4_bp.post("/decisions/brief")
def brief():
    payload = _json_object()
    if payload is None or not isinstance(payload.get("ensemble"), dict):
        return jsonify({"error": "ensemble must be a JSON object"}), 400
    scenario_result = payload.get("scenario")
    if scenario_result is not None and not isinstance(scenario_result, dict):
        return jsonify({"error": "scenario must be a JSON object"}), 400
    return jsonify(decision_brief(payload["ensemble"], scenario_result))
