from routes.v32_api import build_event, heartbeat_stream, normalize_dashboard_preferences


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
