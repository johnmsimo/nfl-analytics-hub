"""NFL Analytics Hub v4.2 distributed intelligence endpoints."""

from __future__ import annotations

from typing import Any

from flask import Blueprint, jsonify, request

from distributed_v42 import job_event, normalize_job, platform_manifest, transition_job
from execution_v422 import (
    TypedHandlerRegistry,
    build_execution_store,
    execution_manifest,
    normalize_cancellation_request,
)
from operations_v423 import (
    build_distributed_cache,
    component_health,
    normalize_cache_key,
    normalize_invalidation_event,
    operations_manifest,
)
from transport_v421 import build_transport, normalize_lease, transport_manifest

v42_bp = Blueprint("v42_api", __name__, url_prefix="/api/v4.2")


def _json_object() -> dict[str, Any] | None:
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else None


@v42_bp.get("/capabilities")
def capabilities():
    foundation = platform_manifest()
    transport = transport_manifest()
    execution = execution_manifest()
    operations = operations_manifest()
    return jsonify(
        {
            **foundation,
            "version": operations["version"],
            "job_contract_version": operations["job_contract_version"],
            "features": {
                **foundation["features"],
                **transport["features"],
                **execution["features"],
                **operations["features"],
            },
            "transport": {
                "backends": transport["backends"],
                "limits": transport["limits"],
            },
            "execution": {
                "handlers": execution["handlers"],
                "result_backends": execution["result_backends"],
                "limits": execution["limits"],
            },
            "operations": {
                "cache_backends": operations["cache_backends"],
                "limits": operations["limits"],
                "scaling": operations["scaling"],
            },
            "next_increment": operations["next_increment"],
            "endpoints": {
                "capabilities": "/api/v4.2/capabilities",
                "job_normalize": "/api/v4.2/jobs/normalize",
                "transition_validate": "/api/v4.2/jobs/transitions/validate",
                "event_normalize": "/api/v4.2/jobs/events/normalize",
                "transport_capabilities": "/api/v4.2/transport/capabilities",
                "lease_normalize": "/api/v4.2/transport/leases/normalize",
                "execution_capabilities": "/api/v4.2/execution/capabilities",
                "execution_validate": "/api/v4.2/execution/jobs/validate",
                "cancellation_normalize": ("/api/v4.2/execution/cancellations/normalize"),
                "operations_capabilities": "/api/v4.2/operations/capabilities",
                "cache_key_normalize": "/api/v4.2/cache/keys/normalize",
                "cache_invalidation_normalize": ("/api/v4.2/cache/invalidations/normalize"),
                "operations_snapshot": "/api/v4.2/operations/snapshot",
                "dead_letters": "/api/v4.2/operations/dead-letters",
            },
        }
    )


@v42_bp.get("/transport/capabilities")
def transport_capabilities():
    return jsonify(transport_manifest())


@v42_bp.post("/transport/leases/normalize")
def normalize_transport_lease():
    payload = _json_object()
    if payload is None or not isinstance(payload.get("job"), dict):
        return jsonify({"error": "job must be a JSON object"}), 400
    try:
        result = normalize_lease(
            payload["job"],
            str(payload.get("message_id", "")),
            str(payload.get("worker_id", "")),
            claimed_at=payload.get("claimed_at"),
            lease_seconds=payload.get("lease_seconds", 60),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


@v42_bp.get("/execution/capabilities")
def execution_capabilities():
    return jsonify(execution_manifest())


@v42_bp.post("/execution/jobs/validate")
def validate_execution_job():
    payload = _json_object()
    if payload is None or not isinstance(payload.get("job"), dict):
        return jsonify({"error": "job must be a JSON object"}), 400
    try:
        result = TypedHandlerRegistry().validate(payload["job"])
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


@v42_bp.post("/execution/cancellations/normalize")
def normalize_execution_cancellation():
    payload = _json_object()
    if payload is None:
        return jsonify({"error": "cancellation must be a JSON object"}), 400
    try:
        result = normalize_cancellation_request(
            payload.get("job_id"),
            requested_at=payload.get("requested_at"),
            reason=payload.get("reason", "cancelled by request"),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


@v42_bp.get("/operations/capabilities")
def operations_capabilities():
    return jsonify(operations_manifest())


@v42_bp.post("/cache/keys/normalize")
def normalize_distributed_cache_key():
    payload = _json_object()
    if payload is None:
        return jsonify({"error": "cache key must be a JSON object"}), 400
    try:
        result = normalize_cache_key(
            payload.get("namespace"),
            payload.get("key"),
            cache_version=payload.get("cache_version", 1),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


@v42_bp.post("/cache/invalidations/normalize")
def normalize_distributed_cache_invalidation():
    payload = _json_object()
    if payload is None:
        return jsonify({"error": "invalidation must be a JSON object"}), 400
    try:
        result = normalize_invalidation_event(
            payload,
            occurred_at=payload.get("occurred_at"),
            sequence=payload.get("sequence", 1),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


@v42_bp.get("/operations/snapshot")
def operations_snapshot():
    try:
        cache = build_distributed_cache()
        transport = build_transport()
        store = build_execution_store()
        components = [
            component_health(cache, "distributed_cache"),
            component_health(transport, "job_transport"),
            component_health(store, "execution_store"),
        ]
        queue = transport.operations_snapshot()
    except Exception as exc:  # noqa: BLE001
        return jsonify({"version": "4.2.3", "healthy": False, "error": str(exc)}), 503
    return jsonify(
        {
            "version": "4.2.3",
            "healthy": all(item["healthy"] for item in components),
            "components": components,
            "queue": queue,
        }
    )


@v42_bp.get("/operations/dead-letters")
def dead_letters():
    try:
        limit = int(request.args.get("limit", 50))
        transport = build_transport()
        records = transport.list_dead_letters(limit=limit)
    except (TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"version": "4.2.3", "error": str(exc)}), 503
    return jsonify(
        {
            "version": "4.2.3",
            "count": len(records),
            "dead_letters": records,
        }
    )


@v42_bp.post("/jobs/normalize")
def normalize_job_contract():
    payload = _json_object()
    if payload is None:
        return jsonify({"error": "job must be a JSON object"}), 400
    try:
        return jsonify(normalize_job(payload, now=payload.get("submitted_at")))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@v42_bp.post("/jobs/transitions/validate")
def validate_job_transition():
    payload = _json_object()
    if payload is None or not isinstance(payload.get("job"), dict):
        return jsonify({"error": "job must be a JSON object"}), 400
    try:
        result = transition_job(
            payload["job"],
            str(payload.get("target_status", "")),
            now=payload.get("occurred_at"),
            worker_id=payload.get("worker_id"),
            result=payload.get("result"),
            error=payload.get("error"),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


@v42_bp.post("/jobs/events/normalize")
def normalize_job_event():
    payload = _json_object()
    if payload is None or not isinstance(payload.get("job"), dict):
        return jsonify({"error": "job must be a JSON object"}), 400
    try:
        result = job_event(
            payload["job"],
            str(payload.get("event_type", "")),
            payload.get("sequence"),
            occurred_at=payload.get("occurred_at"),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)
