"""Thread-safe provider health and failure telemetry."""
from __future__ import annotations

import threading
import time
from copy import deepcopy

_lock = threading.RLock()
_state: dict[str, dict] = {}


def record_success(provider: str, latency_ms: float | None = None) -> None:
    now = time.time()
    with _lock:
        row = _state.setdefault(provider, {"successes": 0, "failures": 0})
        row.update({
            "status": "healthy",
            "last_success_at": now,
            "last_error": None,
            "successes": row.get("successes", 0) + 1,
        })
        if latency_ms is not None:
            row["last_latency_ms"] = round(latency_ms, 1)


def record_failure(provider: str, error: Exception | str, latency_ms: float | None = None) -> None:
    now = time.time()
    with _lock:
        row = _state.setdefault(provider, {"successes": 0, "failures": 0})
        row.update({
            "status": "degraded",
            "last_failure_at": now,
            "last_error": str(error)[:300],
            "failures": row.get("failures", 0) + 1,
        })
        if latency_ms is not None:
            row["last_latency_ms"] = round(latency_ms, 1)


def snapshot() -> dict[str, dict]:
    with _lock:
        return deepcopy(_state)


def reset() -> None:
    with _lock:
        _state.clear()
