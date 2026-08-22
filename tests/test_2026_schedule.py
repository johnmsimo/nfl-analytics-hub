import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import nfl_data


SCHEDULE_PATH = Path(__file__).resolve().parents[1] / "data" / "schedule_2026.json"


def _scoreboard_event():
    return {
        "id": "hof-1",
        "date": "2026-08-07T00:00:00Z",
        "name": "Away at Home",
        "shortName": "AWY @ HOM",
        "competitions": [
            {
                "venue": {"fullName": "Hall of Fame Stadium"},
                "competitors": [
                    {
                        "homeAway": "home",
                        "team": {
                            "abbreviation": "HOM",
                            "displayName": "Home Team",
                            "id": "1",
                        },
                        "score": "0",
                        "records": [{"type": "total", "summary": "0-0"}],
                    },
                    {
                        "homeAway": "away",
                        "team": {
                            "abbreviation": "AWY",
                            "displayName": "Away Team",
                            "id": "2",
                        },
                        "score": "0",
                        "records": [{"type": "total", "summary": "0-0"}],
                    },
                ],
                "status": {
                    "type": {
                        "state": "pre",
                        "completed": False,
                        "shortDetail": "8:00 PM",
                    }
                },
            }
        ],
    }


def test_schedule_file_contains_complete_2026_calendar():
    payload = json.loads(SCHEDULE_PATH.read_text(encoding="utf-8"))
    games = payload["games"]

    assert len(games) == 334
    assert Counter(game["season_type"] for game in games) == {
        "PRE": 49,
        "REG": 272,
        "POST": 13,
    }
    assert Counter(
        game["week"] for game in games if game["season_type"] == "PRE"
    ) == {0: 1, 1: 16, 2: 16, 3: 16}
    assert len({str(game["game_id"]) for game in games}) == len(games)
    assert games == sorted(
        games,
        key=lambda game: (game.get("date") or "", str(game.get("game_id") or "")),
    )


def test_espn_preseason_week_one_is_hall_of_fame_week(monkeypatch):
    seen = {}

    def fake_get_json(url, **_kwargs):
        seen["url"] = url
        return {"events": [_scoreboard_event()]}

    monkeypatch.setattr(nfl_data, "_mem", {})
    monkeypatch.setattr(nfl_data, "_get_json", fake_get_json)

    games = nfl_data.fetch_week_scoreboard(2026, 1, seasontype=1)

    assert "seasontype=1&week=1" in seen["url"]
    assert games[0]["season_type"] == "PRE"
    assert games[0]["week"] == 0


def test_live_preseason_week_maps_back_to_espn_source_week(monkeypatch):
    calls = []

    def fake_fetch(season, week, seasontype=2, ttl=60):
        calls.append((season, week, seasontype))
        return [{"game_id": "live"}]

    monkeypatch.setattr(nfl_data, "fetch_week_scoreboard", fake_fetch)

    assert nfl_data.get_week_games(2026, 2, "PRE", live=True) == [
        {"game_id": "live"}
    ]
    assert calls == [(2026, 3, 1)]


def test_current_week_selects_active_preseason_week(monkeypatch):
    games = [
        {
            "game_id": "hof",
            "week": 0,
            "season_type": "PRE",
            "completed": True,
        },
        {
            "game_id": "pre-1",
            "week": 1,
            "season_type": "PRE",
            "completed": True,
        },
        {
            "game_id": "pre-2",
            "week": 2,
            "season_type": "PRE",
            "completed": False,
        },
    ]
    monkeypatch.setattr(nfl_data, "get_schedule", lambda _season: games)

    assert nfl_data.current_week(2026) == {
        "season": 2026,
        "week": 2,
        "season_type": "PRE",
    }


def test_runtime_schedule_merges_with_seed_and_overrides_status(tmp_path, monkeypatch):
    runtime_dir = tmp_path / "runtime"
    seed_dir = tmp_path / "seed"
    runtime_dir.mkdir()
    seed_dir.mkdir()

    seed_games = [
        {
            "game_id": "pre-1",
            "date": "2026-08-07T00:00:00Z",
            "season_type": "PRE",
            "completed": True,
        },
        {
            "game_id": "reg-1",
            "date": "2026-09-10T00:20:00Z",
            "season_type": "REG",
            "completed": False,
        },
    ]
    runtime_games = [
        {
            "game_id": "reg-1",
            "date": "2026-09-10T00:20:00Z",
            "season_type": "REG",
            "completed": True,
        }
    ]
    (seed_dir / "schedule_2026.json").write_text(
        json.dumps({"fetched_at": 1, "games": seed_games}),
        encoding="utf-8",
    )
    (runtime_dir / "schedule_2026.json").write_text(
        json.dumps({"fetched_at": 2, "games": runtime_games}),
        encoding="utf-8",
    )

    monkeypatch.setattr(nfl_data, "DATA_DIR", str(runtime_dir))
    monkeypatch.setattr(nfl_data, "SEED_DATA_DIR", str(seed_dir))

    merged = nfl_data._read_json("schedule_2026.json")

    assert merged["fetched_at"] == 2
    assert [game["game_id"] for game in merged["games"]] == ["pre-1", "reg-1"]
    assert merged["games"][1]["completed"] is True


def test_schedule_readiness_reports_active_2026_preseason():
    status = nfl_data.schedule_status(
        2026,
        now=datetime(2026, 8, 22, tzinfo=timezone.utc),
    )

    assert status["ready"] is True
    assert status["total_games"] == 334
    assert status["counts"] == {"PRE": 49, "REG": 272, "POST": 13}
    assert status["current_week"] == {
        "season": 2026,
        "week": 2,
        "season_type": "PRE",
    }


def test_ready_endpoint_includes_schedule_contract(client):
    response = client.get("/ready")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["schedule"]["ready"] is True
    assert payload["schedule"]["total_games"] == 334
