from __future__ import annotations

from datetime import UTC, datetime

import p61_calibration_governance_attestation as p61


def _audit(*, digest: str = "a" * 64, event_count: int = 3, ok: bool = True) -> dict:
    return {
        "available": True,
        "model": "p6.0-calibration-governance-audit-ledger",
        "modelVersion": "p60-governance-audit-v1",
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
                "derivedCandidateId": "p54-sp-test",
                "latestEventId": "p54-sp-event",
            },
            "total": {
                "derivedState": "baseline",
                "derivedCandidateId": None,
                "latestEventId": None,
            },
        },
    }


def test_p61_status_is_unattested_when_audit_is_ready_and_history_empty():
    status = p61.build_status(_audit(), [])
    assert status["available"] is True
    assert status["ledgerAvailable"] is True
    assert status["state"] == "unattested"
    assert status["attestationReady"] is True
    assert status["command"]["allowed"] is True
    assert status["command"]["confirmation"] == p61.ATTEST_CONFIRMATION
    assert status["safetyContract"]["automaticAttestation"] is False


def test_p61_rejects_attestation_when_p60_audit_is_degraded():
    result = p61.attest_current_audit(
        confirmation=p61.ATTEST_CONFIRMATION,
        actor="owner",
        persist=False,
        audit_report=_audit(ok=False),
        existing_rows=[],
    )
    assert result["ok"] is False
    assert result["code"] == "AUDIT_NOT_READY"


def test_p61_requires_exact_confirmation():
    result = p61.attest_current_audit(
        confirmation="YES",
        actor="owner",
        persist=False,
        audit_report=_audit(),
        existing_rows=[],
    )
    assert result["ok"] is False
    assert result["code"] == "CONFIRMATION_REQUIRED"


def test_p61_dry_run_attestation_captures_audit_digest_and_champions():
    result = p61.attest_current_audit(
        confirmation=p61.ATTEST_CONFIRMATION,
        actor="owner",
        persist=False,
        audit_report=_audit(),
        existing_rows=[],
    )
    assert result["ok"] is True
    assert result["dryRun"] is True
    row = result["attestation"]
    assert row["portfolioDigest"] == "a" * 64
    assert row["eventCount"] == 3
    assert row["championSnapshot"]["spread"]["candidateId"] == "p54-sp-test"
    assert row["previousAttestationDigest"] is None
    assert len(row["attestationDigest"]) == 64


def test_p61_current_checkpoint_is_idempotent():
    first = p61.attest_current_audit(
        confirmation=p61.ATTEST_CONFIRMATION,
        actor="owner",
        persist=False,
        audit_report=_audit(),
        existing_rows=[],
    )["attestation"]
    second = p61.attest_current_audit(
        confirmation=p61.ATTEST_CONFIRMATION,
        actor="owner",
        persist=False,
        audit_report=_audit(),
        existing_rows=[first],
    )
    assert second["ok"] is True
    assert second["idempotent"] is True
    status = p61.build_status(_audit(), [first])
    assert status["state"] == "attested-current"
    assert status["currentAttestation"] is True
    assert status["attestationReady"] is False


def test_p61_new_audit_digest_marks_previous_attestation_stale():
    first = p61.attest_current_audit(
        confirmation=p61.ATTEST_CONFIRMATION,
        actor="owner",
        persist=False,
        audit_report=_audit(),
        existing_rows=[],
    )["attestation"]
    status = p61.build_status(_audit(digest="b" * 64, event_count=4), [first])
    assert status["state"] == "attestation-stale"
    assert status["attestationReady"] is True
    assert status["currentAttestation"] is False


def test_p61_hash_chain_detects_tampering():
    created = datetime(2026, 9, 1, tzinfo=UTC)
    champion = {
        "moneyline": {"state": "baseline", "candidateId": None, "latestEventId": None},
        "spread": {"state": "baseline", "candidateId": None, "latestEventId": None},
        "total": {"state": "baseline", "candidateId": None, "latestEventId": None},
    }
    integrity = {"ok": True, "checks": {}, "failedChecks": []}
    digest = p61._attestation_digest(  # noqa: SLF001 - deterministic chain regression
        portfolio_digest="a" * 64,
        event_count=0,
        champion_snapshot=champion,
        integrity_snapshot=integrity,
        previous_digest=None,
        actor="owner",
        created_at=created,
    )
    row = {
        "attestationId": "p61-test",
        "portfolioDigest": "a" * 64,
        "eventCount": 0,
        "auditModelVersion": "p60-governance-audit-v1",
        "auditState": "audit-ready",
        "championSnapshot": champion,
        "integritySnapshot": integrity,
        "previousAttestationDigest": None,
        "attestationDigest": digest,
        "attestedBy": "owner",
        "createdAt": created.isoformat(),
    }
    assert p61.verify_attestation_chain([row])["ok"] is True
    row["portfolioDigest"] = "b" * 64
    result = p61.verify_attestation_chain([row])
    assert result["ok"] is False
    assert any("attestation_digest_mismatch" in error for error in result["errors"])


def test_p61_ledger_read_failure_fails_closed(monkeypatch):
    monkeypatch.setattr(
        p61,
        "_load_attestations",
        lambda limit=p61.ATTESTATION_LIMIT: {
            "available": False,
            "rows": [],
            "error": "ATTESTATION_LEDGER_READ_FAILED",
        },
    )
    status = p61.build_status(_audit())
    assert status["available"] is False
    assert status["ledgerAvailable"] is False
    assert status["state"] == "attestation-chain-degraded"
    assert status["attestationReady"] is False
    assert status["currentAttestation"] is False
    assert status["attestationLedger"]["error"] == "ATTESTATION_LEDGER_READ_FAILED"
    assert status["attestationChain"]["ok"] is False
    assert status["command"]["allowed"] is False

    result = p61.attest_current_audit(
        confirmation=p61.ATTEST_CONFIRMATION,
        actor="owner",
        persist=False,
        audit_report=_audit(),
    )
    assert result["ok"] is False
    assert result["code"] == "ATTESTATION_LEDGER_UNAVAILABLE"


def test_p61_status_route_is_read_only(client, monkeypatch):
    monkeypatch.setattr(
        p61,
        "build_status",
        lambda: {
            "available": True,
            "ledgerAvailable": True,
            "state": "unattested",
            "attestationReady": True,
            "safetyContract": {
                "providerRequests": 0,
                "automaticAttestation": False,
                "automaticPromotion": False,
                "automaticRollback": False,
            },
        },
    )
    response = client.get("/api/game-calibration/audit-attestations")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["state"] == "unattested"
    assert payload["safetyContract"]["providerRequests"] == 0
    assert payload["safetyContract"]["automaticAttestation"] is False
