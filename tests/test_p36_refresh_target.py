from __future__ import annotations

import p36_verification as p36


def test_refresh_target_prefers_ranked_pick_from_known_priced_game(monkeypatch):
    rows = [
        {
            "gameId": "game-a",
            "decisionGrade": "Strong Play",
            "decisionScore": 0.90,
            "priceStatus": "unpriced",
        },
        {
            "gameId": "game-b",
            "decisionGrade": "Play",
            "decisionScore": 0.80,
            "priceStatus": "stale",
        },
    ]
    monkeypatch.setattr(
        p36.dd,
        "build_delivery",
        lambda values, limit=100: {"picks": list(values)},
    )

    target = p36._select_refresh_target(rows)

    assert target["gameId"] == "game-b"
    assert target["reason"] == "ranked_pick_with_known_pricing"
    assert target["knownPricedGames"] == 1


def test_refresh_target_falls_back_to_any_known_priced_game_when_delivery_has_none(monkeypatch):
    rows = [
        {
            "gameId": "game-a",
            "decisionGrade": "Pass",
            "decisionScore": 0.30,
            "priceStatus": "stale",
        }
    ]
    monkeypatch.setattr(p36.dd, "build_delivery", lambda values, limit=100: {"picks": []})

    target = p36._select_refresh_target(rows)

    assert target["gameId"] == "game-a"
    assert target["reason"] == "known_priced_game_fallback"
    assert target["knownPricedGames"] == 1


def test_price_game_diagnostics_exposes_refreshed_game_contribution():
    rows = [
        {
            "gameId": "game-a",
            "priceStatus": "positive_value",
            "quoteStatus": "fresh",
            "actionable": True,
            "oddsSnapshotAgeSeconds": 4.2,
        },
        {
            "gameId": "game-a",
            "priceStatus": "stale",
            "quoteStatus": "stale",
            "actionable": False,
            "oddsSnapshotAgeSeconds": 4.2,
        },
        {
            "gameId": "game-b",
            "priceStatus": "unpriced",
            "quoteStatus": "unpriced",
            "actionable": False,
            "oddsSnapshotAgeSeconds": None,
        },
    ]

    diagnostics = p36._price_game_diagnostics(rows)

    assert diagnostics[0]["gameId"] == "game-a"
    assert diagnostics[0]["rows"] == 2
    assert diagnostics[0]["pricedRows"] == 2
    assert diagnostics[0]["freshRows"] == 1
    assert diagnostics[0]["actionableRows"] == 1
    assert diagnostics[0]["snapshotAgeSeconds"] == 4.2
