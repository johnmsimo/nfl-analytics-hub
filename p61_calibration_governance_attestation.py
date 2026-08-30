"""P6.1 owner-attested checkpoints for the P6.0 governance audit.

P6.0 computes a deterministic, read-only audit across the append-only P5.0 and
P5.4 calibration registries. P6.1 lets an owner explicitly attest an audit-ready
snapshot into a separate append-only checkpoint ledger. Checkpoints never alter
calibration champions, model probabilities, actionability, bankroll policy, or
wager execution.

Each attestation is hash-chained to the previous attestation so later reads can
verify both the P6.0 portfolio digest and the checkpoint sequence itself.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

from database import db
import p60_calibration_governance_audit as p60

MODEL_NAME = "p6.1-calibration-governance-attestation"
MODEL_VERSION = "p61-governance-attestation-v1"
ATTEST_CONFIRMATION = "ATTEST_CALIBRATION_GOVERNANCE"
ATTESTATION_LIMIT = 100

attestations = sa.Table(
    "calibration_governance_attestations",
    db.metadata,
    sa.Column("attestation_id", sa.String(32), primary_key=True),
    sa.Column("portfolio_digest", sa.String(64), nullable=False, index=True),
    sa.Column("event_count", sa.Integer, nullable=False),
    sa.Column("audit_model_version", sa.String(64), nullable=False),
    sa.Column("audit_state", sa.String(32), nullable=False),
    sa.Column("champion_snapshot", sa.JSON, nullable=False),
    sa.Column("integrity_snapshot", sa.JSON, nullable=False),
    sa.Column("previous_attestation_digest", sa.String(64)),
    sa.Column("attestation_digest", sa.String(64), nullable=False, unique=True),
    sa.Column("attested_by", sa.String(128), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
)


def _now() -> datetime:
    return datetime.now(UTC)


def _rollback() -> None:
    try:
        db.session.rollback()
    except Exception:  # noqa: BLE001
        pass


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _timestamp_token(value: datetime) -> str:
    """Canonicalize aware/naive DB timestamps to the same UTC digest token."""
    normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return normalized.replace(tzinfo=None).isoformat(timespec="microseconds") + "Z"


def _champion_snapshot(report: dict[str, Any]) -> dict[str, Any]:
    markets = report.get("markets") if isinstance(report.get("markets"), dict) else {}
    return {
        market: {
            "state": (markets.get(market) or {}).get("derivedState"),
            "candidateId": (markets.get(market) or {}).get("derivedCandidateId"),
            "latestEventId": (markets.get(market) or {}).get("latestEventId"),
        }
        for market in p60.MARKETS
    }


def _attestation_digest(
    *,
    portfolio_digest: str,
    event_count: int,
    champion_snapshot: dict[str, Any],
    integrity_snapshot: dict[str, Any],
    previous_digest: str | None,
    actor: str,
    created_at: datetime,
) -> str:
    payload = {
        "portfolioDigest": portfolio_digest,
        "eventCount": int(event_count),
        "championSnapshot": champion_snapshot,
        "integritySnapshot": integrity_snapshot,
        "previousAttestationDigest": previous_digest,
        "attestedBy": actor,
        "createdAt": _timestamp_token(created_at),
        "modelVersion": MODEL_VERSION,
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _public(row: dict[str, Any]) -> dict[str, Any]:
    created_at = row.get("created_at")
    return {
        "attestationId": row.get("attestation_id"),
        "portfolioDigest": row.get("portfolio_digest"),
        "eventCount": row.get("event_count"),
        "auditModelVersion": row.get("audit_model_version"),
        "auditState": row.get("audit_state"),
        "championSnapshot": row.get("champion_snapshot") or {},
        "integritySnapshot": row.get("integrity_snapshot") or {},
        "previousAttestationDigest": row.get("previous_attestation_digest"),
        "attestationDigest": row.get("attestation_digest"),
        "attestedBy": row.get("attested_by"),
        "createdAt": created_at.isoformat() if created_at else None,
    }


def _load_attestations(limit: int = ATTESTATION_LIMIT) -> dict[str, Any]:
    """Read checkpoints while preserving whether the ledger itself was readable."""
    stmt = (
        sa.select(attestations)
        .order_by(attestations.c.created_at.desc(), attestations.c.attestation_id.desc())
        .limit(max(1, min(int(limit), ATTESTATION_LIMIT)))
    )
    try:
        rows = db.session.execute(stmt).mappings().all()
    except (RuntimeError, SQLAlchemyError):
        _rollback()
        return {
            "available": False,
            "rows": [],
            "error": "ATTESTATION_LEDGER_READ_FAILED",
        }
    return {
        "available": True,
        "rows": [_public(dict(row)) for row in rows],
        "error": None,
    }


def list_attestations(limit: int = ATTESTATION_LIMIT) -> list[dict[str, Any]]:
    """Compatibility list API; build_status preserves ledger-read availability."""
    return list(_load_attestations(limit).get("rows") or [])


def verify_attestation_chain(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Verify the newest-first public attestation list as an append-only hash chain."""
    ordered = sorted(
        rows,
        key=lambda row: (
            str(row.get("createdAt") or ""),
            str(row.get("attestationId") or ""),
        ),
    )
    errors: list[str] = []
    previous: str | None = None
    for row in ordered:
        observed_previous = row.get("previousAttestationDigest")
        if str(observed_previous or "") != str(previous or ""):
            errors.append(f"previous_digest_mismatch:{row.get('attestationId')}")
        created = str(row.get("createdAt") or "")
        try:
            created_at = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError:
            errors.append(f"invalid_timestamp:{row.get('attestationId')}")
            previous = str(row.get("attestationDigest") or "") or None
            continue
        expected = _attestation_digest(
            portfolio_digest=str(row.get("portfolioDigest") or ""),
            event_count=int(row.get("eventCount") or 0),
            champion_snapshot=row.get("championSnapshot") or {},
            integrity_snapshot=row.get("integritySnapshot") or {},
            previous_digest=previous,
            actor=str(row.get("attestedBy") or ""),
            created_at=created_at,
        )
        if expected != row.get("attestationDigest"):
            errors.append(f"attestation_digest_mismatch:{row.get('attestationId')}")
        previous = str(row.get("attestationDigest") or "") or None
    return {
        "ok": not errors,
        "attestationCount": len(ordered),
        "headAttestationDigest": previous,
        "errors": errors,
    }


def build_status(
    audit_report: dict[str, Any] | None = None,
    rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    audit = audit_report or p60.build_production_report()
    if rows is None:
        ledger = _load_attestations()
        history = list(ledger.get("rows") or [])
        ledger_available = ledger.get("available") is True
        ledger_error = ledger.get("error")
    else:
        history = list(rows)
        ledger_available = True
        ledger_error = None

    chain = (
        verify_attestation_chain(history)
        if ledger_available
        else {
            "ok": False,
            "attestationCount": 0,
            "headAttestationDigest": None,
            "errors": ["attestation_ledger_unavailable"],
        }
    )
    latest = history[0] if history else None
    audit_ready = audit.get("ok") is True and audit.get("state") == "audit-ready"
    current_digest = str(audit.get("portfolioDigest") or "")
    checkpoint_current = bool(
        ledger_available
        and latest
        and chain.get("ok") is True
        and str(latest.get("portfolioDigest") or "") == current_digest
        and int(latest.get("eventCount") or 0) == int(audit.get("eventCount") or 0)
    )
    if not audit_ready:
        state = "audit-degraded"
        recommendation = "REPAIR_AUDIT_BEFORE_ATTESTATION"
    elif not ledger_available:
        state = "attestation-chain-degraded"
        recommendation = "RESTORE_ATTESTATION_LEDGER"
    elif not chain.get("ok"):
        state = "attestation-chain-degraded"
        recommendation = "REVIEW_ATTESTATION_CHAIN"
    elif checkpoint_current:
        state = "attested-current"
        recommendation = "KEEP_CURRENT_ATTESTATION"
    elif history:
        state = "attestation-stale"
        recommendation = "ATTEST_CURRENT_AUDIT"
    else:
        state = "unattested"
        recommendation = "ATTEST_CURRENT_AUDIT"

    attestation_ready = bool(
        audit_ready
        and ledger_available
        and chain.get("ok") is True
        and not checkpoint_current
    )
    return {
        "available": ledger_available,
        "ledgerAvailable": ledger_available,
        "model": MODEL_NAME,
        "modelVersion": MODEL_VERSION,
        "state": state,
        "recommendation": recommendation,
        "audit": {
            "state": audit.get("state"),
            "ok": audit.get("ok"),
            "eventCount": audit.get("eventCount"),
            "portfolioDigest": audit.get("portfolioDigest"),
            "modelVersion": audit.get("modelVersion"),
        },
        "attestationReady": attestation_ready,
        "currentAttestation": checkpoint_current,
        "latestAttestation": latest,
        "attestationChain": chain,
        "attestationLedger": {
            "available": ledger_available,
            "error": ledger_error,
        },
        "attestations": history,
        "command": {
            "endpoint": "/api/game-calibration/audit-attest",
            "allowed": attestation_ready,
            "confirmation": ATTEST_CONFIRMATION,
            "ownerRoleRequired": True,
        },
        "safetyContract": {
            "providerRequests": 0,
            "writesPromotionRegistries": False,
            "writesAttestationLedgerOnlyOnExplicitOwnerAction": True,
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


def attest_current_audit(
    *,
    confirmation: str,
    actor: str,
    persist: bool = True,
    audit_report: dict[str, Any] | None = None,
    existing_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if confirmation != ATTEST_CONFIRMATION:
        return {
            "ok": False,
            "code": "CONFIRMATION_REQUIRED",
            "required": ATTEST_CONFIRMATION,
        }
    actor = str(actor or "").strip()
    if not actor:
        return {"ok": False, "code": "ACTOR_REQUIRED"}
    audit = audit_report or p60.build_production_report()
    if audit.get("ok") is not True or audit.get("state") != "audit-ready":
        return {"ok": False, "code": "AUDIT_NOT_READY", "audit": audit}

    if existing_rows is None:
        ledger = _load_attestations()
        if ledger.get("available") is not True:
            return {
                "ok": False,
                "code": "ATTESTATION_LEDGER_UNAVAILABLE",
                "error": ledger.get("error"),
            }
        rows = list(ledger.get("rows") or [])
    else:
        rows = list(existing_rows)

    chain = verify_attestation_chain(rows)
    if chain.get("ok") is not True:
        return {"ok": False, "code": "ATTESTATION_CHAIN_INVALID", "chain": chain}
    latest = rows[0] if rows else None
    if (
        latest
        and latest.get("portfolioDigest") == audit.get("portfolioDigest")
        and int(latest.get("eventCount") or 0) == int(audit.get("eventCount") or 0)
    ):
        return {"ok": True, "idempotent": True, "attestation": latest}

    created_at = _now()
    previous_digest = chain.get("headAttestationDigest")
    champion_snapshot = _champion_snapshot(audit)
    integrity_snapshot = audit.get("integrity") or {}
    digest = _attestation_digest(
        portfolio_digest=str(audit.get("portfolioDigest") or ""),
        event_count=int(audit.get("eventCount") or 0),
        champion_snapshot=champion_snapshot,
        integrity_snapshot=integrity_snapshot,
        previous_digest=previous_digest,
        actor=actor,
        created_at=created_at,
    )
    attestation_id = "p61-" + digest[:24]
    row = {
        "attestation_id": attestation_id,
        "portfolio_digest": str(audit.get("portfolioDigest") or ""),
        "event_count": int(audit.get("eventCount") or 0),
        "audit_model_version": str(audit.get("modelVersion") or p60.MODEL_VERSION),
        "audit_state": str(audit.get("state") or "audit-ready"),
        "champion_snapshot": champion_snapshot,
        "integrity_snapshot": integrity_snapshot,
        "previous_attestation_digest": previous_digest,
        "attestation_digest": digest,
        "attested_by": actor[:128],
        "created_at": created_at,
    }
    public = _public(row)
    if not persist:
        return {"ok": True, "dryRun": True, "attestation": public}
    try:
        db.session.execute(attestations.insert().values(**row))
        db.session.commit()
    except (RuntimeError, SQLAlchemyError):
        _rollback()
        return {"ok": False, "code": "ATTESTATION_WRITE_FAILED"}
    return {"ok": True, "attestation": public, "status": build_status()}
