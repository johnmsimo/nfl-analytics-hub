"""ESPN league feeds: endpoints must 200 with a stable shape even when the
upstream fetch fails (they degrade to empty lists, never 5xx)."""
from __future__ import annotations


def test_injuries_endpoint_shape(client):
    d = client.get("/api/injuries").get_json()
    assert set(d) >= {"injuries", "count"}
    assert isinstance(d["injuries"], list)
    for row in d["injuries"][:5]:
        assert set(row) >= {"team", "player", "status"}


def test_injuries_team_filter(client):
    d = client.get("/api/injuries?team=phi").get_json()
    assert d["team"] == "PHI"
    assert all(r["team"] == "PHI" for r in d["injuries"])


def test_news_endpoint_shape(client):
    d = client.get("/api/news?limit=5").get_json()
    assert isinstance(d["articles"], list) and len(d["articles"]) <= 5
    for a in d["articles"]:
        assert "headline" in a and "published" in a
