from realtime_v32 import EventBroker, normalize_saved_filter, normalize_topics, search_entities
from routes.v32_api import (
    broker_stream,
    build_event,
    heartbeat_stream,
    normalize_dashboard_preferences,
)


def test_preferences_are_normalized_and_deduplicated():
    result = normalize_dashboard_preferences(
        {
            "modules": ["live_games", "live_games", "unknown", "assistant"],
            "density": "compact",
            "refresh_seconds": 10,
            "show_confidence": False,
        }
    )
    assert result["modules"] == ["live_games", "assistant"]
    assert result["density"] == "compact"
    assert result["refresh_seconds"] == 10
    assert result["show_confidence"] is False


def test_invalid_preferences_fall_back_safely():
    result = normalize_dashboard_preferences(
        {"modules": "bad", "density": "tiny", "refresh_seconds": 9}
    )
    assert "live_games" in result["modules"]
    assert result["density"] == "comfortable"
    assert result["refresh_seconds"] == 15


def test_sse_event_format():
    frame = build_event("score_update", {"game_id": "g1", "score": 7}, "42")
    assert frame.startswith("id: 42\nevent: score_update\ndata: ")
    assert frame.endswith("\n\n")


def test_heartbeat_stream_is_deterministic_for_tests():
    frames = list(heartbeat_stream(interval_seconds=0, max_events=2))
    assert len(frames) == 3
    assert "event: connected" in frames[0]
    assert "event: heartbeat" in frames[1]
    assert '"sequence":2' in frames[2]


def test_event_broker_replays_after_cursor_and_filters_topics():
    broker = EventBroker(max_events=10)
    first = broker.publish("scores", "score_update", {"home": 7})
    broker.publish("odds", "line_move", {"spread": -3.5})
    third = broker.publish("scores", "score_update", {"home": 14})
    replay = broker.events_after(first.event_id, {"scores"})
    assert [item.event_id for item in replay] == [third.event_id]


def test_topics_are_normalized_with_safe_fallback():
    assert normalize_topics("scores,odds,bad") == {"scores", "odds"}
    assert "system" in normalize_topics("bad")


def test_saved_filter_contract_is_stable():
    result = normalize_saved_filter(
        {
            "name": "  Live QBs  ",
            "query": "quarterback",
            "entity_types": ["player", "player", "unknown"],
            "live_only": True,
        }
    )
    assert result["name"] == "Live QBs"
    assert result["entity_types"] == ["player"]
    assert result["live_only"] is True
    assert result["version"] == "3.2"


def test_cross_entity_search_filters_and_ranks_matches():
    entities = [
        {"type": "team", "name": "Buffalo Bills"},
        {"type": "player", "name": "Josh Allen", "team": "Buffalo Bills"},
        {"type": "game", "title": "Buffalo Bills at Miami Dolphins"},
        {"type": "player", "name": "Lamar Jackson", "team": "Baltimore Ravens"},
    ]
    results = search_entities("Buffalo", entities, ["team", "player"], limit=10)
    assert [item["type"] for item in results] == ["team", "player"]
    assert all("Buffalo" in (item.get("name", "") + item.get("team", "")) for item in results)


def test_broker_stream_emits_connected_event_and_heartbeat(monkeypatch):
    monkeypatch.setattr("routes.v32_api.BROKER.events_after", lambda *_args: [])
    monkeypatch.setattr("routes.v32_api.BROKER.wait", lambda *_args: None)
    frames = list(broker_stream({"scores"}, heartbeat_seconds=0, max_cycles=1))
    assert len(frames) == 2
    assert "event: connected" in frames[0]
    assert '"topics":["scores"]' in frames[0]
    assert "event: heartbeat" in frames[1]
