"""NFL Analytics Hub v4.5 decision delivery intake endpoints."""

from __future__ import annotations

from typing import Any

from flask import Blueprint, g, jsonify, request

from decision_delivery_v450 import DeliveryConflictError, delivery_manifest, get_delivery_backend
from delivery_observability_v453 import delivery_health
from delivery_operations_v452 import (
    audit_delivery_event,
    delivery_metrics,
    ensure_workspace_scope,
    list_dead_letters,
    replay_delivery,
)
from enterprise_identity_v441 import authorize_context

API_VERSION = "4.5.3"

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
                "dead_letter_inspection": True,
                "delivery_replay": True,
                "delivery_metrics": True,
                "workspace_scoped_delivery": True,
                "audit_integrated_delivery": True,
                "delivery_health": True,
            },
            "delivery_contract": delivery_manifest(),
            "endpoints": {
                "capabilities": "/api/v4.5/capabilities",
                "enqueue": "/api/v4.5/deliveries",
                "list": "/api/v4.5/deliveries",
                "status": "/api/v4.5/deliveries/{delivery_id}",
                "dead_letters": "/api/v4.5/deliveries/dead-letters",
                "metrics": "/api/v4.5/deliveries/metrics",
                "replay": "/api/v4.5/deliveries/{delivery_id}/replay",
                "health": "/api/v4.5/deliveries/health",
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
    workspace_id = payload.get("workspace_id")
    if workspace_id is not None:
        workspace_id = str(workspace_id).strip()
        if not workspace_id or len(workspace_id) > 128:
            return jsonify({"error": "workspace_id must contain 1-128 characters", "code": "INVALID_WORKSPACE"}), 400
        try:
            ensure_workspace_scope(context["organization_id"], workspace_id, context)
        except (KeyError, PermissionError, ValueError) as exc:
            return jsonify({"error": str(exc), "code": "WORKSPACE_ACCESS_DENIED"}), 403
    try:
        result = get_delivery_backend().enqueue(
            context["organization_id"],
            context["api_key_id"],
            idempotency_key,
            payload.get("event_type"),
            payload.get("destination"),
            payload.get("payload"),
            workspace_id=workspace_id,
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
        workspace_id = request.args.get("workspace_id")
        if workspace_id:
            ensure_workspace_scope(context["organization_id"], workspace_id, context)
        result = get_delivery_backend().list(context["organization_id"], limit)
        if workspace_id:
            result = [item for item in result if item.get("workspace_id") == workspace_id]
    except RuntimeError as exc:
        return jsonify({"error": str(exc), "code": "DELIVERY_BACKEND_UNAVAILABLE", "retryable": True}), 503
    except (KeyError, PermissionError) as exc:
        return jsonify({"error": str(exc), "code": "WORKSPACE_ACCESS_DENIED"}), 403
    except ValueError as exc:
        return jsonify({"error": str(exc), "code": "INVALID_LIMIT"}), 400
    return jsonify({"version": API_VERSION, "deliveries": result})


@v45_bp.get("/deliveries/health")
def get_delivery_health():
    _context, denied = _api_context("decision.read")
    if denied is not None:
        return denied
    try:
        result = delivery_health(get_delivery_backend())
    except RuntimeError as exc:
        return jsonify({"error": str(exc), "code": "DELIVERY_BACKEND_UNAVAILABLE", "retryable": True}), 503
    return jsonify(result)


@v45_bp.get("/deliveries/dead-letters")
def get_dead_letters():
    context, denied = _api_context("decision.read")
    if denied is not None:
        return denied
    workspace_id = request.args.get("workspace_id")
    try:
        if workspace_id:
            ensure_workspace_scope(context["organization_id"], workspace_id, context)
        result = list_dead_letters(
            get_delivery_backend(),
            context["organization_id"],
            limit=request.args.get("limit"),
            workspace_id=workspace_id,
        )
    except RuntimeError as exc:
        return jsonify({"error": str(exc), "code": "DELIVERY_BACKEND_UNAVAILABLE", "retryable": True}), 503
    except (KeyError, PermissionError) as exc:
        return jsonify({"error": str(exc), "code": "WORKSPACE_ACCESS_DENIED"}), 403
    except ValueError as exc:
        return jsonify({"error": str(exc), "code": "INVALID_DELIVERY_OPERATION"}), 400
    return jsonify({"version": "4.5.2", "organization_id": context["organization_id"], "workspace_id": workspace_id, "dead_letters": result})


@v45_bp.get("/deliveries/metrics")
def get_delivery_metrics():
    context, denied = _api_context("decision.read")
    if denied is not None:
        return denied
    workspace_id = request.args.get("workspace_id")
    try:
        if workspace_id:
            ensure_workspace_scope(context["organization_id"], workspace_id, context)
        result = delivery_metrics(
            get_delivery_backend(),
            context["organization_id"],
            workspace_id=workspace_id,
        )
    except RuntimeError as exc:
        return jsonify({"error": str(exc), "code": "DELIVERY_BACKEND_UNAVAILABLE", "retryable": True}), 503
    except (KeyError, PermissionError) as exc:
        return jsonify({"error": str(exc), "code": "WORKSPACE_ACCESS_DENIED"}), 403
    except ValueError as exc:
        return jsonify({"error": str(exc), "code": "INVALID_DELIVERY_OPERATION"}), 400
    return jsonify(result)


@v45_bp.post("/deliveries/<delivery_id>/replay")
def replay_delivery_route(delivery_id: str):
    context, denied = _api_context("decision.execute")
    if denied is not None:
        return denied
    payload = _payload() or {}
    workspace_id = payload.get("workspace_id") or request.args.get("workspace_id")
    if workspace_id is not None:
        workspace_id = str(workspace_id).strip()
        if not workspace_id or len(workspace_id) > 128:
            return jsonify({"error": "workspace_id must contain 1-128 characters", "code": "INVALID_WORKSPACE"}), 400
    try:
        if workspace_id:
            ensure_workspace_scope(context["organization_id"], workspace_id, context)
        result = replay_delivery(
            get_delivery_backend(),
            context["organization_id"],
            delivery_id,
            context=context,
            workspace_id=workspace_id,
        )
        audit_delivery_event(
            context["organization_id"],
            context,
            "delivery.replayed",
            "delivery",
            delivery_id,
            {"replay_count": result.get("replay_count", 1), "workspace_id": result.get("workspace_id")},
            workspace_id=result.get("workspace_id"),
        )
    except RuntimeError as exc:
        return jsonify({"error": str(exc), "code": "DELIVERY_BACKEND_UNAVAILABLE", "retryable": True}), 503
    except KeyError:
        return jsonify({"error": "delivery not found", "code": "DELIVERY_NOT_FOUND"}), 404
    except PermissionError as exc:
        return jsonify({"error": str(exc), "code": "WORKSPACE_ACCESS_DENIED"}), 403
    except ValueError as exc:
        return jsonify({"error": str(exc), "code": "DELIVERY_NOT_REPLAYABLE"}), 409
    return jsonify(result), 202


@v45_bp.get("/deliveries/<delivery_id>")
def get_delivery(delivery_id: str):
    context, denied = _api_context("decision.read")
    if denied is not None:
        return denied
    workspace_id = request.args.get("workspace_id")
    try:
        if workspace_id:
            ensure_workspace_scope(context["organization_id"], workspace_id, context)
        result = get_delivery_backend().get(context["organization_id"], delivery_id)
        if result is not None and workspace_id and result.get("workspace_id") != workspace_id:
            result = None
    except RuntimeError as exc:
        return jsonify({"error": str(exc), "code": "DELIVERY_BACKEND_UNAVAILABLE", "retryable": True}), 503
    if result is None:
        return jsonify({"error": "delivery not found", "code": "DELIVERY_NOT_FOUND"}), 404
    return jsonify(result)
