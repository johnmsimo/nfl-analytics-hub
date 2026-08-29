#!/usr/bin/env python3
"""P5.6 zero-credit, zero-write market calibration control-plane verification."""
from __future__ import annotations

import json

from database import db
import p44_game_decision_ledger as p44
import p54_game_market_calibration as p54
import p56_game_market_calibration_control_plane as p56


def _candidate(market: str) -> str:
    return "p54-sp-p56-verification" if market == "spread" else "p54-to-p56-verification"


def _challenger(
    market: str,
    *,
    state: str,
    eligible: bool,
    candidate: bool = True,
    samples: int = 80,
) -> dict:
    return {
        "available": True,
        "market": market,
        "state": state,
        "gradedSamples": samples,
        "candidate": {"candidateId": _candidate(market)} if candidate else None,
        "promotionGate": {
            "eligible": eligible,
            "requiresHumanReview": True,
            "automaticApply": False,
        },
    }


def _champion(market: str, *, applied: bool) -> dict:
    return {
        "available": True,
        "market": market,
        "state": "promoted" if applied else "baseline",
        "applied": applied,
        "candidateId": _candidate(market) if applied else None,
    }


def _guard(
    market: str,
    *,
    state: str,
    rollback: bool,
    samples: int = 0,
) -> dict:
    return {
        "available": True,
        "market": market,
        "state": state,
        "gradedSamples": samples,
        "rollbackGate": {
            "recommended": rollback,
            "requiresHumanReview": True,
            "automaticRollback": False,
        },
    }


def _aggregate(
    *,
    spread_challenger: dict,
    spread_champion: dict,
    spread_guard: dict,
    total_challenger: dict,
    total_champion: dict,
    total_guard: dict,
) -> dict:
    return p56.build_control_plane(
        {
            "available": True,
            "markets": {
                "spread": spread_challenger,
                "total": total_challenger,
            },
            "champions": {
                "spread": spread_champion,
                "total": total_champion,
            },
        },
        {
            "available": True,
            "markets": {
                "spread": spread_guard,
                "total": total_guard,
            },
        },
    )


def main() -> int:
    from app import app

    with app.app_context():
        receipts_before = p44.list_receipts(limit=2000)
        events_before = p54.list_events(limit=200)
        production = p56.build_production_control_plane()

        promotion = _aggregate(
            spread_challenger=_challenger("spread", state="review", eligible=True),
            spread_champion=_champion("spread", applied=False),
            spread_guard=_guard("spread", state="baseline", rollback=False),
            total_challenger=_challenger(
                "total", state="collecting", eligible=False, candidate=False, samples=10
            ),
            total_champion=_champion("total", applied=False),
            total_guard=_guard("total", state="baseline", rollback=False),
        )
        healthy = _aggregate(
            spread_challenger=_challenger(
                "spread", state="rejected", eligible=False, candidate=False
            ),
            spread_champion=_champion("spread", applied=True),
            spread_guard=_guard("spread", state="healthy", rollback=False, samples=30),
            total_challenger=_challenger(
                "total", state="rejected", eligible=False, candidate=False
            ),
            total_champion=_champion("total", applied=True),
            total_guard=_guard("total", state="healthy", rollback=False, samples=30),
        )
        rollback = _aggregate(
            spread_challenger=_challenger(
                "spread", state="rejected", eligible=False, candidate=False
            ),
            spread_champion=_champion("spread", applied=True),
            spread_guard=_guard(
                "spread", state="rollback-review", rollback=True, samples=30
            ),
            total_challenger=_challenger("total", state="review", eligible=True),
            total_champion=_champion("total", applied=False),
            total_guard=_guard("total", state="baseline", rollback=False),
        )
        collecting = _aggregate(
            spread_challenger=_challenger(
                "spread", state="collecting", eligible=False, candidate=False, samples=10
            ),
            spread_champion=_champion("spread", applied=False),
            spread_guard=_guard("spread", state="baseline", rollback=False),
            total_challenger=_challenger(
                "total", state="collecting", eligible=False, candidate=False, samples=10
            ),
            total_champion=_champion("total", applied=False),
            total_guard=_guard("total", state="baseline", rollback=False),
        )

        receipts_after = p44.list_receipts(limit=2000)
        events_after = p54.list_events(limit=200)
        db.session.rollback()

    safety = production.get("safetyContract") or {}
    markets = production.get("markets") or {}
    valid_aggregate_states = {
        "rollback-review",
        "promotion-review",
        "collecting",
        "champions-healthy",
        "champions-monitor",
        "baseline-monitor",
    }
    valid_market_states = {
        "rollback-review",
        "champion-collecting",
        "champion-healthy",
        "champion-monitor",
        "promotion-review",
        "challenger-collecting",
        "baseline-monitor",
    }
    gates = {
        "production_state_valid": production.get("state") in valid_aggregate_states,
        "spread_state_valid": (markets.get("spread") or {}).get("state")
        in valid_market_states,
        "total_state_valid": (markets.get("total") or {}).get("state")
        in valid_market_states,
        "production_zero_credit": safety.get("providerRequests") == 0,
        "production_read_only": safety.get("readOnly") is True,
        "market_isolation_enabled": safety.get("marketIsolated") is True,
        "automatic_promotion_disabled": safety.get("automaticPromotion") is False,
        "automatic_rollback_disabled": safety.get("automaticRollback") is False,
        "owner_confirmation_required": safety.get("ownerConfirmationRequired") is True,
        "synthetic_market_promotion_review": promotion.get("state") == "promotion-review"
        and promotion.get("promotionReviewMarkets") == ["spread"]
        and (promotion.get("markets") or {}).get("spread", {}).get("promoteReady")
        is True,
        "synthetic_healthy_market_champions": healthy.get("state") == "champions-healthy"
        and set(healthy.get("healthyChampionMarkets") or []) == {"spread", "total"},
        "synthetic_market_rollback_review_has_priority": rollback.get("state")
        == "rollback-review"
        and rollback.get("rollbackReviewMarkets") == ["spread"]
        and rollback.get("promotionReviewMarkets") == ["total"],
        "synthetic_collecting_is_non_actionable": collecting.get("state") == "collecting"
        and not collecting.get("promotionReviewMarkets")
        and not collecting.get("rollbackReviewMarkets"),
        "game_receipts_unchanged": receipts_before == receipts_after,
        "market_promotion_history_unchanged": events_before == events_after,
    }
    report = {
        "ok": all(gates.values()),
        "phase": "P5.6",
        "mode": "zero-credit-zero-write-market-calibration-control-plane-verification",
        "gates": gates,
        "blockingFailures": [key for key, passed in gates.items() if not passed],
        "production": {
            "state": production.get("state"),
            "recommendedAction": production.get("recommendedAction"),
            "promotionReviewMarkets": production.get("promotionReviewMarkets"),
            "rollbackReviewMarkets": production.get("rollbackReviewMarkets"),
            "activeChampionMarkets": production.get("activeChampionMarkets"),
            "spreadState": (markets.get("spread") or {}).get("state"),
            "totalState": (markets.get("total") or {}).get("state"),
            "receiptCount": len(receipts_before),
            "promotionEventCount": len(events_before),
        },
    }
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
