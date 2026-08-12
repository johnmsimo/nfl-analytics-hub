"""Operational health inspection for the v4.5.4 delivery boundary.

The endpoint is intentionally read-only: it reports queue and worker signals
without exposing payloads, destinations, credentials, or tenant records.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

VERSION = "4.5.4"
DEFAULT_HEARTBEAT_TTL_SECONDS = 90
DEFAULT_ELEVATED_BACKLOG = 100
DEFAULT_CRITICAL_BACKLOG = 1000


def _text(value: Any) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _pending_count(value: Any) -> int:
    if isinstance(value, dict):
        value = value.get("pending", 0)
    elif isinstance(value, (list, tuple)):
        value = value[0] if value else 0
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _threshold(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _heartbeat_rows(client: Any, prefix: str, now: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        keys = client.scan_iter(match=f"{prefix}:heartbeat:*")
    except AttributeError:
        return rows
    for key in keys:
        try:
            raw = client.get(key)
            item = json.loads(_text(raw)) if raw else {}
        except (TypeError, ValueError):
            continue
        if not isinstance(item, dict):
            continue
        try:
            updated_at = float(item.get("updated_at") or 0)
            ttl_seconds = int(item.get("ttl_seconds") or DEFAULT_HEARTBEAT_TTL_SECONDS)
        except (TypeError, ValueError):
            continue
        item["active"] = updated_at >= now - ttl_seconds
        item.pop("signing_secret", None)
        rows.append(item)
    return sorted(rows, key=lambda item: str(item.get("consumer", "")))


def _pressure(backlog: int) -> tuple[str, str]:
    elevated = _threshold("V45_DELIVERY_ELEVATED_BACKLOG", DEFAULT_ELEVATED_BACKLOG)
    critical = _threshold("V45_DELIVERY_CRITICAL_BACKLOG", DEFAULT_CRITICAL_BACKLOG)
    if backlog >= critical:
        return "critical", "scale_delivery_workers_or_pause_new_intake"
    if backlog >= elevated:
        return "elevated", "investigate_worker_capacity"
    return "normal", "none"


def delivery_health(backend: Any, *, now: float | None = None) -> dict[str, Any]:
    """Return bounded delivery infrastructure signals for the API contract."""
    timestamp = time.time() if now is None else float(now)
    client = getattr(backend, "client", None)
    prefix = getattr(backend, "key_prefix", "nfl:v450:delivery")
    result: dict[str, Any] = {
        "version": VERSION,
        "status": "ok",
        "backend": getattr(backend, "backend", "unknown"),
        "distributed": bool(getattr(backend, "distributed", False)),
        "production_ready": bool(getattr(backend, "distributed", False)),
        "generated_at": round(timestamp, 6),
        "queue": {
            "stream_length": None,
            "pending_count": None,
            "retry_backlog": None,
            "total_backlog": None,
            "pressure": None,
            "recommendation": None,
            "consumer_group": None,
        },
        "workers": {
            "observed_count": 0,
            "active_count": 0,
            "consumers": [],
        },
        "redis": {"probe_latency_ms": None},
    }
    if client is None:
        result["status"] = "degraded"
        result["reason"] = "in_memory_backend"
        return result
    try:
        probe_started = time.perf_counter()
        client.ping()
        result["redis"]["probe_latency_ms"] = round((time.perf_counter() - probe_started) * 1000, 3)
        stream = f"{prefix}:stream"
        group = os.environ.get("V45_DELIVERY_CONSUMER_GROUP", "v451-dispatch")
        pending_count = _pending_count(client.xpending(stream, group))
        retry_backlog = int(client.zcard(f"{prefix}:retry"))
        total_backlog = pending_count + retry_backlog
        pressure, recommendation = _pressure(total_backlog)
        result["queue"].update(
            {
                "stream_length": int(client.xlen(stream)),
                "pending_count": pending_count,
                "retry_backlog": retry_backlog,
                "total_backlog": total_backlog,
                "pressure": pressure,
                "recommendation": recommendation,
                "consumer_group": group,
            }
        )
        workers = _heartbeat_rows(client, prefix, timestamp)
        result["workers"] = {
            "observed_count": len(workers),
            "active_count": sum(1 for item in workers if item.get("active")),
            "consumers": workers[:20],
        }
        if pressure == "critical":
            result["status"] = "degraded"
            result["reason"] = "delivery_backlog_critical"
        elif pressure == "elevated":
            result["status"] = "degraded"
            result["reason"] = "delivery_backlog_elevated"
        elif result["workers"]["observed_count"] == 0:
            result["status"] = "degraded"
            result["reason"] = "worker_heartbeat_not_observed"
        elif result["workers"]["active_count"] == 0:
            result["status"] = "degraded"
            result["reason"] = "worker_heartbeat_stale"
    except Exception as exc:  # noqa: BLE001
        result["status"] = "degraded"
        result["production_ready"] = False
        result["reason"] = "redis_unavailable"
        result["error_type"] = type(exc).__name__
    return result
