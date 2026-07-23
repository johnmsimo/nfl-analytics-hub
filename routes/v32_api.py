"""NFL Analytics Hub v3.2 real-time, personalization, and discovery API."""
from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator, Mapping
from typing import Any

from flask import Blueprint, Response, jsonify, request, stream_with_context

from realtime_v32 import BROKER, normalize_saved_filter, normalize_topics, search_entities

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


def broker_stream(
    topics: set[str],
    last_event_id: str | None = None,
    heartbeat_seconds: int = 15,
    max_cycles: int | None = None,
) -> Iterator[str]:
    """Yield replayable broker events and periodic heartbeats."""
    yield build_event(
        "connected",
        {
            "service": "nfl-analytics-hub",
            "version": "3.2",
            "transport": "sse",
            "topics": sorted(topics),
        },
        "0",
    )
    cursor = last_event_id
    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        events = BROKER.events_after(cursor, topics)
        if events:
            for item in events:
                cursor = item.event_id
                payload = dict(item.data)
                payload.update({"topic": item.topic, "created_at": item.created_at})
                yield build_event(item.event, payload, item.event_id)
        else:
            yield build_event("heartbeat", {"timestamp": int(time.time())})
        cycles += 1
        if max_cycles is None or cycles < max_cycles:
            BROKER.wait(heartbeat_seconds)


def _publish_authorized() -> bool:
    configured = os.environ.get("V32_PUBLISH_TOKEN", "")
    supplied = request.headers.get("X-V32-Publish-Token", "")
    return bool(configured) and supplied == configured


@v32_bp.get("/capabilities")
def capabilities():
    return jsonify(
        {
            "version": "3.2",
            "status": "active-development",
            "features": {
                "realtime_transport": "server-sent-events",
                "replayable_event_ids": True,
                "topic_subscriptions": True,
                "personalized_dashboard": True,
                "saved_filter_contract": True,
                "cross_entity_search": True,
                "mobile_first_contract": True,
            },
            "endpoints": {
                "events": "/api/v3.2/events",
                "publish": "/api/v3.2/events/publish",
                "preferences": "/api/v3.2/preferences/normalize",
                "saved_filter": "/api/v3.2/filters/normalize",
                "search": "/api/v3.2/search",
            },
        }
    )


@v32_bp.get("/events")
def events():
    try:
        heartbeat = int(request.args.get("heartbeat", 15))
    except ValueError:
        heartbeat = 15
    heartbeat = min(max(heartbeat, 5), 60)
    topics = normalize_topics(request.args.get("topics"))
    last_event_id = request.headers.get("Last-Event-ID") or request.args.get("last_event_id")
    return Response(
        stream_with_context(
            broker_stream(topics, last_event_id=last_event_id, heartbeat_seconds=heartbeat)
        ),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@v32_bp.post("/events/publish")
def publish_event():
    if not _publish_authorized():
        return jsonify({"error": "publishing is disabled or unauthorized"}), 403
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "request body must be a JSON object"}), 400
    data = payload.get("data", {})
    if not isinstance(data, dict):
        return jsonify({"error": "data must be a JSON object"}), 400
    try:
        item = BROKER.publish(payload.get("topic", ""), payload.get("event", ""), data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(item.as_dict()), 202


@v32_bp.post("/preferences/normalize")
def preferences():
    payload = request.get_json(silent=True)
    if payload is not None and not isinstance(payload, dict):
        return jsonify({"error": "request body must be a JSON object"}), 400
    return jsonify(normalize_dashboard_preferences(payload))


@v32_bp.post("/filters/normalize")
def saved_filter():
    payload = request.get_json(silent=True)
    if payload is not None and not isinstance(payload, dict):
        return jsonify({"error": "request body must be a JSON object"}), 400
    return jsonify(normalize_saved_filter(payload))


@v32_bp.post("/search")
def search():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "request body must be a JSON object"}), 400
    entities = payload.get("entities", [])
    if not isinstance(entities, list) or any(not isinstance(item, dict) for item in entities):
        return jsonify({"error": "entities must be a list of JSON objects"}), 400
    try:
        results = search_entities(
            payload.get("query", ""),
            entities,
            payload.get("entity_types"),
            int(payload.get("limit", 20)),
        )
    except (TypeError, ValueError):
        return jsonify({"error": "limit must be an integer"}), 400
    return jsonify({"count": len(results), "results": results, "version": "3.2"})
