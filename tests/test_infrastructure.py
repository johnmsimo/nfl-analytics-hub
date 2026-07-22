from __future__ import annotations

import json
import time
from unittest.mock import Mock

import pytest
import requests

import cache_backend
import http_client
import provider_health
from security import RateLimiter


def test_memory_cache_round_trip_and_expiry(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    backend = cache_backend.CacheBackend()
    backend.set("unit:key", {"value": 13}, ttl_seconds=1)
    assert backend.backend_name == "memory"
    assert backend.get("unit:key") == {"value": 13}
    backend._memory["unit:key"] = (time.time() - 1, json.dumps({"value": 13}))
    assert backend.get("unit:key") is None


def test_rate_limiter_memory_fallback(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    limiter = RateLimiter()
    assert limiter.backend_name == "memory"
    assert limiter._allowed("bucket", 1, 60)[0] is True
    allowed, retry_after = limiter._allowed("bucket", 1, 60)
    assert allowed is False
    assert retry_after >= 1


def test_http_client_records_success(monkeypatch):
    provider_health.reset()
    response = Mock(status_code=200)
    client = Mock()
    client.request.return_value = response
    monkeypatch.setattr(http_client, "session", lambda: client)

    assert http_client.get("https://example.test/data") is response
    row = provider_health.snapshot()["example.test"]
    assert row["status"] == "healthy"
    assert row["successes"] == 1


def test_http_client_records_transport_failure(monkeypatch):
    provider_health.reset()
    client = Mock()
    client.request.side_effect = requests.ConnectionError("offline")
    monkeypatch.setattr(http_client, "session", lambda: client)

    with pytest.raises(requests.ConnectionError):
        http_client.get("https://provider.test/data")
    row = provider_health.snapshot()["provider.test"]
    assert row["status"] == "degraded"
    assert row["failures"] == 1


def test_request_id_is_returned(client):
    response = client.get("/health", headers={"X-Request-ID": "test-request-13"})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "test-request-13"
