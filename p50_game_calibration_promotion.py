"""P5.0 controlled game-calibration promotion and production application.

P4.9 may declare a logit-affine challenger eligible for human review. P5.0 is
the explicit production-application boundary: only an owner-confirmed promotion
may append a champion event, and the event is applied only to P4.0 selected-side
moneyline probabilities. Promotion is never automatic and rollback is append-only.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

from database import db
import p49_game_calibration as p49

MODEL_NAME = "p5.0-game-calibration-promotion"
MODEL_VERSION = "p50-promotion-v1"
BASE_MODEL_VERSION = "p40-transparent-v1"
PROMOTE_CONFIRMATION = "PROMOTE_GAME_CALIBRATION"
ROLLBACK_CONFIRMATION = "ROLLBACK_GAME_CALIBRATION"
EVENT_LIMIT = 100

promotion_events = sa.Table(
    "game_calibration_promotion_events",
    db.metadata,
    sa.Column("event_id", sa.String(32), primary_key=True),
    sa.Column("action", sa.String(16), nullable=False, index=True),
    sa.Column("candidate_id", sa.String(64), index=True),
    sa.Column("family", sa.String(32)),
    sa.Column("slope", sa.Float),
    sa.Column("intercept", sa.Float),
    sa.Column("challenger_model_version", sa.String(64)),
    sa.Column("base_model_version", sa.String(64), nullable=False),
    sa.Column("approved_by", sa.String(128), nullable=False),
    sa.Column("governance_fingerprint", sa.String(64), nullable=False),
    sa.Column("governance_payload", sa.JSON, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
)


def _now() -> datetime:
    return datetime.now(UTC)


def _rollback() -> None:
    try:
        db.session.rollback()
    except Exception:  # noqa: BLE001
        pass


def _env_int(name: str, default: int, low: int, high: int) -> int:
    value = int(os.environ.get(name, default))
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return value


def _env_float(name: str, default: float, low: float, high: float) -> float:
    value = float(os.environ.get(name, default))
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return value


def policy() -> dict[str, Any]:
    """Additional P5.0 gates for applying an all-game challenger to moneyline."""
    return {
        "minMoneylineValidationSamples": _env_int(
            "P50_MIN_MONEYLINE_VALIDATION_SAMPLES", 8, 5, 500
        ),
        "maxMoneylineBrierRegression": _env_float(
            "P50_MAX_MONEYLINE_BRIER_REGRESSION", 0.0, 0.0, 0.05
        ),
        "maxMoneylineEceRegression": _env_float(
            "P50_MAX_MONEYLINE_ECE_REGRESSION", 0.02, 0.0, 0.10
        ),
        "selectedProbabilityFloor": 0.5,
        "selectedProbabilityCeiling": 0.999,
    }


def _fingerprint(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _event_public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "eventId": row.get("event_id"),
        "action": row.get("action"),
        "candidateId": row.get("candidate_id"),
        "family": row.get("family"),
        "parameters": {
            "slope": row.get("slope"),
            "intercept": row.get("intercept"),
        },
        "challengerModelVersion": row.get("challenger_model_version"),
        "baseModelVersion": row.get("base_model_version"),
        "approvedBy": row.get("approved_by"),
        "governanceFingerprint": row.get("governance_fingerprint"),
        "createdAt": row.get("created_at").isoformat() if row.get("created_at") else None,
    }


def list_events(*, limit: int = EVENT_LIMIT) -> list[dict[str, Any]]:
    try:
        rows = db.session.execute(
            sa.select(promotion_events)
            .order_by(promotion_events.c.created_at.desc(), promotion_events.c.event_id.desc())
            .limit(max(1, min(int(limit), EVENT_LIMIT)))
        ).mappings().all()
    except (RuntimeError, SQLAlchemyError):
        _rollback()
        return []
    return [_event_public(dict(row)) for row in rows]


def current_champion() -> dict[str, Any]:
    """Return the effective champion. Missing/unavailable registry fails to baseline."""
    try:
        row = db.session.execute(
            sa.select(promotion_events)
            .order_by(promotion_events.c.created_at.desc(), promotion_events.c.event_id.desc())
            .limit(1)
        ).mappings().first()
    except (RuntimeError, SQLAlchemyError):
        _rollback()
        return {
            "available": False,
            "state": "baseline",
            "applied": False,
            "candidateId": None,
            "family": "identity",
            "parameters": {"slope": 1.0, "intercept": 0.0},
            "baseModelVersion": BASE_MODEL_VERSION,
            "reason": "promotion_registry_unavailable",
        }
    if not row or str(row.action) != "promote":
        return {
            "available": True,
            "state": "baseline",
            "applied": False,
            "candidateId": None,
            "family": "identity",
            "parameters": {"slope": 1.0, "intercept": 0.0},
            "baseModelVersion": BASE_MODEL_VERSION,
            "reason": "no_active_promotion" if not row else "rolled_back_to_baseline",
            "latestEvent": _event_public(dict(row)) if row else None,
        }
    slope = row.slope
    intercept = row.intercept
    if not isinstance(slope, (int, float)) or not isinstance(intercept, (int, float)):
        return {
            "available": True,
            "state": "baseline",
            "applied": False,
            "candidateId": None,
            "family": "identity",
            "parameters": {"slope": 1.0, "intercept": 0.0},
            "baseModelVersion": BASE_MODEL_VERSION,
            "reason": "invalid_active_promotion",
            "latestEvent": _event_public(dict(row)),
        }
    return {
        "available": True,
        "state": "promoted",
        "applied": True,
        "candidateId": row.candidate_id,
        "family": row.family,
        "parameters": {"slope": float(slope), "intercept": float(intercept)},
        "challengerModelVersion": row.challenger_model_version,
        "baseModelVersion": row.base_model_version,
        "approvedBy": row.approved_by,
        "approvedAt": row.created_at.isoformat() if row.created_at else None,
        "governanceFingerprint": row.governance_fingerprint,
        "latestEvent": _event_public(dict(row)),
    }


def assess_candidate_report(report: dict[str, Any]) -> dict[str, Any]:
    """Apply P5.0 moneyline-specific production gates to a P4.9 review report."""
    candidate = report.get("candidate") if isinstance(report.get("candidate"), dict) else {}
    gate = report.get("promotionGate") if isinstance(report.get("promotionGate"), dict) else {}
    validation = candidate.get("validation") if isinstance(candidate.get("validation"), dict) else {}
    champion_markets = validation.get("perMarketChampion") if isinstance(validation.get("perMarketChampion"), dict) else {}
    challenger_markets = validation.get("perMarketChallenger") if isinstance(validation.get("perMarketChallenger"), dict) else {}
    moneyline_champion = champion_markets.get("moneyline") if isinstance(champion_markets.get("moneyline"), dict) else {}
    moneyline_challenger = challenger_markets.get("moneyline") if isinstance(challenger_markets.get("moneyline"), dict) else {}
    active = policy()

    champion_brier = moneyline_champion.get("brier")
    challenger_brier = moneyline_challenger.get("brier")
    champion_ece = moneyline_champion.get("ece")
    challenger_ece = moneyline_challenger.get("ece")
    moneyline_samples = int(moneyline_challenger.get("samples") or 0)
    brier_delta = (
        float(challenger_brier) - float(champion_brier)
        if isinstance(champion_brier, (int, float)) and isinstance(challenger_brier, (int, float))
        else None
    )
    ece_delta = (
        float(challenger_ece) - float(champion_ece)
        if isinstance(champion_ece, (int, float)) and isinstance(challenger_ece, (int, float))
        else None
    )
    params = candidate.get("parameters") if isinstance(candidate.get("parameters"), dict) else {}
    slope = params.get("slope")
    intercept = params.get("intercept")
    checks = {
        "p49ReviewState": report.get("state") == "review",
        "p49Eligible": gate.get("eligible") is True,
        "humanReviewRequired": gate.get("requiresHumanReview") is True,
        "automaticApplyDisabled": gate.get("automaticApply") is False,
        "forwardHoldout": candidate.get("validationIsForwardHoldout") is True,
        "candidateParametersValid": isinstance(slope, (int, float))
        and 0.5 <= float(slope) <= 1.5
        and isinstance(intercept, (int, float))
        and -0.3 <= float(intercept) <= 0.3,
        "moneylineValidationSampleFloor": moneyline_samples
        >= int(active["minMoneylineValidationSamples"]),
        "moneylineBrierNotRegressed": brier_delta is not None
        and brier_delta <= float(active["maxMoneylineBrierRegression"]),
        "moneylineEceRegressionBounded": ece_delta is not None
        and ece_delta <= float(active["maxMoneylineEceRegression"]),
    }
    return {
        "eligible": all(checks.values()),
        "candidateId": candidate.get("candidateId"),
        "parameters": {"slope": slope, "intercept": intercept},
        "checks": checks,
        "failedChecks": [key for key, passed in checks.items() if not passed],
        "moneyline": {
            "samples": moneyline_samples,
            "championBrier": champion_brier,
            "challengerBrier": challenger_brier,
            "brierDelta": round(brier_delta, 6) if brier_delta is not None else None,
            "championEce": champion_ece,
            "challengerEce": challenger_ece,
            "eceDelta": round(ece_delta, 6) if ece_delta is not None else None,
        },
        "policy": active,
    }


def _build_event(
    action: str,
    *,
    actor: str,
    candidate: dict[str, Any] | None,
    governance: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    timestamp = now or _now()
    params = candidate.get("parameters") if candidate and isinstance(candidate.get("parameters"), dict) else {}
    candidate_id = candidate.get("candidateId") if candidate else None
    material = f"{timestamp.isoformat()}|{action}|{candidate_id or 'baseline'}|{actor}"
    return {
        "event_id": "p50-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24],
        "action": action,
        "candidate_id": candidate_id,
        "family": candidate.get("family") if candidate else "identity",
        "slope": float(params["slope"]) if isinstance(params.get("slope"), (int, float)) else None,
        "intercept": float(params["intercept"]) if isinstance(params.get("intercept"), (int, float)) else None,
        "challenger_model_version": p49.MODEL_VERSION if candidate else None,
        "base_model_version": BASE_MODEL_VERSION,
        "approved_by": str(actor)[:128],
        "governance_fingerprint": _fingerprint(governance),
        "governance_payload": governance,
        "created_at": timestamp,
    }


def promote_candidate(
    candidate_id: str,
    *,
    confirmation: str,
    actor: str,
    persist: bool = True,
    report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Promote exactly the currently eligible P4.9 challenger after owner confirmation."""
    if confirmation != PROMOTE_CONFIRMATION:
        return {"ok": False, "code": "CONFIRMATION_REQUIRED", "required": PROMOTE_CONFIRMATION}
    if not str(actor or "").strip():
        return {"ok": False, "code": "ACTOR_REQUIRED"}
    report = report or p49.build_production_report()
    review = assess_candidate_report(report)
    candidate = report.get("candidate") if isinstance(report.get("candidate"), dict) else None
    if not review["eligible"] or not candidate:
        return {"ok": False, "code": "PROMOTION_GATE_FAILED", "review": review}
    if str(candidate_id) != str(candidate.get("candidateId")):
        return {
            "ok": False,
            "code": "CANDIDATE_MISMATCH",
            "expectedCandidateId": candidate.get("candidateId"),
        }
    current = current_champion() if persist else None
    if current and current.get("applied") and current.get("candidateId") == candidate_id:
        return {"ok": True, "idempotent": True, "champion": current, "review": review}

    governance = {
        "p49ModelVersion": report.get("modelVersion"),
        "p49State": report.get("state"),
        "p49PromotionGate": report.get("promotionGate"),
        "p50Review": review,
        "candidate": candidate,
        "automaticPromotion": False,
        "explicitOwnerConfirmation": True,
    }
    event = _build_event("promote", actor=actor, candidate=candidate, governance=governance)
    if not persist:
        return {
            "ok": True,
            "dryRun": True,
            "event": _event_public(event),
            "review": review,
            "wouldApply": True,
        }
    try:
        db.session.execute(promotion_events.insert().values(**event))
        db.session.commit()
    except (RuntimeError, SQLAlchemyError):
        _rollback()
        return {"ok": False, "code": "PROMOTION_WRITE_FAILED"}
    return {"ok": True, "event": _event_public(event), "champion": current_champion(), "review": review}


def rollback_to_baseline(
    *, confirmation: str, actor: str, persist: bool = True
) -> dict[str, Any]:
    """Append an explicit rollback event; never rewrite or delete promotion history."""
    if confirmation != ROLLBACK_CONFIRMATION:
        return {"ok": False, "code": "CONFIRMATION_REQUIRED", "required": ROLLBACK_CONFIRMATION}
    if not str(actor or "").strip():
        return {"ok": False, "code": "ACTOR_REQUIRED"}
    current = current_champion()
    if not current.get("applied"):
        return {"ok": True, "idempotent": True, "champion": current}
    governance = {
        "rollbackFromCandidateId": current.get("candidateId"),
        "rollbackFromParameters": current.get("parameters"),
        "automaticRollback": False,
        "explicitOwnerConfirmation": True,
    }
    event = _build_event("rollback", actor=actor, candidate=None, governance=governance)
    if not persist:
        return {"ok": True, "dryRun": True, "event": _event_public(event), "wouldApplyBaseline": True}
    try:
        db.session.execute(promotion_events.insert().values(**event))
        db.session.commit()
    except (RuntimeError, SQLAlchemyError):
        _rollback()
        return {"ok": False, "code": "ROLLBACK_WRITE_FAILED"}
    return {"ok": True, "event": _event_public(event), "champion": current_champion()}


def apply_to_selected_probability(
    probability: float, *, champion: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Apply the active champion to a selected-side P4.0 moneyline probability.

    The selected side is never flipped by calibration: production output remains
    within [0.5, 0.999]. If the registry is unavailable or no promotion exists,
    the original probability is returned unchanged.
    """
    active = champion or current_champion()
    original = max(0.5, min(0.999, float(probability)))
    if not active.get("applied"):
        return {
            "probability": original,
            "rawProbability": original,
            "applied": False,
            "candidateId": None,
            "modelVersion": MODEL_VERSION,
            "championState": active.get("state", "baseline"),
        }
    params = active.get("parameters") if isinstance(active.get("parameters"), dict) else {}
    slope = params.get("slope")
    intercept = params.get("intercept")
    if not isinstance(slope, (int, float)) or not isinstance(intercept, (int, float)):
        return {
            "probability": original,
            "rawProbability": original,
            "applied": False,
            "candidateId": None,
            "modelVersion": MODEL_VERSION,
            "championState": "invalid",
        }
    calibrated = p49.calibrate_probability(original, slope=float(slope), intercept=float(intercept))
    bounded = max(float(policy()["selectedProbabilityFloor"]), min(float(policy()["selectedProbabilityCeiling"]), calibrated))
    return {
        "probability": bounded,
        "rawProbability": original,
        "applied": True,
        "candidateId": active.get("candidateId"),
        "family": active.get("family"),
        "parameters": {"slope": float(slope), "intercept": float(intercept)},
        "modelVersion": MODEL_VERSION,
        "championState": "promoted",
        "approvedAt": active.get("approvedAt"),
    }


def build_status() -> dict[str, Any]:
    """Read-only production status and current P4.9-to-P5.0 promotion readiness."""
    challenger = p49.build_production_report()
    return {
        "available": True,
        "model": MODEL_NAME,
        "modelVersion": MODEL_VERSION,
        "champion": current_champion(),
        "promotionReview": assess_candidate_report(challenger),
        "challengerState": challenger.get("state"),
        "gradedSamples": challenger.get("gradedSamples", 0),
        "recentEvents": list_events(limit=10),
        "safetyContract": {
            "providerRequests": 0,
            "automaticPromotion": False,
            "explicitOwnerConfirmationRequired": True,
            "appendOnlyPromotionHistory": True,
            "rollbackToBaselineAvailable": True,
            "changesActionabilityThresholds": False,
            "changesBankrollPolicy": False,
            "placesBets": False,
        },
    }
