"""StatMuse-style Q&A: parser + executor answered from our own warehouse."""
from __future__ import annotations


def _ask(client, q):
    r = client.get("/api/ask", query_string={"q": q, "season": 2025})
    assert r.status_code == 200
    return r.get_json()


def test_player_stat_question(client):
    d = _ask(client, "CeeDee Lamb receiving yards")
    assert d["ok"] and d["kind"] == "player_stat"
    assert d["summary"]["games"] == 13
    assert d["summary"]["total"] == 1077
    assert d["player"]["id"] == "4241389"
    assert d["table"]["rows"]


def test_scoped_and_leader_questions(client):
    d = _ask(client, "Saquon Barkley rushing yards last 4 games")
    assert d["ok"] and d["summary"]["games"] == 4

    d = _ask(client, "who leads the NFL in receiving TDs")
    assert d["ok"] and d["kind"] == "leaders"
    assert d["table"]["rows"][0][0] == 1

    d = _ask(client, "most receptions by a TE")
    assert d["ok"] and all(r[3] == "TE" for r in d["table"]["rows"])


def test_team_and_game_questions(client):
    d = _ask(client, "Eagles record")
    assert d["ok"] and d["kind"] == "team" and d["summary"]["record"] == "11-6"

    d = _ask(client, "Cowboys week 1 score")
    assert d["ok"] and d["kind"] == "game" and "24" in d["headline"]


def test_unparseable_returns_examples(client):
    d = _ask(client, "what is the meaning of football")
    assert not d["ok"] and d["examples"]
