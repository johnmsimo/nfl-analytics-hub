from __future__ import annotations

import p36_verification as p36


def _game() -> dict:
    return {"game_id": "g1", "home_name": "Home Team", "away_name": "Away Team"}


def _model_rows(player: str = "Joe Quarterback") -> list[dict]:
    return [{"gameId": "g1", "player": player, "marketKey": "pass_yds"}]


def _payload(player: str = "Joe Quarterback") -> dict:
    return {
        "bookmakers": [
            {
                "key": "book-a",
                "title": "Book A",
                "markets": [
                    {
                        "key": "player_pass_yds",
                        "last_update": "2026-08-28T12:00:00Z",
                        "outcomes": [
                            {
                                "name": "Over",
                                "description": player,
                                "point": 250.5,
                                "price": -110,
                            },
                            {
                                "name": "Under",
                                "description": player,
                                "point": 250.5,
                                "price": -110,
                            },
                        ],
                    }
                ],
            }
        ]
    }


def test_payload_diagnostic_classifies_props_not_posted(monkeypatch):
    monkeypatch.setattr(
        p36.odds_api,
        "find_event_for_game",
        lambda game, cache_only=False: {"id": "evt-1"},
    )
    monkeypatch.setattr(
        p36.odds_api,
        "event_props_snapshot",
        lambda event_id: {
            "available": True,
            "age_seconds": 4.0,
            "fetched_at": 1.0,
            "data": {"bookmakers": []},
        },
    )

    result = p36._provider_payload_diagnostic(_game(), _model_rows())

    assert result["diagnosis"] == "provider_props_not_posted"
    assert result["bookmakers"] == 0
    assert result["parsedQuoteRows"] == 0
    assert result["matchablePlayerMarketPairs"] == 0


def test_payload_diagnostic_proves_supported_player_market_overlap(monkeypatch):
    monkeypatch.setattr(
        p36.odds_api,
        "find_event_for_game",
        lambda game, cache_only=False: {"id": "evt-1"},
    )
    monkeypatch.setattr(
        p36.odds_api,
        "event_props_snapshot",
        lambda event_id: {
            "available": True,
            "age_seconds": 4.0,
            "fetched_at": 1787920000.0,
            "data": _payload(),
        },
    )

    result = p36._provider_payload_diagnostic(_game(), _model_rows())

    assert result["diagnosis"] == "matchable_quotes_present"
    assert result["recognizedMarketKeys"] == ["player_pass_yds"]
    assert result["parsedQuoteRows"] == 2
    assert result["usableQuoteRows"] == 2
    assert result["providerPlayers"] == 1
    assert result["projectedPlayers"] == 1
    assert result["playerOverlap"] == 1
    assert result["matchablePlayerMarketPairs"] == 1


def test_payload_diagnostic_detects_player_identity_mismatch(monkeypatch):
    monkeypatch.setattr(
        p36.odds_api,
        "find_event_for_game",
        lambda game, cache_only=False: {"id": "evt-1"},
    )
    monkeypatch.setattr(
        p36.odds_api,
        "event_props_snapshot",
        lambda event_id: {
            "available": True,
            "age_seconds": 4.0,
            "fetched_at": 1787920000.0,
            "data": _payload("Different Player"),
        },
    )

    result = p36._provider_payload_diagnostic(_game(), _model_rows())

    assert result["diagnosis"] == "provider_player_names_do_not_overlap_model"
    assert result["usableQuoteRows"] == 2
    assert result["playerOverlap"] == 0
    assert result["matchablePlayerMarketPairs"] == 0


def test_aggregate_payload_classification_distinguishes_external_availability(monkeypatch):
    games = [
        {"game_id": "g1"},
        {"game_id": "g2"},
    ]
    monkeypatch.setattr(p36.nfl_data, "get_week_games", lambda season, week, season_type: games)
    monkeypatch.setattr(
        p36,
        "_provider_payload_diagnostic",
        lambda game, rows: {
            "gameId": game["game_id"],
            "snapshotAvailable": True,
            "bookmakers": 0,
            "usableQuoteRows": 0,
            "matchablePlayerMarketPairs": 0,
            "diagnosis": "provider_props_not_posted",
        },
    )

    result = p36._provider_payload_diagnostics(2026, 1, "REG", [])

    assert result["classification"] == "provider_player_props_not_posted"
    assert result["snapshotsAvailable"] == 2
    assert result["gamesWithBookmakers"] == 0
    assert result["gamesWithUsableQuotes"] == 0
    assert result["gamesWithMatchableQuotes"] == 0
