from __future__ import annotations

import p58_calibration_portfolio_control_plane as p58


def _moneyline(
    *,
    state: str = "baseline-monitor",
    promote: bool = False,
    rollback: bool = False,
    active: bool = False,
    available: bool = True,
) -> dict:
    return {
        "available": available,
        "state": state,
        "recommendedAction": "MONEYLINE_ACTION",
        "message": "moneyline",
        "candidateId": "p49-moneyline" if promote else None,
        "championCandidateId": "p49-moneyline-live" if active else None,
        "championApplied": active,
        "promoteReady": promote,
        "rollbackReady": rollback,
        "blockers": [] if promote or active else ["no_current_candidate"],
        "evidence": {
            "challengerState": "review" if promote else "collecting",
            "challengerGradedSamples": 40,
            "p50Eligible": promote,
            "guardState": "rollback-review" if rollback else ("healthy" if active else "baseline"),
            "guardGradedSamples": 25 if active else 0,
            "rollbackRecommended": rollback,
        },
        "commands": {
            "promotion": {
                "endpoint": "/api/game-calibration/promote",
                "allowed": promote,
                "candidateId": "p49-moneyline" if promote else None,
                "confirmation": "PROMOTE_GAME_CALIBRATION",
            },
            "rollback": {
                "endpoint": "/api/game-calibration/rollback",
                "allowed": active,
                "recommended": rollback,
                "confirmation": "ROLLBACK_GAME_CALIBRATION",
            },
        },
    }


def _market(
    market: str,
    *,
    state: str = "baseline-monitor",
    promote: bool = False,
    rollback: bool = False,
    active: bool = False,
    available: bool = True,
) -> dict:
    prefix = "p54-sp" if market == "spread" else "p54-to"
    return {
        "available": available,
        "market": market,
        "state": state,
        "recommendedAction": f"{market.upper()}_ACTION",
        "message": market,
        "candidateId": f"{prefix}-candidate" if promote else None,
        "championCandidateId": f"{prefix}-live" if active else None,
        "championApplied": active,
        "promoteReady": promote,
        "rollbackReady": rollback,
        "blockers": [] if promote or active else ["no_current_candidate"],
        "evidence": {
            "challengerState": "review" if promote else "collecting",
            "challengerGradedSamples": 55,
            "promotionEligible": promote,
            "guardState": "rollback-review" if rollback else ("healthy" if active else "baseline"),
            "guardGradedSamples": 22 if active else 0,
            "rollbackRecommended": rollback,
        },
        "commands": {
            "promotion": {
                "endpoint": "/api/game-market-calibration/promote",
                "allowed": promote,
                "market": market,
                "candidateId": f"{prefix}-candidate" if promote else None,
                "confirmation": "PROMOTE_GAME_MARKET_CALIBRATION",
            },
            "rollback": {
                "endpoint": "/api/game-market-calibration/rollback",
                "allowed": active,
                "recommended": rollback,
                "market": market,
                "confirmation": "ROLLBACK_GAME_MARKET_CALIBRATION",
            },
        },
    }


def _market_control(spread: dict | None = None, total: dict | None = None) -> dict:
    return {
        "available": True,
        "markets": {
            "spread": spread or _market("spread"),
            "total": total or _market("total"),
        },
    }


def test_portfolio_baseline_state_when_no_market_requires_action():
    report = p58.build_portfolio(_moneyline(), _market_control())
    assert report["state"] == "baseline-monitor"
    assert report["recommendedAction"] == "KEEP_CALIBRATION_BASELINES"
    assert report["promotionReviewMarkets"] == []
    assert report["rollbackReviewMarkets"] == []
    assert report["activeChampionMarkets"] == []
    assert set(report["markets"]) == {"moneyline", "spread", "total"}


def test_rollback_review_has_priority_over_promotion_review():
    report = p58.build_portfolio(
        _moneyline(state="promotion-review", promote=True),
        _market_control(
            spread=_market(
                "spread",
                state="rollback-review",
                rollback=True,
                active=True,
            )
        ),
    )
    assert report["state"] == "rollback-review"
    assert report["recommendedAction"] == "REVIEW_CALIBRATION_ROLLBACKS"
    assert report["rollbackReviewMarkets"] == ["spread"]
    assert report["promotionReviewMarkets"] == ["moneyline"]


def test_promotion_review_combines_moneyline_spread_and_total_without_reinterpreting_gates():
    report = p58.build_portfolio(
        _moneyline(state="promotion-review", promote=True),
        _market_control(
            spread=_market("spread", state="promotion-review", promote=True),
            total=_market("total", state="promotion-review", promote=True),
        ),
    )
    assert report["state"] == "promotion-review"
    assert report["promotionReviewMarkets"] == ["moneyline", "spread", "total"]
    assert report["markets"]["moneyline"]["governanceSource"] == "P5.2"
    assert report["markets"]["spread"]["governanceSource"] == "P5.6"
    assert report["markets"]["total"]["governanceSource"] == "P5.6"
    assert report["commands"]["moneyline"]["promotion"]["endpoint"] == "/api/game-calibration/promote"
    assert report["commands"]["spread"]["promotion"]["endpoint"] == "/api/game-market-calibration/promote"


def test_collecting_state_is_preserved_across_all_markets():
    report = p58.build_portfolio(
        _moneyline(state="challenger-collecting"),
        _market_control(
            spread=_market("spread", state="champion-collecting", active=True),
            total=_market("total", state="baseline-monitor"),
        ),
    )
    assert report["state"] == "collecting"
    assert report["collectingMarkets"] == ["moneyline", "spread"]


def test_all_active_healthy_champions_resolve_to_healthy_portfolio():
    report = p58.build_portfolio(
        _moneyline(state="champion-healthy", active=True),
        _market_control(
            spread=_market("spread", state="champion-healthy", active=True),
            total=_market("total", state="champion-healthy", active=True),
        ),
    )
    assert report["state"] == "champions-healthy"
    assert report["activeChampionMarkets"] == ["moneyline", "spread", "total"]
    assert report["healthyChampionMarkets"] == ["moneyline", "spread", "total"]


def test_unavailable_source_fails_closed_and_does_not_manufacture_mutation_readiness():
    report = p58.build_portfolio(
        _moneyline(available=False),
        _market_control(),
    )
    assert report["available"] is False
    assert report["state"] == "degraded-monitor"
    assert report["unavailableMarkets"] == ["moneyline"]
    assert report["markets"]["moneyline"]["promoteReady"] is False
    assert report["markets"]["moneyline"]["rollbackReady"] is False


def test_portfolio_safety_contract_is_read_only_and_delegated():
    report = p58.build_portfolio(_moneyline(), _market_control())
    safety = report["safetyContract"]
    assert safety["readOnly"] is True
    assert safety["providerRequests"] == 0
    assert safety["automaticPromotion"] is False
    assert safety["automaticRollback"] is False
    assert safety["delegatesMoneylineGatesToP52"] is True
    assert safety["delegatesSpreadTotalGatesToP56"] is True
    assert safety["placesBets"] is False


def test_portfolio_route_is_read_only(client, monkeypatch):
    monkeypatch.setattr(
        p58,
        "build_production_portfolio",
        lambda: {
            "available": True,
            "state": "baseline-monitor",
            "recommendedAction": "KEEP_CALIBRATION_BASELINES",
            "markets": {},
            "promotionReviewMarkets": [],
            "rollbackReviewMarkets": [],
            "safetyContract": {
                "readOnly": True,
                "providerRequests": 0,
                "automaticPromotion": False,
                "automaticRollback": False,
            },
        },
    )
    response = client.get("/api/game-calibration/portfolio-control-plane")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["state"] == "baseline-monitor"
    assert payload["safetyContract"]["readOnly"] is True
    assert payload["safetyContract"]["providerRequests"] == 0
