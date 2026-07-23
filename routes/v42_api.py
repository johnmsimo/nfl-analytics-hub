"""NFL Analytics Hub v4.2 distributed intelligence endpoints."""
from __future__ import annotations

from typing import Any

from flask import Blueprint, jsonify, request

from distributed_v42 import job_event, normalize_job, platform_manifest, transition_job

v42_bp = Blueprint("v42_api", __name__, url_prefix="/api/v4.2")


def _json_object() -> dict[str, Any] | None:
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else None


@v42_bp.get("/capabilities")
def capabilities():
    return jsonify(
        {
            **platform_manifest(),
            "endpoints": {
                "capabilities": "/api/v4.2/capabilities",
                "job_normalize": "/api/v4.2/jobs/normalize",
                "transition_validate": "/api/v4.2/jobs/transitions/validate",
                "event_normalize": "/api/v4.2/jobs/events/normalize",
            },
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
