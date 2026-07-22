"""Small JSON cache abstraction with Redis and in-memory backends."""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

try:
    import redis
except ImportError:  # pragma: no cover - dependency is installed in production
    redis = None


class CacheBackend:
    def __init__(self) -> None:
        self._memory: dict[str, tuple[float | None, str]] = {}
        self._lock = threading.RLock()
        self._redis = None
        url = os.getenv("REDIS_URL")
        if url and redis is not None:
            try:
                client = redis.Redis.from_url(
                    url,
                    decode_responses=True,
                    socket_connect_timeout=1,
                    socket_timeout=1,
                    health_check_interval=30,
                )
                client.ping()
                self._redis = client
            except Exception:
                self._redis = None

    @property
    def backend_name(self) -> str:
        return "redis" if self._redis is not None else "memory"

    def get(self, key: str, default: Any = None) -> Any:
        raw = None
        if self._redis is not None:
            try:
                raw = self._redis.get(key)
            except Exception:
                raw = None
        if raw is None:
            with self._lock:
                item = self._memory.get(key)
                if item is None:
                    return default
                expires_at, raw = item
                if expires_at is not None and expires_at <= time.time():
                    self._memory.pop(key, None)
                    return default
        try:
            return json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return default

    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        raw = json.dumps(value, separators=(",", ":"), default=str)
        if self._redis is not None:
            try:
                self._redis.set(key, raw, ex=ttl_seconds)
                return
            except Exception:
                pass
        expires_at = time.time() + ttl_seconds if ttl_seconds else None
        with self._lock:
            self._memory[key] = (expires_at, raw)

    def delete(self, key: str) -> None:
        if self._redis is not None:
            try:
                self._redis.delete(key)
            except Exception:
                pass
        with self._lock:
            self._memory.pop(key, None)


cache = CacheBackend()
