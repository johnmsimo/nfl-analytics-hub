#!/usr/bin/env python3
"""P6.0 zero-credit, zero-write calibration governance audit verification."""
from __future__ import annotations

import json

from database import db
import p50_game_calibration_promotion as p50
import p54_game_market_calibration as p54
import p60_calibration_governance_audit as p60

FP = "a" * 64


def _moneyline(event_id: str, action: str, when: str) -> dict:
    return {
        "eventId": event_id,
        "action": action,
        "candidateId": "p49-p60-verification" if action == "promote" else None,
        "family": "logit-affine" if action == "promote" else "identity",
        "parameters": {"slope": 0.9, "intercept": 0.01}
        if action == "promote"
        else {"slope": None, "intercept": None},
        "baseModelVersion": "p40-transparent-v1",
        "approvedBy": "p60-verifier",
        "governanceFingerprint": FP,
        "createdAt": when,
    }


def _market(market: str, event_id: str, action: str, when: str) -> dict:
    candidate = "p54-sp-p60-verification" if market == "spread" else "p54-to-p60-verification"
    return {
        "eventId": event_id,
        "market": market,
        "action": action,
        "candidateId": candidate if action == "promote" else None,
        "family": "logit-affine" if action == "promote" else "identity",
        "parameters": {"slope": 0.9, "intercept": 0.01}
        if action == "promote"
        else {"slope": None, "intercept": None},
        "baseModelVersion": "p41-pricing-v1",
        "approvedBy": "p60-verifier",
        "governanceFingerprint": FP,
        "createdAt": when,
    }


def _champions() -> dict:
    return {
        "moneyline": {
            "available": True,
            "state": "baseline",
            "applied": False,
            "candidateId": None,
        },
        "spread": {
            "available": True,
            "state": "promoted",
            "applied": True,
            "candidateId": "p54-sp-p60-verification",
        },
        "total": {
            "available": True,
            "state": "promoted",
            "applied": True,
            "candidateId": "p54-to-p60-verification",
        },
    }


def main() -> int:
    from app import app

    with app.app_context():
        moneyline_before = p50.list_events(limit=100)
        market_before = p54.list_events(limit=200)
        production = p60.build_production_report()

        synthetic = p60.build_audit_report(
            [
                _moneyline("p50-p60-a", "promote", "2026-09-01T00:00:00+00:00"),
                _moneyline("p50-p60-b", "rollback", "2026-09-04T00:00:00+00:00"),
            ],
            [
                _market("spread", "p54-p60-s", "promote", "2026-09-02T00:00:00+00:00"),
                _market("total", "p54-p60-t", "promote", "2026-09-03T00:00:00+00:00"),
            ],
            _champions(),
        )
        malformed = p60.build_audit_report(
            [
                {
                    **_moneyline(
                        "p50-p60-bad",
                        "promote",
                        "2026-09-01T00:00:00+00:00",
                    ),
                    "governanceFingerprint": "bad",
                }
            ],
            [],
            {
                "moneyline": {
                    "available": True,
                    "state": "promoted",
                    "applied": True,
                    "candidateId": "p49-p60-verification",
                },
                "spread": {
                    "available": True,
                    "state": "baseline",
                    "applied": False,
                    "candidateId": None,
                },
                "total": {
                    "available": True,
                    "state": "baseline",
                    "applied": False,
                    "candidateId": None,
                },
            },
        )

        moneyline_after = p50.list_events(limit=100)
        market_after = p54.list_events(limit=200)
        db.session.rollback()

    safety = production.get("safetyContract") or {}
    integrity = production.get("integrity") or {}
    gates = {
        "production_audit_available": production.get("available") is True,
        "production_state_valid": production.get("state")
        in {"audit-ready", "audit-degraded"},
        "production_integrity_shape_valid": isinstance(integrity.get("checks"), dict),
        "portfolio_digest_present": len(str(production.get("portfolioDigest") or "")) == 64,
        "all_three_market_summaries_present": set((production.get("markets") or {}).keys())
        == {"moneyline", "spread", "total"},
        "production_zero_credit": safety.get("providerRequests") == 0,
        "production_read_only": safety.get("readOnly") is True,
        "no_new_mutation_endpoint": safety.get("createsMutationEndpoint") is False,
        "automatic_promotion_disabled": safety.get("automaticPromotion") is False,
        "automatic_rollback_disabled": safety.get("automaticRollback") is False,
        "synthetic_valid_history_passes": synthetic.get("ok") is True
        and synthetic.get("state") == "audit-ready",
        "synthetic_history_is_globally_ordered": [
            row.get("eventId") for row in synthetic.get("events") or []
        ]
        == ["p50-p60-a", "p54-p60-s", "p54-p60-t", "p50-p60-b"],
        "malformed_governance_is_detected": malformed.get("ok") is False
        and "governanceFingerprintsWellFormed"
        in ((malformed.get("integrity") or {}).get("failedChecks") or []),
        "moneyline_history_unchanged": moneyline_before == moneyline_after,
        "market_history_unchanged": market_before == market_after,
    }
    report = {
        "ok": all(gates.values()),
        "phase": "P6.0",
        "mode": "zero-credit-zero-write-calibration-governance-audit-verification",
        "gates": gates,
        "blockingFailures": [key for key, passed in gates.items() if not passed],
        "production": {
            "state": production.get("state"),
            "integrityOk": integrity.get("ok"),
            "eventCount": production.get("eventCount"),
            "portfolioDigest": production.get("portfolioDigest"),
            "moneylineEvents": len(moneyline_before),
            "marketEvents": len(market_before),
        },
    }
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
