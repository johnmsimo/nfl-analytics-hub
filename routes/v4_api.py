"""NFL Analytics Hub v4.0 decision-intelligence API."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from ai_decision_v4 import decision_brief, ensemble_decision, scenario_decision
from ai_insights_v4 import (
    confidence_reasoning,
    decision_history,
    evidence_recommendations,
    explain_prediction_change,
    upset_alert,
)
from simulation_lab_v4 import compare_scenarios, sensitivity_analysis, simulate_game

v4_bp = Blueprint("v4_api", __name__, url_prefix="/api/v4")


def _json_object():
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else None


@v4_bp.get("/capabilities")
def capabilities():
    return jsonify(
        {
            "version": "4.0",
            "status": "active-development",
            "features": {
                "reliability_weighted_ensemble": True,
                "automatic_primary_model": True,
                "scenario_analysis": True,
                "decision_explanations": True,
                "model_disagreement": True,
                "risk_classification": True,
                "distribution_simulation": True,
                "scenario_comparison": True,
                "sensitivity_analysis": True,
                "deterministic_seeds": True,
                "prediction_change_explanations": True,
                "upset_alerts": True,
                "confidence_reasoning": True,
                "evidence_linked_recommendations": True,
                "decision_history": True,
            },
            "endpoints": {
                "ensemble": "/api/v4/decisions/ensemble",
                "scenario": "/api/v4/decisions/scenario",
                "brief": "/api/v4/decisions/brief",
                "simulation": "/api/v4/simulations/run",
                "comparison": "/api/v4/simulations/compare",
                "sensitivity": "/api/v4/simulations/sensitivity",
                "change_explanation": "/api/v4/insights/change",
                "upset_alert": "/api/v4/insights/upset-alert",
                "confidence": "/api/v4/insights/confidence",
                "recommendations": "/api/v4/insights/recommendations",
                "history": "/api/v4/insights/history",
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


@v4_bp.post("/simulations/run")
def simulation_run():
    payload = _json_object()
    if payload is None or not isinstance(payload.get("profile"), dict):
        return jsonify({"error": "profile must be a JSON object"}), 400
    adjustments = payload.get("adjustments", [])
    if not isinstance(adjustments, list):
        return jsonify({"error": "adjustments must be a list"}), 400
    return jsonify(simulate_game(payload["profile"], adjustments))


@v4_bp.post("/simulations/compare")
def simulation_compare():
    payload = _json_object()
    if payload is None or not isinstance(payload.get("profile"), dict):
        return jsonify({"error": "profile must be a JSON object"}), 400
    scenarios = payload.get("scenarios", [])
    if not isinstance(scenarios, list):
        return jsonify({"error": "scenarios must be a list"}), 400
    return jsonify(compare_scenarios(payload["profile"], scenarios))


@v4_bp.post("/simulations/sensitivity")
def simulation_sensitivity():
    payload = _json_object()
    if payload is None or not isinstance(payload.get("profile"), dict):
        return jsonify({"error": "profile must be a JSON object"}), 400
    factors = payload.get("factors", [])
    if not isinstance(factors, list):
        return jsonify({"error": "factors must be a list"}), 400
    return jsonify(sensitivity_analysis(payload["profile"], factors))


@v4_bp.post("/insights/change")
def insight_change():
    payload = _json_object()
    if payload is None or not isinstance(payload.get("previous"), dict):
        return jsonify({"error": "previous must be a JSON object"}), 400
    if not isinstance(payload.get("current"), dict):
        return jsonify({"error": "current must be a JSON object"}), 400
    evidence = payload.get("evidence", [])
    if not isinstance(evidence, list):
        return jsonify({"error": "evidence must be a list"}), 400
    return jsonify(explain_prediction_change(payload["previous"], payload["current"], evidence))


@v4_bp.post("/insights/upset-alert")
def insight_upset_alert():
    payload = _json_object()
    if payload is None or not isinstance(payload.get("decision"), dict):
        return jsonify({"error": "decision must be a JSON object"}), 400
    if not isinstance(payload.get("market"), dict):
        return jsonify({"error": "market must be a JSON object"}), 400
    return jsonify(upset_alert(payload["decision"], payload["market"]))


@v4_bp.post("/insights/confidence")
def insight_confidence():
    payload = _json_object()
    if payload is None or not isinstance(payload.get("decision"), dict):
        return jsonify({"error": "decision must be a JSON object"}), 400
    return jsonify(confidence_reasoning(payload["decision"]))


@v4_bp.post("/insights/recommendations")
def insight_recommendations():
    payload = _json_object()
    if payload is None or not isinstance(payload.get("decision"), dict):
        return jsonify({"error": "decision must be a JSON object"}), 400
    evidence = payload.get("evidence", [])
    if not isinstance(evidence, list):
        return jsonify({"error": "evidence must be a list"}), 400
    return jsonify(evidence_recommendations(payload["decision"], evidence))


@v4_bp.post("/insights/history")
def insight_history():
    payload = _json_object()
    if payload is None or not isinstance(payload.get("events"), list):
        return jsonify({"error": "events must be a list"}), 400
    return jsonify(decision_history(payload["events"]))
