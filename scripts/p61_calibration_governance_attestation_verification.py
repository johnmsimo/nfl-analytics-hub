#!/usr/bin/env python3
"""P6.1 zero-credit, zero-write governance attestation verification."""
from __future__ import annotations

import json

from database import db
import p50_game_calibration_promotion as p50
import p54_game_market_calibration as p54
import p60_calibration_governance_audit as p60
import p61_calibration_governance_attestation as p61


def _audit(digest: str = "a" * 64, event_count: int = 3, ok: bool = True) -> dict:
    return {
        "available": True,
        "modelVersion": p60.MODEL_VERSION,
        "state": "audit-ready" if ok else "audit-degraded",
        "ok": ok,
        "eventCount": event_count,
        "portfolioDigest": digest,
        "integrity": {
            "ok": ok,
            "checks": {"liveChampionsMatchHistory": ok},
            "failedChecks": [] if ok else ["liveChampionsMatchHistory"],
        },
        "markets": {
            "moneyline": {
                "derivedState": "baseline",
                "derivedCandidateId": None,
                "latestEventId": None,
            },
            "spread": {
                "derivedState": "promoted",
                "derivedCandidateId": "p54-sp-p61-verification",
                "latestEventId": "p54-sp-p61-event",
            },
            "total": {
                "derivedState": "baseline",
                "derivedCandidateId": None,
                "latestEventId": None,
            },
        },
    }


def main() -> int:
    from app import app

    with app.app_context():
        moneyline_before = p50.list_events(limit=100)
        market_before = p54.list_events(limit=200)
        attestations_before = p61.list_attestations(limit=100)
        production_audit = p60.build_production_report()
        production = p61.build_status(production_audit, attestations_before)

        synthetic_first = p61.attest_current_audit(
            confirmation=p61.ATTEST_CONFIRMATION,
            actor="p61-verifier",
            persist=False,
            audit_report=_audit(),
            existing_rows=[],
        )
        first_row = synthetic_first.get("attestation") or {}
        synthetic_current = p61.build_status(_audit(), [first_row])
        synthetic_stale = p61.build_status(_audit("b" * 64, 4), [first_row])
        synthetic_degraded = p61.attest_current_audit(
            confirmation=p61.ATTEST_CONFIRMATION,
            actor="p61-verifier",
            persist=False,
            audit_report=_audit(ok=False),
            existing_rows=[],
        )
        bad_confirmation = p61.attest_current_audit(
            confirmation="NO",
            actor="p61-verifier",
            persist=False,
            audit_report=_audit(),
            existing_rows=[],
        )

        moneyline_after = p50.list_events(limit=100)
        market_after = p54.list_events(limit=200)
        attestations_after = p61.list_attestations(limit=100)
        db.session.rollback()

    safety = production.get("safetyContract") or {}
    valid_states = {
        "audit-degraded",
        "attestation-chain-degraded",
        "attested-current",
        "attestation-stale",
        "unattested",
    }
    production_requires_action = production.get("state") in {"unattested", "attestation-stale"}
    dry_run_ok = synthetic_first.get("ok") is True and synthetic_first.get("dryRun") is True
    gates = {
        "production_state_valid": production.get("state") in valid_states,
        "production_zero_credit": safety.get("providerRequests") == 0,
        "promotion_registries_write_disabled": safety.get("writesPromotionRegistries") is False,
        "automatic_attestation_disabled": safety.get("automaticAttestation") is False,
        "automatic_promotion_disabled": safety.get("automaticPromotion") is False,
        "automatic_rollback_disabled": safety.get("automaticRollback") is False,
        "synthetic_dry_run_attestation_passes": dry_run_ok
        and len(str(first_row.get("attestationDigest") or "")) == 64,
        "synthetic_current_attestation_detected": synthetic_current.get("state") == "attested-current"
        and synthetic_current.get("currentAttestation") is True,
        "synthetic_new_audit_marks_checkpoint_stale": synthetic_stale.get("state") == "attestation-stale"
        and synthetic_stale.get("attestationReady") is True,
        "degraded_audit_cannot_be_attested": synthetic_degraded.get("code") == "AUDIT_NOT_READY",
        "invalid_confirmation_rejected": bad_confirmation.get("code") == "CONFIRMATION_REQUIRED",
        "moneyline_history_unchanged": moneyline_before == moneyline_after,
        "market_history_unchanged": market_before == market_after,
        "attestation_history_unchanged": attestations_before == attestations_after,
    }
    report = {
        "ok": all(gates.values()),
        "phase": "P6.1",
        "mode": "zero-credit-zero-write-governance-attestation-verification",
        "gates": gates,
        "blockingFailures": [key for key, passed in gates.items() if not passed],
        "production": {
            "state": production.get("state"),
            "attestationReady": production.get("attestationReady"),
            "requiresOwnerAttestation": production_requires_action,
            "auditState": (production.get("audit") or {}).get("state"),
            "portfolioDigest": (production.get("audit") or {}).get("portfolioDigest"),
            "attestationCount": (production.get("attestationChain") or {}).get("attestationCount"),
            "attestationChainOk": (production.get("attestationChain") or {}).get("ok"),
        },
    }
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
