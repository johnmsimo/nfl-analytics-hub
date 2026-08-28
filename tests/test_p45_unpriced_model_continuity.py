from __future__ import annotations

import p43_game_decision_delivery as p43
import p45_smart_market_refresh as p45


def _unpriced_board(*, grade: str = "Play") -> dict:
    return {
        "available": True,
        "modelVersion": "p42-hydration-v1",
        "season": 2026,
        "seasonType": "PRE",
        "week": 3,
        "hydrationState": "available",
        "hydratedAt": "2026-08-28T21:43:57+00:00",
        "hydrationAgeSeconds": 300.0,
        "gameCount": 1,
        "pricedGameCount": 0,
        "freshPricedGameCount": 0,
        "actionableGameCount": 0,
        "rows": [
            {
                "gameId": "g1",
                "season": 2026,
                "seasonType": "PRE",
                "week": 3,
                "kickoffAt": "2026-08-28T22:00:00Z",
                "homeTeam": "AAA",
                "awayTeam": "BBB",
                "homeWinProbability": 0.66,
                "awayWinProbability": 0.34,
                "selectedProbability": 0.66,
                "selectedSide": "home",
                "selectedTeam": "AAA",
                "confidenceScore": 78.0,
                "decisionGrade": grade,
                "reasons": [{"factor": "team-strength edge"}],
                "risks": ["Early-season evidence."],
                "marketStatus": "unpriced",
                "actionable": False,
                "actionableMarkets": [],
                "markets": {},
            }
        ],
    }


def test_p43_preserves_unpriced_model_moneyline_without_inventing_price():
    delivery = p43.build_delivery_from_board(_unpriced_board())
    assert len(delivery["allMarkets"]) == 1
    row = delivery["allMarkets"][0]
    assert row["gameId"] == "g1"
    assert row["market"] == "moneyline"
    assert row["pickLabel"] == "AAA ML"
    assert row["modelProbability"] == 0.66
    assert row["decisionGrade"] == "Play"
    assert row["quoteStatus"] == "unpriced"
    assert row["priceStatus"] == "unpriced"
    assert row["bestBook"] is None
    assert row["bestPrice"] is None
    assert row["fairMarketProbability"] is None
    assert row["edge"] is None
    assert row["evPct"] is None
    assert row["kellyPct"] is None
    assert row["actionable"] is False
    assert row["modelOnlyFallback"] is True


def test_p45_turns_unpriced_play_into_visible_model_opportunity():
    delivery = p43.build_delivery_from_board(_unpriced_board())
    out = p45.enrich_delivery(delivery)
    assert out["state"] == "model-opportunities"
    assert out["summary"]["visibleOpportunities"] == 1
    opportunity = out["opportunities"][0]
    assert opportunity["opportunityState"] == "MODEL"
    assert opportunity["recommendedAction"] == "MODEL LEAN"
    assert opportunity["actionable"] is False
    assert opportunity["bestPrice"] is None


def test_unpriced_pass_remains_non_visible_pass():
    delivery = p43.build_delivery_from_board(_unpriced_board(grade="Pass"))
    out = p45.enrich_delivery(delivery)
    assert out["state"] == "no-play"
    assert out["summary"]["visibleOpportunities"] == 0
    assert len(out["allOpportunities"]) == 1
    assert out["allOpportunities"][0]["opportunityState"] == "PASS"
