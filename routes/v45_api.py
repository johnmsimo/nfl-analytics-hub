"""NFL Analytics Hub v4.5 decision delivery intake endpoints."""

from __future__ import annotations

from typing import Any

from flask import Blueprint, g, jsonify, request

from decision_delivery_v450 import DeliveryConflictError, delivery_manifest, get_delivery_backend
from enterprise_identity_v441 import authorize_context

API_VERSION = "4.5.1"

v45_bp = Blueprint("v45_api", __name__, url_prefix="/api/v4.5")


def _api_context(permission: str) -> tuple[dict[str, Any] | None, tuple[Any, int] | None]:
    context = getattr(g, "enterprise_api_key", None)
    if not isinstance(context, dict):
        return None, (
            jsonify({"error": "a scoped v4.4 API key is required", "code": "API_KEY_REQUIRED"}),
            401,
        )
    try:
        authorize_context(context, context["organization_id"], permission)
    except (KeyError, PermissionError, ValueError) as exc:
        return None, (
            jsonify({"error": str(exc), "code": "ENTERPRISE_ACCESS_DENIED"}),
            403,
        )
    return context, None


def _payload() -> dict[str, Any] | None:
    value = request.get_json(silent=True)
    return value if isinstance(value, dict) else None


@v45_bp.get("/capabilities")
def capabilities():
    return jsonify(
        {
            "version": API_VERSION,
            "contract_version": API_VERSION,
            "features": {
                "redis_delivery_intake": True,
                "idempotent_delivery_jobs": True,
                "delivery_status_inspection": True,
                "outbound_delivery": True,
                "signed_dispatch_worker": True,
            },
            "delivery_contract": delivery_manifest(),
            "endpoints": {
                "capabilities": "/api/v4.5/capabilities",
                "enqueue": "/api/v4.5/deliveries",
                "list": "/api/v4.5/deliveries",
                "status": "/api/v4.5/deliveries/{delivery_id}",
            },
        }
    )


@v45_bp.post("/deliveries")
def enqueue_delivery():
    context, denied = _api_context("decision.execute")
    if denied is not None:
        return denied
    payload = _payload()
    if payload is None:
        return jsonify({"error": "delivery request must be a JSON object"}), 400
    idempotency_key = request.headers.get("Idempotency-Key")
    if not idempotency_key:
        return jsonify(
            {"error": "Idempotency-Key header is required", "code": "MISSING_IDEMPOTENCY_KEY"}
        ), 400
    try:
        result = get_delivery_backend().enqueue(
            context["organization_id"],
            context["api_key_id"],
            idempotency_key,
            payload.get("event_type"),
            payload.get("destination"),
            payload.get("payload"),
        )
    except DeliveryConflictError as exc:
        return jsonify({"error": str(exc), "code": "IDEMPOTENCY_CONFLICT"}), 409
    except RuntimeError as exc:
        return jsonify({"error": str(exc), "code": "DELIVERY_BACKEND_UNAVAILABLE", "retryable": True}), 503
    except ValueError as exc:
        return jsonify({"error": str(exc), "code": "INVALID_DELIVERY_REQUEST"}), 400
    return jsonify(result), 200 if result.get("replayed") else 202


@v45_bp.get("/deliveries")
def list_deliveries():
    context, denied = _api_context("decision.read")
    if denied is not None:
        return denied
    try:
        limit = int(request.args.get("limit", "50"))
        result = get_delivery_backend().list(context["organization_id"], limit)
    except RuntimeError as exc:
        return jsonify({"error": str(exc), "code": "DELIVERY_BACKEND_UNAVAILABLE", "retryable": True}), 503
    except ValueError as exc:
        return jsonify({"error": str(exc), "code": "INVALID_LIMIT"}), 400
    return jsonify({"version": API_VERSION, "deliveries": result})


@v45_bp.get("/deliveries/<delivery_id>")
def get_delivery(delivery_id: str):
    context, denied = _api_context("decision.read")
    if denied is not None:
        return denied
    try:
        result = get_delivery_backend().get(context["organization_id"], delivery_id)
    except RuntimeError as exc:
        return jsonify({"error": str(exc), "code": "DELIVERY_BACKEND_UNAVAILABLE", "retryable": True}), 503
    if result is None:
        return jsonify({"error": "delivery not found", "code": "DELIVERY_NOT_FOUND"}), 404
    return jsonify(result)
