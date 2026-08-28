from __future__ import annotations

import time

import p42_live_market_hydration as p42


def _game() -> dict:
    return {
        "game_id": "g1",
        "season": 2026,
        "season_type": "REG",
        "week": 1,
        "date": "2026-09-10T00:00:00Z",
        "home_team": "AAA",
        "away_team": "BBB",
        "home_name": "Alpha AAA",
        "away_name": "Beta BBB",
    }


def _decision() -> dict:
    return {
        "gameId": "g1",
        "season": 2026,
        "seasonType": "REG",
        "week": 1,
        "homeTeam": "AAA",
        "awayTeam": "BBB",
        "model": "p4.0-game-intelligence",
        "modelVersion": "p40-transparent-v1",
        "modelHomeMargin": 10.0,
        "homeWinProbability": 0.75,
        "awayWinProbability": 0.25,
        "confidenceScore": 84.0,
        "confidenceGrade": "A",
        "decisionGrade": "Strong Play",
        "selectedSide": "home",
        "selectedTeam": "AAA",
        "selectedProbability": 0.75,
        "evidence": {
            "home": {"basic": {"ppg": 27.0, "papg": 20.0}},
            "away": {"basic": {"ppg": 20.0, "papg": 27.0}},
        },
    }


def _event(event_id: str = "event-1") -> dict:
    now = "2099-09-09T23:59:00Z"
    return {
        "id": event_id,
        "home_team": "Alpha AAA",
        "away_team": "Beta BBB",
        "commence_time": "2099-09-10T00:00:00Z",
        "bookmakers": [
            {
                "key": "book-a",
                "title": "Book A",
                "last_update": now,
                "markets": [
                    {
                        "key": "h2h",
                        "last_update": now,
                        "outcomes": [
                            {"name": "Alpha AAA", "price": 110},
                            {"name": "Beta BBB", "price": -130},
                        ],
                    },
                    {
                        "key": "spreads",
                        "last_update": now,
                        "outcomes": [
                            {"name": "Alpha AAA", "price": -105, "point": -3.5},
                            {"name": "Beta BBB", "price": -115, "point": 3.5},
                        ],
                    },
                    {
                        "key": "totals",
                        "last_update": now,
                        "outcomes": [
                            {"name": "Over", "price": -105, "point": 40.5},
                            {"name": "Under", "price": -115, "point": 40.5},
                        ],
                    },
                ],
            },
            {
                "key": "book-b",
                "title": "Book B",
                "last_update": now,
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Alpha AAA", "price": 105},
                            {"name": "Beta BBB", "price": -125},
                        ],
                    },
                    {
                        "key": "spreads",
                        "outcomes": [
                            {"name": "Alpha AAA", "price": -110, "point": -3.5},
                            {"name": "Beta BBB", "price": -110, "point": 3.5},
                        ],
                    },
                    {
                        "key": "totals",
                        "outcomes": [
                            {"name": "Over", "price": -110, "point": 40.5},
                            {"name": "Under", "price": -110, "point": 40.5},
                        ],
                    },
                ],
            },
        ],
    }


def test_hydration_requires_explicit_provider_spend(monkeypatch):
    monkeypatch.setattr(p42.nfl_data, "get_week_games", lambda *args, **kwargs: [_game()])
    monkeypatch.setattr(p42.odds_api, "is_configured", lambda: True)
    monkeypatch.setattr(
        p42.odds_api,
        "get_game_odds",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("provider request")),
    )
    report = p42.hydrate_week(2026, 1, "REG", allow_provider_spend=False)
    assert report["state"] == "blocked"
    assert report["providerRequests"] == 0


def test_bulk_hydration_avoids_targeted_requests_when_week_is_present(monkeypatch):
    monkeypatch.setattr(p42.nfl_data, "get_week_games", lambda *args, **kwargs: [_game()])
    monkeypatch.setattr(p42.odds_api, "is_configured", lambda: True)
    monkeypatch.setattr(p42.odds_api, "get_game_odds", lambda force=False: [_event()])
    monkeypatch.setattr(
        p42,
        "_provider_catalog",
        lambda: (_ for _ in ()).throw(AssertionError("catalog request")),
    )
    monkeypatch.setattr(
        p42,
        "_targeted_game_odds",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("targeted request")),
    )
    monkeypatch.setattr(p42, "_save_week_snapshot", lambda *args, **kwargs: True)

    report = p42.hydrate_week(2026, 1, "REG", allow_provider_spend=True)
    assert report["ok"] is True
    assert report["providerRequests"] == 1
    assert report["matchedGameCount"] == 1
    assert report["marketReadyGameCount"] == 1
    assert report["targetedRequests"] == 0


def test_targeted_hydration_recovers_game_missing_from_bulk(monkeypatch):
    catalog_event = {
        "id": "event-1",
        "home_team": "Alpha AAA",
        "away_team": "Beta BBB",
        "commence_time": "2026-09-10T00:00:00Z",
    }
    monkeypatch.setattr(p42.nfl_data, "get_week_games", lambda *args, **kwargs: [_game()])
    monkeypatch.setattr(p42.odds_api, "is_configured", lambda: True)
    monkeypatch.setattr(p42.odds_api, "get_game_odds", lambda force=False: [])
    monkeypatch.setattr(p42, "_provider_catalog", lambda: [catalog_event])
    monkeypatch.setattr(p42, "_targeted_game_odds", lambda *args, **kwargs: _event())
    monkeypatch.setattr(p42, "_save_week_snapshot", lambda *args, **kwargs: True)

    report = p42.hydrate_week(
        2026,
        1,
        "REG",
        allow_provider_spend=True,
        max_targeted_requests=4,
    )
    assert report["ok"] is True
    assert report["providerRequests"] == 3
    assert report["catalogEventCount"] == 1
    assert report["targetedRequests"] == 1
    assert report["targetedWithMarkets"] == 1
    assert report["marketReadyGameCount"] == 1


def test_targeted_request_budget_is_hard_capped(monkeypatch):
    games = []
    catalog = []
    for idx in range(3):
        games.append(
            {
                **_game(),
                "game_id": f"g{idx}",
                "home_name": f"Home{idx} Club{idx}",
                "away_name": f"Away{idx} Rival{idx}",
            }
        )
        catalog.append(
            {
                "id": f"e{idx}",
                "home_team": f"Home{idx} Club{idx}",
                "away_team": f"Away{idx} Rival{idx}",
            }
        )
    calls: list[str] = []
    monkeypatch.setattr(p42.nfl_data, "get_week_games", lambda *args, **kwargs: games)
    monkeypatch.setattr(p42.odds_api, "is_configured", lambda: True)
    monkeypatch.setattr(p42.odds_api, "get_game_odds", lambda force=False: [])
    monkeypatch.setattr(p42, "_provider_catalog", lambda: catalog)

    def _targeted(event_id):
        calls.append(str(event_id))
        source = next(row for row in catalog if row["id"] == event_id)
        return {**source, "bookmakers": []}

    monkeypatch.setattr(p42, "_targeted_game_odds", _targeted)
    monkeypatch.setattr(p42, "_save_week_snapshot", lambda *args, **kwargs: True)

    report = p42.hydrate_week(
        2026,
        1,
        "REG",
        allow_provider_spend=True,
        max_targeted_requests=2,
    )
    assert report["targetedRequests"] == 2
    assert len(calls) == 2


def test_cached_board_uses_persisted_event_without_provider_calls(monkeypatch):
    hydrated_at = time.time()
    snapshot = {
        "hydratedAt": "2099-09-09T23:59:00+00:00",
        "hydratedAtEpoch": hydrated_at,
        "providerRequests": 1,
        "bulkEventCount": 1,
        "catalogEventCount": 0,
        "matchedGameCount": 1,
        "marketReadyGameCount": 1,
        "targetedRequests": 0,
        "targetedWithMarkets": 0,
        "gameEventIds": {"g1": "event-1"},
        "events": [_event()],
        "missingGames": [],
    }
    monkeypatch.setattr(p42, "_load_week_snapshot", lambda *args, **kwargs: snapshot)
    monkeypatch.setattr(
        p42.p40,
        "build_week_report",
        lambda *args, **kwargs: {
            "available": True,
            "modelVersion": "p40-transparent-v1",
            "gameCount": 1,
            "decisions": [_decision()],
        },
    )
    monkeypatch.setattr(
        p42.odds_api,
        "get_game_odds",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("provider request")),
    )

    board = p42.build_cached_week_board(2026, 1, "REG")
    assert board["hydrationState"] == "available"
    assert board["pricedGameCount"] == 1
    assert board["freshPricedGameCount"] == 1
    assert board["marketCoverage"]["moneyline"] == 1
    assert board["safety"]["cacheOnlyProductReads"] is True
    assert p42.verify_board(board)["ok"] is True


def test_missing_hydration_fails_closed_without_prices(monkeypatch):
    monkeypatch.setattr(p42, "_load_week_snapshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        p42.p40,
        "build_week_report",
        lambda *args, **kwargs: {
            "available": True,
            "modelVersion": "p40-transparent-v1",
            "gameCount": 1,
            "decisions": [_decision()],
        },
    )
    board = p42.build_cached_week_board(2026, 1, "REG")
    assert board["hydrationState"] == "missing"
    assert board["pricedGameCount"] == 0
    assert board["actionableGameCount"] == 0
    assert board["rows"][0]["marketStatus"] == "unpriced"
