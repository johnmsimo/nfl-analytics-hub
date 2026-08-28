from __future__ import annotations

import p36_verification as p36


def test_refresh_target_prefers_existing_priced_game():
    rows = [
        {
            "gameId": "g1",
            "decisionGrade": "Play",
            "decisionScore": 0.91,
            "priceStatus": "stale",
        },
        {
            "gameId": "g2",
            "decisionGrade": "Strong Play",
            "decisionScore": 0.95,
            "priceStatus": "unpriced",
        },
    ]

    result = p36._select_refresh_target(rows, {"details": []})

    assert result["gameId"] == "g1"
    assert result["knownPricedGames"] == 1
    assert result["reason"] in {
        "ranked_pick_with_known_pricing",
        "known_priced_game_fallback",
    }


def test_refresh_target_uses_richest_matchable_provider_game_when_quotes_expired():
    rows = [
        {
            "gameId": "g1",
            "decisionGrade": "Strong Play",
            "decisionScore": 0.96,
            "priceStatus": "unpriced",
        },
        {
            "gameId": "g2",
            "decisionGrade": "Play",
            "decisionScore": 0.90,
            "priceStatus": "unpriced",
        },
        {
            "gameId": "g3",
            "decisionGrade": "Play",
            "decisionScore": 0.88,
            "priceStatus": "unpriced",
        },
    ]
    diagnostics = {
        "details": [
            {
                "gameId": "g1",
                "matchablePlayerMarketPairs": 12,
                "usableQuoteRows": 40,
                "bookmakers": 1,
                "marketOverlap": ["anytime_td"],
                "snapshotAgeSeconds": 47000.0,
            },
            {
                "gameId": "g2",
                "matchablePlayerMarketPairs": 31,
                "usableQuoteRows": 173,
                "bookmakers": 4,
                "marketOverlap": [
                    "anytime_td",
                    "pass_tds",
                    "pass_yds",
                    "rec_yds",
                    "receptions",
                    "rush_yds",
                ],
                "snapshotAgeSeconds": 47100.0,
            },
            {
                "gameId": "g3",
                "matchablePlayerMarketPairs": 37,
                "usableQuoteRows": 275,
                "bookmakers": 5,
                "marketOverlap": ["anytime_td", "pass_yds"],
                "snapshotAgeSeconds": 47100.0,
            },
        ]
    }

    result = p36._select_refresh_target(rows, diagnostics)

    # Broad supported-market coverage wins over a higher pair count in a
    # narrower market set, reducing the risk of refreshing an anytime-TD-only
    # event whose selected model side is not quoted.
    assert result["gameId"] == "g2"
    assert result["reason"] == "matchable_provider_quote_overlap"
    assert result["knownPricedGames"] == 0
    assert result["matchableProviderGames"] == 3
    assert result["providerMarketBreadth"] == 6
    assert result["providerMatchablePlayerMarketPairs"] == 31
    assert result["providerBookmakers"] == 4
    assert result["providerUsableQuoteRows"] == 173


def test_refresh_target_falls_back_to_model_pick_without_provider_evidence():
    rows = [
        {
            "gameId": "g9",
            "decisionGrade": "Strong Play",
            "decisionScore": 0.93,
            "priceStatus": "unpriced",
        }
    ]

    result = p36._select_refresh_target(
        rows,
        {"details": [{"gameId": "g9", "matchablePlayerMarketPairs": 0}]},
    )

    assert result["gameId"] == "g9"
    assert result["reason"] == "model_pick_fallback_no_provider_pricing_evidence"
    assert result["knownPricedGames"] == 0
    assert result["matchableProviderGames"] == 0
