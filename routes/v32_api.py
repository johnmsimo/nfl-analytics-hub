"""NFL Analytics Hub v3.2 real-time and personalization API."""
from __future__ import annotations

import json
import time
from collections.abc import Iterator, Mapping
from typing import Any

from flask import Blueprint, Response, jsonify, request, stream_with_context

v32_bp = Blueprint("v32_api", __name__, url_prefix="/api/v3.2")

_ALLOWED_MODULES = (
    "live_games",
    "game_intelligence",
    "player_intelligence",
    "team_intelligence",
    "betting_intelligence",
    "assistant",
    "watchlist",
)
_ALLOWED_DENSITIES = {"comfortable", "compact"}
_ALLOWED_REFRESH_SECONDS = {5, 10, 15, 30, 60}


def normalize_dashboard_preferences(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a stable, validated dashboard preference profile."""
    data = dict(payload or {})
    requested_modules = data.get("modules", _ALLOWED_MODULES)
    if not isinstance(requested_modules, (list, tuple)):
        requested_modules = _ALLOWED_MODULES

    modules: list[str] = []
    for item in requested_modules:
        name = str(item).strip()
        if name in _ALLOWED_MODULES and name not in modules:
            modules.append(name)
    if not modules:
        modules = list(_ALLOWED_MODULES)

    density = str(data.get("density", "comfortable")).lower()
    if density not in _ALLOWED_DENSITIES:
        density = "comfortable"

    try:
        refresh_seconds = int(data.get("refresh_seconds", 15))
    except (TypeError, ValueError):
        refresh_seconds = 15
    if refresh_seconds not in _ALLOWED_REFRESH_SECONDS:
        refresh_seconds = 15

    return {
        "modules": modules,
        "density": density,
        "refresh_seconds": refresh_seconds,
        "show_confidence": bool(data.get("show_confidence", True)),
        "show_market_context": bool(data.get("show_market_context", True)),
        "version": "3.2",
    }


def build_event(event: str, data: Mapping[str, Any], event_id: str | None = None) -> str:
    """Serialize one standards-compliant Server-Sent Event frame."""
    lines = []
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(dict(data), separators=(',', ':'), sort_keys=True)}")
    return "\n".join(lines) + "\n\n"


def heartbeat_stream(interval_seconds: int = 15, max_events: int | None = None) -> Iterator[str]:
    """Yield connection and heartbeat events; max_events supports deterministic tests."""
    yield build_event(
        "connected",
        {"service": "nfl-analytics-hub", "version": "3.2", "transport": "sse"},
        "0",
    )
    emitted = 0
    while max_events is None or emitted < max_events:
        if interval_seconds:
            time.sleep(interval_seconds)
        emitted += 1
        yield build_event(
            "heartbeat",
            {"sequence": emitted, "timestamp": int(time.time())},
            str(emitted),
        )


@v32_bp.get("/capabilities")
def capabilities():
    return jsonify(
        {
            "version": "3.2",
            "status": "foundation",
            "features": {
                "realtime_transport": "server-sent-events",
                "personalized_dashboard": True,
                "saved_layout_contract": True,
                "mobile_first_contract": True,
            },
            "endpoints": {
                "events": "/api/v3.2/events",
                "preferences": "/api/v3.2/preferences/normalize",
            },
        }
    )


@v32_bp.get("/events")
def events():
    try:
        interval = int(request.args.get("interval", 15))
    except ValueError:
        interval = 15
    interval = min(max(interval, 5), 60)
    return Response(
        stream_with_context(heartbeat_stream(interval_seconds=interval)),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@v32_bp.post("/preferences/normalize")
def preferences():
    payload = request.get_json(silent=True)
    if payload is not None and not isinstance(payload, dict):
        return jsonify({"error": "request body must be a JSON object"}), 400
    return jsonify(normalize_dashboard_preferences(payload))
