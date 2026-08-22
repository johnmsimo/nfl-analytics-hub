import json

import nfl_data


def test_schedule_refresh_uses_stale_snapshot_when_provider_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(nfl_data, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(nfl_data, "SEED_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(nfl_data, "_mem", {})
    cached_games = [
        {
            "game_id": "cached-1",
            "completed": True,
            "season_type": "REG",
            "week": 1,
        }
    ]
    (tmp_path / "schedule_2026.json").write_text(
        json.dumps({"fetched_at": 0, "games": cached_games}),
        encoding="utf-8",
    )

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("ESPN HTTP 403")

    monkeypatch.setattr(nfl_data, "fetch_week_scoreboard", unavailable)

    assert nfl_data.get_schedule(2026, refresh=True) == cached_games


def test_schedule_provider_failure_without_snapshot_is_degraded(tmp_path, monkeypatch):
    monkeypatch.setattr(nfl_data, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(nfl_data, "SEED_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(nfl_data, "_mem", {})

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("ESPN HTTP 403")

    monkeypatch.setattr(nfl_data, "fetch_week_scoreboard", unavailable)

    assert nfl_data.get_schedule(2026, refresh=True) == []
    assert nfl_data.current_week(2026) == {
        "season": 2026,
        "week": 1,
        "season_type": "REG",
    }
