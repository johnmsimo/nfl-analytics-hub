"""P6.2 canonical trust control plane for calibration governance.

P6.0 verifies the append-only calibration governance history and current
champions. P6.1 adds explicit owner attestations over audit-ready snapshots.
P6.2 combines those two layers into one deterministic, read-only trust posture
for operators and future product surfaces.

P6.2 never creates, promotes, rolls back, attests, or changes a model. Existing
P5.0/P5.4 mutation endpoints and the P6.1 owner-only attestation endpoint remain
the only write boundaries.
"""
from __future__ import annotations

from typing import Any

import p60_calibration_governance_audit as p60
import p61_calibration_governance_attestation as p61

MODEL_NAME = "p6.2-calibration-governance-trust-control-plane"
MODEL_VERSION = "p62-governance-trust-v1"

_VALID_ATTESTATION_STATES = {
    "unattested",
    "attested-current",
    "attestation-stale",
    "audit-degraded",
    "attestation-chain-degraded",
}


def build_control_plane(
    audit: dict[str, Any],
    attestation: dict[str, Any],
) -> dict[str, Any]:
    """Combine P6.0/P6.1 into one fail-closed governance trust decision."""
    audit_available = audit.get("available") is not False
    audit_ready = audit.get("ok") is True and audit.get("state") == "audit-ready"
    audit_digest = str(audit.get("portfolioDigest") or "")
    audit_digest_valid = len(audit_digest) == 64

    attestation_state = str(attestation.get("state") or "unavailable")
    attestation_state_valid = attestation_state in _VALID_ATTESTATION_STATES
    chain = (
        attestation.get("attestationChain")
        if isinstance(attestation.get("attestationChain"), dict)
        else {}
    )
    chain_ok = chain.get("ok") is True
    current_attestation = attestation.get("currentAttestation") is True
    attestation_ready = attestation.get("attestationReady") is True
    latest = (
        attestation.get("latestAttestation")
        if isinstance(attestation.get("latestAttestation"), dict)
        else None
    )

    blockers: list[str] = []
    if not audit_available:
        blockers.append("audit_unavailable")
    if not audit_ready:
        blockers.append("audit_not_ready")
    if not audit_digest_valid:
        blockers.append("audit_digest_invalid")
    if not attestation_state_valid:
        blockers.append("attestation_state_invalid")
    if not chain_ok:
        blockers.append("attestation_chain_invalid")

    if not audit_available:
        state = "unavailable"
        action = "RESTORE_AUDIT_AVAILABILITY"
        trust_level = "blocked"
        mutation_posture = "hold"
        message = "The calibration governance audit is unavailable. Hold governance mutations until audit visibility is restored."
    elif not audit_ready or not audit_digest_valid:
        state = "audit-degraded"
        action = "REPAIR_AUDIT_INTEGRITY"
        trust_level = "blocked"
        mutation_posture = "hold"
        message = "P6.0 audit integrity is degraded. Governance changes should remain on hold pending review."
    elif not attestation_state_valid or not chain_ok or attestation_state == "attestation-chain-degraded":
        state = "attestation-chain-degraded"
        action = "REVIEW_ATTESTATION_CHAIN"
        trust_level = "blocked"
        mutation_posture = "hold"
        message = "The P6.1 attestation chain is not trustworthy. Review the checkpoint chain before further governance changes."
    elif attestation_state == "attested-current" and current_attestation:
        state = "trusted"
        action = "KEEP_TRUSTED_GOVERNANCE"
        trust_level = "trusted"
        mutation_posture = "normal"
        message = "The P6.0 audit is healthy and the latest P6.1 owner attestation matches the current governance digest."
    elif attestation_state in {"unattested", "attestation-stale"} or attestation_ready:
        state = "attestation-required"
        action = "ATTEST_CURRENT_AUDIT"
        trust_level = "review"
        mutation_posture = "hold"
        message = "The audit is healthy, but the current governance snapshot has not been owner-attested."
    else:
        state = "review"
        action = "REVIEW_GOVERNANCE_TRUST"
        trust_level = "review"
        mutation_posture = "hold"
        message = "Governance trust is not in a canonical terminal state and requires operator review."

    latest_digest = str((latest or {}).get("portfolioDigest") or "") or None
    digest_matches_latest = bool(latest_digest and latest_digest == audit_digest)
    event_count_matches_latest = bool(
        latest
        and int((latest or {}).get("eventCount") or 0) == int(audit.get("eventCount") or 0)
    )
    trusted = bool(
        state == "trusted"
        and audit_ready
        and audit_digest_valid
        and chain_ok
        and current_attestation
        and digest_matches_latest
        and event_count_matches_latest
    )

    if state == "trusted" and not trusted:
        state = "review"
        action = "REVIEW_GOVERNANCE_TRUST"
        trust_level = "review"
        mutation_posture = "hold"
        message = "The attestation claims to be current but does not exactly match the live P6.0 digest/event count."
        blockers.append("current_attestation_mismatch")

    return {
        "available": audit_available and attestation.get("available") is not False,
        "model": MODEL_NAME,
        "modelVersion": MODEL_VERSION,
        "state": state,
        "trustLevel": trust_level,
        "trusted": trusted,
        "recommendedAction": action,
        "recommendedMutationPosture": mutation_posture,
        "message": message,
        "blockers": blockers,
        "audit": {
            "state": audit.get("state"),
            "ok": audit.get("ok"),
            "eventCount": audit.get("eventCount"),
            "portfolioDigest": audit.get("portfolioDigest"),
            "digestValid": audit_digest_valid,
            "failedChecks": ((audit.get("integrity") or {}).get("failedChecks") or [])
            if isinstance(audit.get("integrity"), dict)
            else [],
        },
        "attestation": {
            "state": attestation_state,
            "current": current_attestation,
            "ready": attestation_ready,
            "chainOk": chain_ok,
            "attestationCount": chain.get("attestationCount", 0),
            "headAttestationDigest": chain.get("headAttestationDigest"),
            "latestAttestationId": (latest or {}).get("attestationId"),
            "latestPortfolioDigest": latest_digest,
            "digestMatchesAudit": digest_matches_latest,
            "eventCountMatchesAudit": event_count_matches_latest,
        },
        "command": {
            "attest": {
                "endpoint": "/api/game-calibration/audit-attest",
                "allowed": state == "attestation-required" and attestation_ready,
                "confirmation": p61.ATTEST_CONFIRMATION,
                "ownerRoleRequired": True,
            }
        },
        "safetyContract": {
            "readOnly": True,
            "providerRequests": 0,
            "writesPromotionRegistries": False,
            "writesAttestationLedger": False,
            "createsMutationEndpoint": False,
            "automaticAttestation": False,
            "automaticPromotion": False,
            "automaticRollback": False,
            "changesModelProbabilities": False,
            "changesSelectedSide": False,
            "changesActionabilityThresholds": False,
            "changesBankrollPolicy": False,
            "placesBets": False,
        },
    }


def build_production_control_plane() -> dict[str, Any]:
    """Use one P6.0 snapshot for both audit and P6.1 status to avoid races."""
    audit = p60.build_production_report()
    attestation = p61.build_status(audit_report=audit)
    return build_control_plane(audit, attestation)
