"""NFL Analytics Hub v4.3 model lifecycle endpoints."""

from __future__ import annotations

from typing import Any

from flask import Blueprint, jsonify, request

from evaluation_v431 import (
    evaluation_manifest,
    evaluation_metric_catalog,
    run_held_out_evaluation,
    select_champion_challenger,
)
from lifecycle_v43 import (
    lifecycle_manifest,
    normalize_model_version,
    normalize_promotion_policy,
    transition_model_version,
)
from rollout_v432 import (
    build_retraining_request,
    evaluate_retraining_triggers,
    evaluate_rollout_step,
    normalize_rollout_plan,
    rollout_manifest,
)

v43_bp = Blueprint("v43_api", __name__, url_prefix="/api/v4.3")


def _json_object() -> dict[str, Any] | None:
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else None


@v43_bp.get("/capabilities")
def capabilities():
    registry = lifecycle_manifest()
    evaluation = evaluation_manifest()
    rollout = rollout_manifest()
    return jsonify(
        {
            **rollout,
            "features": {
                **registry["features"],
                **evaluation["features"],
                **rollout["features"],
                "automated_evaluation": True,
                "champion_challenger_automation": True,
            },
            "registry_contract_version": registry["version"],
            "evaluation_contract_version": evaluation["version"],
            "rollout_contract_version": rollout["version"],
            "endpoints": {
                "capabilities": "/api/v4.3/capabilities",
                "model_version_normalize": "/api/v4.3/models/versions/normalize",
                "transition_validate": "/api/v4.3/models/transitions/validate",
                "promotion_policy_normalize": ("/api/v4.3/models/promotion-policies/normalize"),
                "evaluation_metrics": "/api/v4.3/models/evaluations/metrics",
                "evaluation_run": "/api/v4.3/models/evaluations/run",
                "champion_challenger_select": ("/api/v4.3/models/champion-challenger/select"),
                "retraining_trigger_evaluate": ("/api/v4.3/models/retraining/triggers/evaluate"),
                "retraining_request_normalize": ("/api/v4.3/models/retraining/requests/normalize"),
                "rollout_plan_normalize": "/api/v4.3/models/rollouts/plans/normalize",
                "rollout_step_evaluate": "/api/v4.3/models/rollouts/steps/evaluate",
            },
        }
    )


@v43_bp.post("/models/versions/normalize")
def normalize_registry_model_version():
    payload = _json_object()
    if payload is None:
        return jsonify({"error": "model version must be a JSON object"}), 400
    try:
        result = normalize_model_version(
            payload,
            registered_at=payload.get("registered_at"),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


@v43_bp.post("/models/transitions/validate")
def validate_model_transition():
    payload = _json_object()
    if payload is None or not isinstance(payload.get("model_version"), dict):
        return jsonify({"error": "model_version must be a JSON object"}), 400
    try:
        result = transition_model_version(
            payload["model_version"],
            payload.get("target_status"),
            occurred_at=payload.get("occurred_at"),
            actor=payload.get("actor"),
            reason=payload.get("reason"),
            promotion_decision=payload.get("promotion_decision"),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


@v43_bp.post("/models/promotion-policies/normalize")
def normalize_registry_promotion_policy():
    payload = _json_object()
    if payload is None:
        return jsonify({"error": "promotion policy must be a JSON object"}), 400
    try:
        result = normalize_promotion_policy(payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


@v43_bp.get("/models/evaluations/metrics")
def evaluation_metrics():
    return jsonify(evaluation_metric_catalog())


@v43_bp.post("/models/evaluations/run")
def run_model_evaluation():
    payload = _json_object()
    if payload is None:
        return jsonify({"error": "evaluation must be a JSON object"}), 400
    try:
        result = run_held_out_evaluation(
            payload,
            evaluated_at=payload.get("evaluated_at"),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


@v43_bp.post("/models/champion-challenger/select")
def select_model_champion():
    payload = _json_object()
    if payload is None:
        return jsonify({"error": "selection must be a JSON object"}), 400
    try:
        result = select_champion_challenger(
            payload,
            decided_at=payload.get("decided_at"),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


@v43_bp.post("/models/retraining/triggers/evaluate")
def evaluate_model_retraining_trigger():
    payload = _json_object()
    if payload is None:
        return jsonify({"error": "trigger evaluation must be a JSON object"}), 400
    try:
        result = evaluate_retraining_triggers(
            payload,
            evaluated_at=payload.get("evaluated_at"),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


@v43_bp.post("/models/retraining/requests/normalize")
def normalize_model_retraining_request():
    payload = _json_object()
    if payload is None:
        return jsonify({"error": "retraining request must be a JSON object"}), 400
    try:
        result = build_retraining_request(
            payload,
            requested_at=payload.get("requested_at"),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


@v43_bp.post("/models/rollouts/plans/normalize")
def normalize_model_rollout_plan():
    payload = _json_object()
    if payload is None:
        return jsonify({"error": "rollout plan must be a JSON object"}), 400
    try:
        result = normalize_rollout_plan(
            payload,
            planned_at=payload.get("planned_at"),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


@v43_bp.post("/models/rollouts/steps/evaluate")
def evaluate_model_rollout_step():
    payload = _json_object()
    if payload is None:
        return jsonify({"error": "rollout step evaluation must be a JSON object"}), 400
    try:
        result = evaluate_rollout_step(
            payload,
            evaluated_at=payload.get("evaluated_at"),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)
