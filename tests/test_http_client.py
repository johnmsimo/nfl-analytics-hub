from __future__ import annotations

import http_client
import odds_api


def test_shared_session_configures_retries():
    client = http_client.session()
    adapter = client.get_adapter("https://")

    assert adapter.max_retries.total == 3
    assert 429 in adapter.max_retries.status_forcelist
    assert "GET" in adapter.max_retries.allowed_methods


def test_odds_api_uses_shared_http_client(monkeypatch):
    class Response:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"ok": True}

    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return Response()

    monkeypatch.setenv("ODDS_API_KEY", "test-key")
    monkeypatch.setattr(http_client, "get", fake_get)

    result = odds_api._get("/test", regions="us")

    assert result == {"ok": True}
    assert captured["url"].endswith("/test")
    assert captured["kwargs"]["params"]["apiKey"] == "test-key"
