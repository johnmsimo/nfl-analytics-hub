"""NFL Analytics Hub v4.3 model lifecycle endpoints."""

from __future__ import annotations

from typing import Any

from flask import Blueprint, jsonify, request

from lifecycle_v43 import (
    lifecycle_manifest,
    normalize_model_version,
    normalize_promotion_policy,
    transition_model_version,
)

v43_bp = Blueprint("v43_api", __name__, url_prefix="/api/v4.3")


def _json_object() -> dict[str, Any] | None:
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else None


@v43_bp.get("/capabilities")
def capabilities():
    manifest = lifecycle_manifest()
    return jsonify(
        {
            **manifest,
            "registry_contract_version": manifest["version"],
            "endpoints": {
                "capabilities": "/api/v4.3/capabilities",
                "model_version_normalize": "/api/v4.3/models/versions/normalize",
                "transition_validate": "/api/v4.3/models/transitions/validate",
                "promotion_policy_normalize": ("/api/v4.3/models/promotion-policies/normalize"),
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
