#!/usr/bin/env python3
"""P6.2 zero-credit, zero-write governance trust control-plane verification."""
from __future__ import annotations

import json

from database import db
import p50_game_calibration_promotion as p50
import p54_game_market_calibration as p54
import p60_calibration_governance_audit as p60
import p61_calibration_governance_attestation as p61
import p62_calibration_governance_trust_control_plane as p62

DIGEST = "a" * 64


def _audit(*, ready: bool = True, digest: str = DIGEST, event_count: int = 3) -> dict:
    return {
        "available": True,
        "state": "audit-ready" if ready else "audit-degraded",
        "ok": ready,
        "eventCount": event_count,
        "portfolioDigest": digest,
        "integrity": {
            "ok": ready,
            "checks": {"liveChampionsMatchHistory": ready},
            "failedChecks": [] if ready else ["liveChampionsMatchHistory"],
        },
    }


def _attestation(
    state: str,
    *,
    current: bool = False,
    ready: bool = False,
    chain_ok: bool = True,
    digest: str = DIGEST,
    event_count: int = 3,
) -> dict:
    latest = None
    if state != "unattested":
        latest = {
            "attestationId": "p61-p62-verification",
            "portfolioDigest": digest,
            "eventCount": event_count,
            "attestationDigest": "b" * 64,
        }
    return {
        "available": True,
        "state": state,
        "attestationReady": ready,
        "currentAttestation": current,
        "latestAttestation": latest,
        "attestationChain": {
            "ok": chain_ok,
            "attestationCount": 0 if latest is None else 1,
            "headAttestationDigest": None if latest is None else "b" * 64,
            "errors": [] if chain_ok else ["attestation_digest_mismatch:p61-p62-verification"],
        },
    }


def main() -> int:
    from app import app

    with app.app_context():
        moneyline_before = p50.list_events(limit=100)
        market_before = p54.list_events(limit=200)
        attestations_before = p61.list_attestations(limit=100)
        production = p62.build_production_control_plane()

        trusted = p62.build_control_plane(
            _audit(),
            _attestation("attested-current", current=True),
        )
        unattested = p62.build_control_plane(
            _audit(),
            _attestation("unattested", ready=True),
        )
        stale = p62.build_control_plane(
            _audit(digest="c" * 64, event_count=4),
            _attestation("attestation-stale", ready=True, digest=DIGEST, event_count=3),
        )
        degraded = p62.build_control_plane(
            _audit(ready=False),
            _attestation("audit-degraded"),
        )
        chain_degraded = p62.build_control_plane(
            _audit(),
            _attestation("attestation-chain-degraded", chain_ok=False),
        )
        mismatch = p62.build_control_plane(
            _audit(digest="d" * 64, event_count=4),
            _attestation("attested-current", current=True, digest=DIGEST, event_count=3),
        )

        moneyline_after = p50.list_events(limit=100)
        market_after = p54.list_events(limit=200)
        attestations_after = p61.list_attestations(limit=100)
        db.session.rollback()

    safety = production.get("safetyContract") or {}
    valid_states = {
        "unavailable",
        "audit-degraded",
        "attestation-chain-degraded",
        "trusted",
        "attestation-required",
        "review",
    }
    gates = {
        "production_state_valid": production.get("state") in valid_states,
        "production_zero_credit": safety.get("providerRequests") == 0,
        "production_read_only": safety.get("readOnly") is True,
        "promotion_registry_writes_disabled": safety.get("writesPromotionRegistries") is False,
        "attestation_writes_disabled": safety.get("writesAttestationLedger") is False,
        "no_new_mutation_endpoint": safety.get("createsMutationEndpoint") is False,
        "automatic_attestation_disabled": safety.get("automaticAttestation") is False,
        "automatic_promotion_disabled": safety.get("automaticPromotion") is False,
        "automatic_rollback_disabled": safety.get("automaticRollback") is False,
        "synthetic_current_attestation_is_trusted": trusted.get("state") == "trusted"
        and trusted.get("trusted") is True
        and trusted.get("recommendedMutationPosture") == "normal",
        "synthetic_unattested_requires_attestation": unattested.get("state") == "attestation-required"
        and unattested.get("trusted") is False
        and (unattested.get("command") or {}).get("attest", {}).get("allowed") is True,
        "synthetic_stale_requires_attestation": stale.get("state") == "attestation-required"
        and stale.get("recommendedMutationPosture") == "hold",
        "synthetic_degraded_audit_blocks_trust": degraded.get("state") == "audit-degraded"
        and degraded.get("trustLevel") == "blocked",
        "synthetic_broken_chain_blocks_trust": chain_degraded.get("state") == "attestation-chain-degraded"
        and chain_degraded.get("trustLevel") == "blocked",
        "synthetic_current_mismatch_fails_closed": mismatch.get("state") == "review"
        and mismatch.get("trusted") is False
        and "current_attestation_mismatch" in (mismatch.get("blockers") or []),
        "moneyline_history_unchanged": moneyline_before == moneyline_after,
        "market_history_unchanged": market_before == market_after,
        "attestation_history_unchanged": attestations_before == attestations_after,
    }
    report = {
        "ok": all(gates.values()),
        "phase": "P6.2",
        "mode": "zero-credit-zero-write-governance-trust-control-plane-verification",
        "gates": gates,
        "blockingFailures": [key for key, passed in gates.items() if not passed],
        "production": {
            "state": production.get("state"),
            "trustLevel": production.get("trustLevel"),
            "trusted": production.get("trusted"),
            "recommendedAction": production.get("recommendedAction"),
            "recommendedMutationPosture": production.get("recommendedMutationPosture"),
            "auditState": (production.get("audit") or {}).get("state"),
            "attestationState": (production.get("attestation") or {}).get("state"),
            "attestationCount": (production.get("attestation") or {}).get("attestationCount"),
        },
    }
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
