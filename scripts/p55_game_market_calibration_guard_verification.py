#!/usr/bin/env python3
"""P5.5 zero-credit, zero-write spread/total champion guard verification."""
from __future__ import annotations

import json

from database import db
import p44_game_decision_ledger as p44
import p49_game_calibration as p49
import p54_game_market_calibration as p54
import p55_game_market_calibration_guard as p55


def _candidate(market: str) -> str:
    return "p54-sp-p55-verification" if market == "spread" else "p54-to-p55-verification"


def _event(market: str, slope: float, intercept: float) -> dict:
    return {
        "eventId": f"p54-{market}-p55-verification-event",
        "market": market,
        "action": "promote",
        "candidateId": _candidate(market),
        "family": "logit-affine",
        "parameters": {"slope": slope, "intercept": intercept},
        "approvedBy": "p55-verifier",
        "createdAt": "2026-09-01T00:00:00+00:00",
    }


def _champion(market: str, slope: float, intercept: float) -> dict:
    return {
        "available": True,
        "market": market,
        "state": "promoted",
        "applied": True,
        "candidateId": _candidate(market),
        "family": "logit-affine",
        "parameters": {"slope": slope, "intercept": intercept},
        "approvedAt": "2026-09-01T00:00:00+00:00",
    }


def _receipts(
    market: str,
    *,
    baseline_probability: float,
    slope: float,
    intercept: float,
    count: int = 20,
) -> list[dict]:
    promoted = p49.calibrate_probability(
        baseline_probability, slope=slope, intercept=intercept
    )
    return [
        {
            "receiptId": f"p55-{market}-{idx:03d}",
            "releasedAt": f"2026-09-{(idx % 20) + 1:02d}T00:00:00+00:00",
            "grade": "win" if idx < count // 2 else "loss",
            "release": {
                "marketKey": market,
                "modelProbability": promoted,
                "fairMarketProbability": 0.55,
                "sourceModelVersion": f"p41-pricing-v1+{_candidate(market)}",
            },
            "result": {"probability": promoted},
        }
        for idx in range(count)
    ]


def main() -> int:
    from app import app

    with app.app_context():
        receipts_before = p44.list_receipts(limit=2000)
        events_before = p54.list_events(limit=200)
        production = p55.build_production_report()

        healthy_spread = p55.build_market_guard_report(
            _receipts(
                "spread",
                baseline_probability=0.8,
                slope=0.5,
                intercept=0.0,
            ),
            [_event("spread", 0.5, 0.0)],
            "spread",
            _champion("spread", 0.5, 0.0),
        )
        healthy_total = p55.build_market_guard_report(
            _receipts(
                "total",
                baseline_probability=0.8,
                slope=0.5,
                intercept=0.0,
            ),
            [_event("total", 0.5, 0.0)],
            "total",
            _champion("total", 0.5, 0.0),
        )
        regressed_spread = p55.build_market_guard_report(
            _receipts(
                "spread",
                baseline_probability=0.6,
                slope=1.5,
                intercept=0.3,
            ),
            [_event("spread", 1.5, 0.3)],
            "spread",
            _champion("spread", 1.5, 0.3),
        )
        collecting_total = p55.build_market_guard_report(
            _receipts(
                "total",
                baseline_probability=0.8,
                slope=0.5,
                intercept=0.0,
                count=10,
            ),
            [_event("total", 0.5, 0.0)],
            "total",
            _champion("total", 0.5, 0.0),
        )
        isolated = p55.build_guard_report(
            _receipts(
                "spread",
                baseline_probability=0.8,
                slope=0.5,
                intercept=0.0,
            )
            + _receipts(
                "total",
                baseline_probability=0.6,
                slope=1.5,
                intercept=0.3,
            ),
            [_event("spread", 0.5, 0.0), _event("total", 1.5, 0.3)],
            {
                "spread": _champion("spread", 0.5, 0.0),
                "total": _champion("total", 1.5, 0.3),
            },
        )

        receipts_after = p44.list_receipts(limit=2000)
        events_after = p54.list_events(limit=200)
        db.session.rollback()

    safety = production.get("safetyContract") or {}
    markets = production.get("markets") or {}
    valid_market_states = {"baseline", "collecting", "healthy", "rollback-review"}
    gates = {
        "production_state_valid": production.get("state")
        in {"baseline", "collecting", "healthy", "rollback-review"},
        "spread_state_valid": (markets.get("spread") or {}).get("state")
        in valid_market_states,
        "total_state_valid": (markets.get("total") or {}).get("state")
        in valid_market_states,
        "production_zero_credit": safety.get("providerRequests") == 0,
        "production_read_only": safety.get("readOnly") is True,
        "market_isolation_enabled": safety.get("marketIsolated") is True,
        "automatic_rollback_disabled": safety.get("automaticRollback") is False,
        "selected_side_changes_disabled": safety.get("changesSelectedSide") is False,
        "healthy_spread_kept": healthy_spread.get("state") == "healthy"
        and (healthy_spread.get("rollbackGate") or {}).get("recommended") is False,
        "healthy_total_kept": healthy_total.get("state") == "healthy"
        and (healthy_total.get("rollbackGate") or {}).get("recommended") is False,
        "regressed_spread_requires_human_rollback_review": regressed_spread.get("state")
        == "rollback-review"
        and (regressed_spread.get("rollbackGate") or {}).get("recommended") is True
        and (regressed_spread.get("rollbackGate") or {}).get("automaticRollback") is False,
        "low_sample_total_collects_safely": collecting_total.get("state") == "collecting"
        and (collecting_total.get("rollbackGate") or {}).get("recommended") is False,
        "one_market_can_fail_without_contaminating_other": isolated.get("state")
        == "rollback-review"
        and (isolated.get("markets") or {}).get("spread", {}).get("state") == "healthy"
        and (isolated.get("markets") or {}).get("total", {}).get("state")
        == "rollback-review"
        and isolated.get("rollbackReviewMarkets") == ["total"],
        "game_receipts_unchanged": receipts_before == receipts_after,
        "market_promotion_history_unchanged": events_before == events_after,
    }
    report = {
        "ok": all(gates.values()),
        "phase": "P5.5",
        "mode": "zero-credit-zero-write-market-champion-guard-verification",
        "gates": gates,
        "blockingFailures": [key for key, passed in gates.items() if not passed],
        "production": {
            "state": production.get("state"),
            "rollbackReviewMarkets": production.get("rollbackReviewMarkets"),
            "spreadState": (markets.get("spread") or {}).get("state"),
            "spreadSamples": (markets.get("spread") or {}).get("gradedSamples"),
            "totalState": (markets.get("total") or {}).get("state"),
            "totalSamples": (markets.get("total") or {}).get("gradedSamples"),
            "receiptCount": len(receipts_before),
            "promotionEventCount": len(events_before),
        },
    }
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
