"""NFL Analytics Hub v4.3 model lifecycle endpoints."""

from __future__ import annotations

from typing import Any

from flask import Blueprint, current_app, jsonify, request, session
from redis.exceptions import RedisError

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
from operations_v433 import (
    build_lifecycle_operations,
    operations_manifest,
    workspace_manifest,
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


def _operations_store():
    store = current_app.extensions.get("v43_lifecycle_operations")
    if store is None:
        store = build_lifecycle_operations()
        current_app.extensions["v43_lifecycle_operations"] = store
    return store


def _actor(payload: dict[str, Any]) -> Any:
    user = session.get("user") if session else None
    return (
        (user or {}).get("username")
        or payload.get("actor")
        or payload.get("requested_by")
        or payload.get("decided_by")
    )


def _operations_error(exc: Exception):
    if isinstance(exc, KeyError):
        return jsonify({"error": str(exc.args[0])}), 404
    if isinstance(exc, ValueError):
        return jsonify({"error": str(exc)}), 400
    current_app.logger.exception("Lifecycle operations backend failure")
    return jsonify({"error": "lifecycle operations backend unavailable"}), 503


@v43_bp.get("/capabilities")
def capabilities():
    registry = lifecycle_manifest()
    evaluation = evaluation_manifest()
    rollout = rollout_manifest()
    operations = operations_manifest()
    return jsonify(
        {
            **operations,
            "features": {
                **registry["features"],
                **evaluation["features"],
                **rollout["features"],
                **operations["features"],
                "automated_evaluation": True,
                "champion_challenger_automation": True,
            },
            "registry_contract_version": registry["version"],
            "evaluation_contract_version": evaluation["version"],
            "rollout_contract_version": rollout["version"],
            "operations_contract_version": operations["version"],
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
                "registry_versions": "/api/v4.3/operations/registry/versions",
                "approvals": "/api/v4.3/operations/approvals",
                "health_observations": "/api/v4.3/operations/health/observations",
                "operations_status": "/api/v4.3/operations/status",
                "audit": "/api/v4.3/operations/audit",
                "workspace": "/api/v4.3/operations/workspace",
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


@v43_bp.get("/operations/workspace")
def model_operations_workspace():
    return jsonify(workspace_manifest())


@v43_bp.get("/operations/status")
def model_operations_status():
    try:
        return jsonify(_operations_store().operations_snapshot())
    except (RedisError, RuntimeError, OSError) as exc:
        return _operations_error(exc)


@v43_bp.get("/operations/registry/versions")
def list_registry_versions():
    try:
        records = _operations_store().list_versions(
            model_key=request.args.get("model_key"),
            status=request.args.get("status"),
            limit=request.args.get("limit", 100),
        )
    except (RedisError, RuntimeError, OSError, ValueError) as exc:
        return _operations_error(exc)
    return jsonify({"version": "4.3.3", "model_versions": records, "count": len(records)})


@v43_bp.post("/operations/registry/versions")
def register_registry_version():
    payload = _json_object()
    if payload is None:
        return jsonify({"error": "model version must be a JSON object"}), 400
    try:
        result = _operations_store().register(
            payload,
            registered_at=payload.get("registered_at"),
        )
    except (RedisError, RuntimeError, OSError, ValueError) as exc:
        return _operations_error(exc)
    return jsonify(result), 201 if result["accepted"] else 200


@v43_bp.get("/operations/registry/versions/<model_version_id>")
def get_registry_version(model_version_id: str):
    try:
        record = _operations_store().get(model_version_id)
    except (RedisError, RuntimeError, OSError, ValueError) as exc:
        return _operations_error(exc)
    if record is None:
        return jsonify({"error": "model version not found"}), 404
    return jsonify(record)


@v43_bp.post("/operations/registry/versions/<model_version_id>/transitions")
def apply_registry_transition(model_version_id: str):
    payload = _json_object()
    if payload is None:
        return jsonify({"error": "transition must be a JSON object"}), 400
    try:
        result = _operations_store().transition(
            model_version_id,
            payload.get("target_status"),
            actor=_actor(payload),
            reason=payload.get("reason"),
            occurred_at=payload.get("occurred_at"),
            promotion_decision=payload.get("promotion_decision"),
            approval_id=payload.get("approval_id"),
        )
    except (KeyError, RedisError, RuntimeError, OSError, ValueError) as exc:
        return _operations_error(exc)
    return jsonify(result)


@v43_bp.get("/operations/approvals")
def list_operation_approvals():
    try:
        records = _operations_store().list_approvals(
            status=request.args.get("status"),
            limit=request.args.get("limit", 100),
        )
    except (RedisError, RuntimeError, OSError, ValueError) as exc:
        return _operations_error(exc)
    return jsonify({"version": "4.3.3", "approvals": records, "count": len(records)})


@v43_bp.post("/operations/approvals")
def request_operation_approval():
    payload = _json_object()
    if payload is None:
        return jsonify({"error": "approval request must be a JSON object"}), 400
    request_payload = {**payload, "requested_by": _actor(payload)}
    try:
        result = _operations_store().request_approval(
            request_payload,
            requested_at=payload.get("requested_at"),
        )
    except (RedisError, RuntimeError, OSError, ValueError) as exc:
        return _operations_error(exc)
    return jsonify(result), 201 if result["accepted"] else 200


@v43_bp.post("/operations/approvals/<approval_id>/decisions")
def decide_operation_approval(approval_id: str):
    payload = _json_object()
    if payload is None:
        return jsonify({"error": "approval decision must be a JSON object"}), 400
    try:
        result = _operations_store().decide_approval(
            approval_id,
            payload.get("decision"),
            decided_by=_actor(payload),
            reason=payload.get("reason"),
            decided_at=payload.get("decided_at"),
        )
    except (KeyError, RedisError, RuntimeError, OSError, ValueError) as exc:
        return _operations_error(exc)
    return jsonify(result)


@v43_bp.post("/operations/health/observations")
def record_model_health():
    payload = _json_object()
    if payload is None:
        return jsonify({"error": "health observation must be a JSON object"}), 400
    try:
        result = _operations_store().record_health(
            payload,
            observed_at=payload.get("observed_at"),
            actor=_actor(payload),
        )
    except (KeyError, RedisError, RuntimeError, OSError, ValueError) as exc:
        return _operations_error(exc)
    return jsonify(result), 201


@v43_bp.get("/operations/audit")
def lifecycle_audit_history():
    try:
        records = _operations_store().audit_history(
            resource_id=request.args.get("resource_id"),
            limit=request.args.get("limit", 100),
        )
    except (RedisError, RuntimeError, OSError, ValueError) as exc:
        return _operations_error(exc)
    return jsonify({"version": "4.3.3", "events": records, "count": len(records)})
