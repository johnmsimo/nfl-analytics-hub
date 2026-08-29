#!/usr/bin/env python3
"""P5.8 zero-credit, zero-write all-market calibration portfolio verification."""
from __future__ import annotations

import json

from database import db
import p44_game_decision_ledger as p44
import p50_game_calibration_promotion as p50
import p54_game_market_calibration as p54
import p58_calibration_portfolio_control_plane as p58


def _moneyline(*, state: str, promote: bool = False, rollback: bool = False, active: bool = False) -> dict:
    return {
        "available": True,
        "state": state,
        "recommendedAction": "MONEYLINE",
        "message": "moneyline",
        "candidateId": "p49-p58-verification" if promote else None,
        "championCandidateId": "p49-p58-live" if active else None,
        "championApplied": active,
        "promoteReady": promote,
        "rollbackReady": rollback,
        "blockers": [],
        "evidence": {
            "challengerState": "review" if promote else "collecting",
            "challengerGradedSamples": 50,
            "p50Eligible": promote,
            "guardState": "rollback-review" if rollback else ("healthy" if active else "baseline"),
            "guardGradedSamples": 25 if active else 0,
            "rollbackRecommended": rollback,
        },
        "commands": {},
    }


def _market(market: str, *, state: str, promote: bool = False, rollback: bool = False, active: bool = False) -> dict:
    return {
        "available": True,
        "market": market,
        "state": state,
        "recommendedAction": market.upper(),
        "message": market,
        "candidateId": f"p54-{market[:2]}-p58" if promote else None,
        "championCandidateId": f"p54-{market[:2]}-live" if active else None,
        "championApplied": active,
        "promoteReady": promote,
        "rollbackReady": rollback,
        "blockers": [],
        "evidence": {
            "challengerState": "review" if promote else "collecting",
            "challengerGradedSamples": 55,
            "promotionEligible": promote,
            "guardState": "rollback-review" if rollback else ("healthy" if active else "baseline"),
            "guardGradedSamples": 22 if active else 0,
            "rollbackRecommended": rollback,
        },
        "commands": {},
    }


def _market_control(spread: dict, total: dict) -> dict:
    return {"available": True, "markets": {"spread": spread, "total": total}}


def main() -> int:
    from app import app

    with app.app_context():
        receipts_before = p44.list_receipts(limit=2000)
        moneyline_events_before = p50.list_events(limit=100)
        market_events_before = p54.list_events(limit=200)
        production = p58.build_production_portfolio()

        rollback_priority = p58.build_portfolio(
            _moneyline(state="promotion-review", promote=True),
            _market_control(
                _market("spread", state="rollback-review", rollback=True, active=True),
                _market("total", state="baseline-monitor"),
            ),
        )
        promotion = p58.build_portfolio(
            _moneyline(state="promotion-review", promote=True),
            _market_control(
                _market("spread", state="promotion-review", promote=True),
                _market("total", state="baseline-monitor"),
            ),
        )
        collecting = p58.build_portfolio(
            _moneyline(state="challenger-collecting"),
            _market_control(
                _market("spread", state="champion-collecting", active=True),
                _market("total", state="baseline-monitor"),
            ),
        )
        healthy = p58.build_portfolio(
            _moneyline(state="champion-healthy", active=True),
            _market_control(
                _market("spread", state="champion-healthy", active=True),
                _market("total", state="champion-healthy", active=True),
            ),
        )

        receipts_after = p44.list_receipts(limit=2000)
        moneyline_events_after = p50.list_events(limit=100)
        market_events_after = p54.list_events(limit=200)
        db.session.rollback()

    safety = production.get("safetyContract") or {}
    valid_states = {
        "rollback-review",
        "promotion-review",
        "collecting",
        "champions-healthy",
        "champions-monitor",
        "degraded-monitor",
        "baseline-monitor",
    }
    gates = {
        "production_state_valid": production.get("state") in valid_states,
        "all_three_market_views_present": set((production.get("markets") or {}).keys())
        == {"moneyline", "spread", "total"},
        "production_zero_credit": safety.get("providerRequests") == 0,
        "production_read_only": safety.get("readOnly") is True,
        "automatic_promotion_disabled": safety.get("automaticPromotion") is False,
        "automatic_rollback_disabled": safety.get("automaticRollback") is False,
        "moneyline_gate_delegation_preserved": safety.get("delegatesMoneylineGatesToP52") is True,
        "spread_total_gate_delegation_preserved": safety.get("delegatesSpreadTotalGatesToP56") is True,
        "rollback_priority_over_promotion": rollback_priority.get("state") == "rollback-review"
        and rollback_priority.get("rollbackReviewMarkets") == ["spread"]
        and rollback_priority.get("promotionReviewMarkets") == ["moneyline"],
        "promotion_review_aggregates_without_mutation": promotion.get("state") == "promotion-review"
        and promotion.get("promotionReviewMarkets") == ["moneyline", "spread"],
        "collecting_paths_remain_non_mutating": collecting.get("state") == "collecting"
        and collecting.get("collectingMarkets") == ["moneyline", "spread"],
        "healthy_champions_resolve_portfolio_health": healthy.get("state") == "champions-healthy"
        and healthy.get("healthyChampionMarkets") == ["moneyline", "spread", "total"],
        "game_receipts_unchanged": receipts_before == receipts_after,
        "moneyline_promotion_history_unchanged": moneyline_events_before == moneyline_events_after,
        "market_promotion_history_unchanged": market_events_before == market_events_after,
    }
    report = {
        "ok": all(gates.values()),
        "phase": "P5.8",
        "mode": "zero-credit-zero-write-all-market-calibration-portfolio-verification",
        "gates": gates,
        "blockingFailures": [key for key, passed in gates.items() if not passed],
        "production": {
            "state": production.get("state"),
            "recommendedAction": production.get("recommendedAction"),
            "promotionReviewMarkets": production.get("promotionReviewMarkets"),
            "rollbackReviewMarkets": production.get("rollbackReviewMarkets"),
            "activeChampionMarkets": production.get("activeChampionMarkets"),
            "collectingMarkets": production.get("collectingMarkets"),
            "healthyChampionMarkets": production.get("healthyChampionMarkets"),
            "unavailableMarkets": production.get("unavailableMarkets"),
            "receiptCount": len(receipts_before),
            "moneylinePromotionEventCount": len(moneyline_events_before),
            "marketPromotionEventCount": len(market_events_before),
        },
    }
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
