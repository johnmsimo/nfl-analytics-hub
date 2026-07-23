"""NFL Analytics Hub v3.2 completion endpoints."""
from __future__ import annotations

import time
from typing import Any

from flask import Blueprint, jsonify, request, session

from realtime_v32 import normalize_saved_filter
from routes.v4_api import v4_bp
from routes.v32_api import normalize_dashboard_preferences
from routes.v41_api import v41_bp
from routes.v42_api import v42_bp
from routes.v43_api import v43_bp
from v32_release import (
    METRICS,
    PROFILE_STORE,
    backtest_report,
    calibration_report,
    drift_report,
    generated_report,
)

v32_release_bp = Blueprint("v32_release_api", __name__, url_prefix="/api/v3.2")


@v32_release_bp.record_once
def _register_later_version_blueprints(state) -> None:
    """Attach later version APIs while preserving the thin app entrypoint."""
    state.app.register_blueprint(v4_bp)
    state.app.register_blueprint(v41_bp)
    state.app.register_blueprint(v42_bp)
    state.app.register_blueprint(v43_bp)


def _json_object() -> dict[str, Any] | None:
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else None


def _subject() -> str | None:
    user = session.get("user")
    if isinstance(user, dict):
        value = user.get("username") or user.get("id") or user.get("email")
    else:
        value = user
    subject = str(value or "").strip()
    return subject or None


def _profile_payload(payload: dict[str, Any]) -> dict[str, Any]:
    filters = payload.get("saved_filters", [])
    if not isinstance(filters, list):
        filters = []
    return {
        "preferences": normalize_dashboard_preferences(payload.get("preferences")),
        "saved_filters": [normalize_saved_filter(item) for item in filters if isinstance(item, dict)][:50],
        "layout": payload.get("layout", {}) if isinstance(payload.get("layout", {}), dict) else {},
        "watchlist": payload.get("watchlist", [])[:100] if isinstance(payload.get("watchlist", []), list) else [],
    }


@v32_release_bp.get("/profile")
def get_profile():
    subject = _subject()
    if not subject:
        return jsonify({"error": "authentication required"}), 401
    return jsonify(PROFILE_STORE.get(subject))


@v32_release_bp.put("/profile")
def put_profile():
    subject = _subject()
    if not subject:
        return jsonify({"error": "authentication required"}), 401
    payload = _json_object()
    if payload is None:
        return jsonify({"error": "request body must be a JSON object"}), 400
    try:
        stored = PROFILE_STORE.put(subject, _profile_payload(payload))
    except (OSError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(stored)


@v32_release_bp.post("/models/calibration")
def model_calibration():
    started = time.perf_counter()
    payload = _json_object()
    if payload is None or not isinstance(payload.get("predictions", []), list):
        return jsonify({"error": "predictions must be a list"}), 400
    try:
        result = calibration_report(payload["predictions"], int(payload.get("bins", 10)))
    except (TypeError, ValueError):
        return jsonify({"error": "bins must be an integer"}), 400
    METRICS.observe("model_calibration", (time.perf_counter() - started) * 1000)
    return jsonify(result)


@v32_release_bp.post("/models/backtest")
def model_backtest():
    started = time.perf_counter()
    payload = _json_object()
    if payload is None or not isinstance(payload.get("records", []), list):
        return jsonify({"error": "records must be a list"}), 400
    result = backtest_report(payload["records"])
    METRICS.observe("model_backtest", (time.perf_counter() - started) * 1000)
    return jsonify(result)


@v32_release_bp.post("/models/drift")
def model_drift():
    started = time.perf_counter()
    payload = _json_object()
    if payload is None:
        return jsonify({"error": "request body must be a JSON object"}), 400
    reference = payload.get("reference", [])
    current = payload.get("current", [])
    if not isinstance(reference, list) or not isinstance(current, list):
        return jsonify({"error": "reference and current must be lists"}), 400
    result = drift_report(reference, current)
    METRICS.observe("model_drift", (time.perf_counter() - started) * 1000)
    return jsonify(result)


@v32_release_bp.post("/providers/freshness")
def provider_freshness():
    payload = _json_object()
    if payload is None or not str(payload.get("provider", "")).strip():
        return jsonify({"error": "provider is required"}), 400
    METRICS.mark_fresh(str(payload["provider"]), payload.get("observed_at"))
    return jsonify({"ok": True})


@v32_release_bp.get("/observability")
def observability():
    return jsonify({"version": "3.2", **METRICS.snapshot()})


@v32_release_bp.post("/reports/generate")
def reports_generate():
    started = time.perf_counter()
    payload = _json_object()
    if payload is None or not isinstance(payload.get("context", {}), dict):
        return jsonify({"error": "context must be a JSON object"}), 400
    try:
        result = generated_report(payload.get("kind", "preview"), payload["context"])
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    METRICS.observe("report_generation", (time.perf_counter() - started) * 1000)
    return jsonify(result)
