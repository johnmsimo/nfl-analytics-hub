from __future__ import annotations

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
            "attestationId": "p61-test",
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
            "errors": [] if chain_ok else ["attestation_digest_mismatch:p61-test"],
        },
    }


def test_p62_trusted_only_when_current_attestation_exactly_matches_audit():
    report = p62.build_control_plane(
        _audit(),
        _attestation("attested-current", current=True),
    )
    assert report["state"] == "trusted"
    assert report["trusted"] is True
    assert report["trustLevel"] == "trusted"
    assert report["recommendedAction"] == "KEEP_TRUSTED_GOVERNANCE"
    assert report["recommendedMutationPosture"] == "normal"
    assert report["attestation"]["digestMatchesAudit"] is True
    assert report["attestation"]["eventCountMatchesAudit"] is True


def test_p62_unattested_audit_requires_owner_attestation():
    report = p62.build_control_plane(
        _audit(),
        _attestation("unattested", ready=True),
    )
    assert report["state"] == "attestation-required"
    assert report["trusted"] is False
    assert report["recommendedAction"] == "ATTEST_CURRENT_AUDIT"
    assert report["recommendedMutationPosture"] == "hold"
    assert report["command"]["attest"]["allowed"] is True


def test_p62_stale_checkpoint_requires_new_attestation():
    report = p62.build_control_plane(
        _audit(digest="c" * 64, event_count=4),
        _attestation(
            "attestation-stale",
            ready=True,
            digest=DIGEST,
            event_count=3,
        ),
    )
    assert report["state"] == "attestation-required"
    assert report["attestation"]["digestMatchesAudit"] is False
    assert report["command"]["attest"]["allowed"] is True


def test_p62_degraded_audit_blocks_governance_trust():
    report = p62.build_control_plane(
        _audit(ready=False),
        _attestation("audit-degraded", chain_ok=True),
    )
    assert report["state"] == "audit-degraded"
    assert report["trustLevel"] == "blocked"
    assert report["recommendedAction"] == "REPAIR_AUDIT_INTEGRITY"
    assert report["recommendedMutationPosture"] == "hold"
    assert "audit_not_ready" in report["blockers"]
    assert report["command"]["attest"]["allowed"] is False


def test_p62_broken_attestation_chain_blocks_trust():
    report = p62.build_control_plane(
        _audit(),
        _attestation("attestation-chain-degraded", chain_ok=False),
    )
    assert report["state"] == "attestation-chain-degraded"
    assert report["trustLevel"] == "blocked"
    assert report["recommendedAction"] == "REVIEW_ATTESTATION_CHAIN"
    assert "attestation_chain_invalid" in report["blockers"]


def test_p62_fails_closed_when_current_claim_does_not_match_live_audit():
    report = p62.build_control_plane(
        _audit(digest="d" * 64, event_count=4),
        _attestation(
            "attested-current",
            current=True,
            digest=DIGEST,
            event_count=3,
        ),
    )
    assert report["state"] == "review"
    assert report["trusted"] is False
    assert report["recommendedMutationPosture"] == "hold"
    assert "current_attestation_mismatch" in report["blockers"]


def test_p62_invalid_audit_digest_is_fail_closed():
    report = p62.build_control_plane(
        _audit(digest="bad"),
        _attestation("unattested", ready=True),
    )
    assert report["state"] == "audit-degraded"
    assert report["trusted"] is False
    assert "audit_digest_invalid" in report["blockers"]


def test_p62_route_exposes_read_only_trust_posture(client, monkeypatch):
    monkeypatch.setattr(
        p62,
        "build_production_control_plane",
        lambda: {
            "available": True,
            "state": "trusted",
            "trustLevel": "trusted",
            "trusted": True,
            "recommendedAction": "KEEP_TRUSTED_GOVERNANCE",
            "recommendedMutationPosture": "normal",
            "safetyContract": {
                "readOnly": True,
                "providerRequests": 0,
                "writesPromotionRegistries": False,
                "writesAttestationLedger": False,
                "createsMutationEndpoint": False,
                "automaticAttestation": False,
                "automaticPromotion": False,
                "automaticRollback": False,
            },
        },
    )
    response = client.get("/api/game-calibration/governance-trust")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["state"] == "trusted"
    assert payload["trusted"] is True
    assert payload["safetyContract"]["readOnly"] is True
    assert payload["safetyContract"]["providerRequests"] == 0
    assert payload["safetyContract"]["createsMutationEndpoint"] is False
