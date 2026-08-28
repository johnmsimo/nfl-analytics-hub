from __future__ import annotations

import time

import p41_game_market_pricing as p41


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


def _event() -> dict:
    return {
        "id": "event-1",
        "home_team": "Alpha AAA",
        "away_team": "Beta BBB",
        "commence_time": "2026-09-10T00:00:00Z",
        "bookmakers": [
            {
                "key": "book-a",
                "title": "Book A",
                "last_update": "2026-09-09T23:59:00Z",
                "markets": [
                    {
                        "key": "h2h",
                        "last_update": "2026-09-09T23:59:00Z",
                        "outcomes": [
                            {"name": "Alpha AAA", "price": 110},
                            {"name": "Beta BBB", "price": -130},
                        ],
                    },
                    {
                        "key": "spreads",
                        "outcomes": [
                            {"name": "Alpha AAA", "price": -105, "point": -3.5},
                            {"name": "Beta BBB", "price": -115, "point": 3.5},
                        ],
                    },
                    {
                        "key": "totals",
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
                "last_update": "2026-09-09T23:59:00Z",
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


def test_fresh_positive_value_markets_can_become_actionable():
    row = p41.price_game_decision(_decision(), _event(), fetched_at=time.time())
    assert row["marketStatus"] == "fresh"
    assert row["markets"]["moneyline"]["actionable"] is True
    assert row["markets"]["spread"]["actionable"] is True
    assert row["markets"]["total"]["actionable"] is True
    assert set(row["actionableMarkets"]) == {"moneyline", "spread", "total"}
    for market in row["markets"].values():
        pricing = market["pricing"]
        assert pricing["quoteStatus"] == "fresh"
        assert pricing["pairedFairBookCount"] >= 1
        assert pricing["priceStatus"] == "positive_value"


def test_stale_quotes_always_fail_closed():
    row = p41.price_game_decision(
        _decision(),
        _event(),
        fetched_at=time.time() - 3600,
    )
    assert row["actionable"] is False
    assert row["actionableMarkets"] == []
    assert all(market["actionable"] is False for market in row["markets"].values())
    assert all(market["pricing"]["quoteStatus"] == "stale" for market in row["markets"].values())


def test_missing_event_preserves_model_but_never_invents_price():
    row = p41.price_game_decision(_decision(), None, fetched_at=None)
    assert row["marketStatus"] == "unpriced"
    assert row["markets"] == {}
    assert row["actionable"] is False
    assert row["actionableMarkets"] == []


def test_total_expected_points_are_bounded_and_transparent():
    row = p41.price_game_decision(_decision(), _event(), fetched_at=time.time())
    total = row["markets"]["total"]
    assert 25.0 <= total["modelExpectedTotal"] <= 70.0
    assert total["line"] == 40.5
    assert total["selectedSide"] in {"over", "under"}
    assert total["confidenceScore"] <= 79.0
    assert "pace/play-volume" in total["risk"]


def test_verify_actionability_rejects_invalid_actionable_market():
    good = p41.price_game_decision(_decision(), _event(), fetched_at=time.time())
    assert p41.verify_actionability([good])["ok"] is True
    bad = p41.price_game_decision(_decision(), _event(), fetched_at=time.time())
    bad["markets"]["moneyline"]["pricing"]["quoteStatus"] = "stale"
    assert p41.verify_actionability([bad])["ok"] is False


def test_week_cache_mode_never_calls_live_provider(monkeypatch):
    game = {
        "game_id": "g1",
        "home_team": "AAA",
        "away_team": "BBB",
        "home_name": "Alpha AAA",
        "away_name": "Beta BBB",
    }
    monkeypatch.setattr(
        p41.p40,
        "build_week_report",
        lambda *args, **kwargs: {
            "available": True,
            "modelVersion": "p40-transparent-v1",
            "gameCount": 1,
            "decisions": [_decision()],
        },
    )
    monkeypatch.setattr(p41.nfl_data, "get_week_games", lambda *args, **kwargs: [game])
    monkeypatch.setattr(
        p41.odds_api,
        "snapshot_status",
        lambda: {"game_snapshot_age_seconds": 30.0},
    )
    monkeypatch.setattr(p41.odds_api, "peek_game_odds", lambda: [_event()])
    monkeypatch.setattr(
        p41.odds_api,
        "find_event_for_game",
        lambda *args, **kwargs: _event(),
    )
    monkeypatch.setattr(
        p41.odds_api,
        "get_game_odds",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("provider call")),
    )
    report = p41.build_week_market_report(2026, 1, "REG", pricing_mode="cache")
    assert report["decisionCount"] == 1
    assert report["pricingMode"] == "cache"
    assert report["safety"]["liveRefreshRequiresExplicitMode"] is True
