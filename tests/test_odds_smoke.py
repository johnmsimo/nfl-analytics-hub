"""P1.2 Odds API provider-key, runtime-gate, and smoke-contract tests."""
from __future__ import annotations

import json

import pytest

import odds_api
from scripts import smoke_odds_api


EVENT_ID = "0123456789abcdef0123456789abcdef"


def _runtime_on(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "test-key-not-real")
    monkeypatch.setenv("ENABLED_PROVIDERS", "nflverse,nws,the-odds-api")
    monkeypatch.setenv("ENABLE_ODDS_API", "true")


@pytest.mark.parametrize(
    ("providers", "feature", "key"),
    [
        ("nflverse,nws,the-odds-api", "true", None),
        ("nflverse,nws,odds", "true", "test-key-not-real"),
        ("nflverse,nws,the-odds-api", "false", "test-key-not-real"),
    ],
)
def test_runtime_requires_key_canonical_provider_and_feature_gate(
    monkeypatch, providers, feature, key
):
    calls = []
    if key is None:
        monkeypatch.delenv("ODDS_API_KEY", raising=False)
    else:
        monkeypatch.setenv("ODDS_API_KEY", key)
    monkeypatch.setenv("ENABLED_PROVIDERS", providers)
    monkeypatch.setenv("ENABLE_ODDS_API", feature)
    monkeypatch.setattr(odds_api.http_client, "get", lambda *a, **k: calls.append(1))

    assert odds_api.is_configured() is False
    assert odds_api.get_game_odds() == []
    assert calls == []


def test_status_exposes_gates_without_secret(monkeypatch):
    _runtime_on(monkeypatch)
    monkeypatch.setattr(odds_api, "_snapshot", {})

    status = odds_api.snapshot_status()

    assert status["provider_key"] == "the-odds-api"
    assert status["key_configured"] is True
    assert status["provider_enabled"] is True
    assert status["feature_enabled"] is True
    assert status["configured"] is True
    assert "test-key-not-real" not in json.dumps(status)


def test_commercial_odds_sync_obeys_runtime_gate(monkeypatch):
    import commercial_integrations

    calls = []
    monkeypatch.setenv("ODDS_API_KEY", "test-key-not-real")
    monkeypatch.setenv("ENABLED_PROVIDERS", "nflverse,nws,the-odds-api")
    monkeypatch.setenv("ENABLE_ODDS_API", "false")
    monkeypatch.setattr(
        commercial_integrations,
        "_json_get",
        lambda *a, **k: calls.append(1),
    )

    with pytest.raises(RuntimeError, match="runtime is disabled"):
        commercial_integrations.sync_odds(2026, 3)
    assert calls == []


def test_smoke_makes_exactly_one_sanitized_request(monkeypatch):
    _runtime_on(monkeypatch)
    calls = []

    class Response:
        status_code = 200
        headers = {
            "x-requests-last": "1",
            "x-requests-used": "41",
            "x-requests-remaining": "459",
        }

        @staticmethod
        def json():
            return {
                "id": EVENT_ID,
                "sport_key": "americanfootball_nfl",
                "bookmakers": [
                    {
                        "key": "draftkings",
                        "markets": [{"key": "h2h", "outcomes": []}],
                    }
                ],
            }

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    result = smoke_odds_api.run_smoke(
        EVENT_ID,
        1,
        get=fake_get,
    )

    assert len(calls) == 1
    _, kwargs = calls[0]
    assert kwargs["allow_redirects"] is False
    assert kwargs["params"]["regions"] == "us"
    assert kwargs["params"]["markets"] == "h2h"
    assert result["requests_last"] == 1
    assert result["bookmakers"] == 1
    assert "test-key-not-real" not in json.dumps(result)


@pytest.mark.parametrize(
    ("event_id", "confirm", "providers"),
    [
        ("bad-event-id", 1, "nflverse,nws,the-odds-api"),
        (EVENT_ID, 0, "nflverse,nws,the-odds-api"),
        (EVENT_ID, 1, "nflverse,nws,odds"),
    ],
)
def test_smoke_rejects_unsafe_preflight_without_request(
    monkeypatch, event_id, confirm, providers
):
    calls = []
    monkeypatch.setenv("ODDS_API_KEY", "test-key-not-real")
    monkeypatch.setenv("ENABLED_PROVIDERS", providers)

    with pytest.raises(smoke_odds_api.SmokeError):
        smoke_odds_api.run_smoke(
            event_id,
            confirm,
            get=lambda *a, **k: calls.append(1),
        )
    assert calls == []


def test_smoke_stops_when_provider_reports_more_than_one_credit(monkeypatch):
    _runtime_on(monkeypatch)
    calls = []

    class Response:
        status_code = 200
        headers = {
            "x-requests-last": "2",
            "x-requests-used": "42",
            "x-requests-remaining": "458",
        }

        @staticmethod
        def json():
            return {
                "id": EVENT_ID,
                "sport_key": "americanfootball_nfl",
                "bookmakers": [
                    {"key": "book", "markets": [{"key": "h2h", "outcomes": []}]}
                ],
            }

    def fake_get(*args, **kwargs):
        calls.append(1)
        return Response()

    with pytest.raises(smoke_odds_api.SmokeError, match="credit_cap_exceeded"):
        smoke_odds_api.run_smoke(EVENT_ID, 1, get=fake_get)
    assert len(calls) == 1
