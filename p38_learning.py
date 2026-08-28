"""P3.8 read-only outcome learning and calibration monitoring.

P3.7 created immutable publication receipts and automatic grading. P3.8 turns
those receipts into a transparent learning report without allowing production
outcomes to silently rewrite model probabilities or thresholds.

This module is intentionally read-only. It may recommend review directions, but
it never mutates the decision ledger, projection parameters, or pricing policy.
"""
from __future__ import annotations

import os
from collections import defaultdict
from typing import Any, Iterable

import decision_ledger

MODEL_NAME = "p3.8-learning-monitor"
RECEIPT_WINDOW_LIMIT = 2000


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    value = default if raw in (None, "") else int(raw)
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    raw = os.environ.get(name)
    value = default if raw in (None, "") else float(raw)
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def policy() -> dict[str, Any]:
    """Return the protected learning-monitor sample policy."""
    return {
        "minGradedSamples": _env_int(
            "P38_MIN_GRADED_SAMPLES", 50, minimum=10, maximum=5000
        ),
        "minSegmentSamples": _env_int(
            "P38_MIN_SEGMENT_SAMPLES", 20, minimum=5, maximum=1000
        ),
        "calibrationAlert": _env_float(
            "P38_CALIBRATION_ALERT", 0.05, minimum=0.01, maximum=0.25
        ),
        "maxEce": _env_float("P38_MAX_ECE", 0.08, minimum=0.01, maximum=0.30),
        "receiptWindowLimit": RECEIPT_WINDOW_LIMIT,
    }


def _probability(receipt: dict[str, Any]) -> float | None:
    result = receipt.get("result") if isinstance(receipt.get("result"), dict) else {}
    release = receipt.get("release") if isinstance(receipt.get("release"), dict) else {}
    for value in (
        result.get("probability"),
        release.get("consensusProb"),
        release.get("modelProb"),
    ):
        if isinstance(value, (int, float)):
            return max(0.0, min(1.0, float(value)))
    return None


def _sample(receipt: dict[str, Any]) -> dict[str, Any] | None:
    grade = str(receipt.get("grade") or "").lower()
    if grade not in {"win", "loss"}:
        return None
    probability = _probability(receipt)
    if probability is None:
        return None

    release = receipt.get("release") if isinstance(receipt.get("release"), dict) else {}
    result = receipt.get("result") if isinstance(receipt.get("result"), dict) else {}
    unit_profit = result.get("unitProfit")
    return {
        "probability": probability,
        "outcome": 1.0 if grade == "win" else 0.0,
        "unitProfit": float(unit_profit) if isinstance(unit_profit, (int, float)) else None,
        "market": str(release.get("marketKey") or "unknown"),
        "decisionGrade": str(release.get("decisionGrade") or "unknown"),
        "confidenceGrade": str(release.get("confidenceGrade") or "unknown"),
        "side": str(release.get("side") or "unknown"),
    }


def _ece(samples: list[dict[str, Any]], bins: int = 10) -> float | None:
    if not samples:
        return None
    total = len(samples)
    error = 0.0
    for idx in range(bins):
        low, high = idx / bins, (idx + 1) / bins
        bucket = [
            sample
            for sample in samples
            if low <= sample["probability"] < high
            or (idx == bins - 1 and sample["probability"] == 1.0)
        ]
        if not bucket:
            continue
        avg_probability = sum(item["probability"] for item in bucket) / len(bucket)
        hit_rate = sum(item["outcome"] for item in bucket) / len(bucket)
        error += len(bucket) / total * abs(avg_probability - hit_rate)
    return round(error, 6)


def _metrics(samples: list[dict[str, Any]]) -> dict[str, Any]:
    if not samples:
        return {
            "samples": 0,
            "avgProbability": None,
            "hitRate": None,
            "calibrationGap": None,
            "brier": None,
            "ece": None,
            "pricedSamples": 0,
            "unitRoi": None,
        }

    count = len(samples)
    avg_probability = sum(item["probability"] for item in samples) / count
    hit_rate = sum(item["outcome"] for item in samples) / count
    brier = sum(
        (item["probability"] - item["outcome"]) ** 2 for item in samples
    ) / count
    unit_profits = [
        item["unitProfit"] for item in samples if item["unitProfit"] is not None
    ]
    return {
        "samples": count,
        "avgProbability": round(avg_probability, 6),
        "hitRate": round(hit_rate, 6),
        "calibrationGap": round(hit_rate - avg_probability, 6),
        "brier": round(brier, 6),
        "ece": _ece(samples),
        "pricedSamples": len(unit_profits),
        "unitRoi": (
            round(sum(unit_profits) / len(unit_profits), 6) if unit_profits else None
        ),
    }


def _segment_metrics(
    samples: list[dict[str, Any]], field: str
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sample in samples:
        grouped[str(sample.get(field) or "unknown")].append(sample)
    return {
        key: _metrics(grouped[key])
        for key in sorted(grouped, key=lambda item: (-len(grouped[item]), item))
    }


def _signals(
    overall: dict[str, Any],
    segments: dict[str, dict[str, dict[str, Any]]],
    *,
    min_segment_samples: int,
    calibration_alert: float,
    max_ece: float,
) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []

    def inspect(scope: str, key: str, metrics: dict[str, Any]) -> None:
        samples = int(metrics.get("samples") or 0)
        if samples < min_segment_samples:
            return
        gap = metrics.get("calibrationGap")
        ece = metrics.get("ece")
        if isinstance(gap, (int, float)):
            if gap <= -calibration_alert:
                signals.append(
                    {
                        "scope": scope,
                        "key": key,
                        "type": "overconfidence",
                        "samples": samples,
                        "gap": round(float(gap), 6),
                        "direction": "shrink_toward_50",
                    }
                )
            elif gap >= calibration_alert:
                signals.append(
                    {
                        "scope": scope,
                        "key": key,
                        "type": "underconfidence",
                        "samples": samples,
                        "gap": round(float(gap), 6),
                        "direction": "expand_from_50",
                    }
                )
        if isinstance(ece, (int, float)) and ece > max_ece:
            signals.append(
                {
                    "scope": scope,
                    "key": key,
                    "type": "high_calibration_error",
                    "samples": samples,
                    "ece": round(float(ece), 6),
                    "direction": "review_calibration",
                }
            )

    inspect("overall", "all", overall)
    for family, family_metrics in segments.items():
        for key, metrics in family_metrics.items():
            inspect(family, key, metrics)
    return signals


def build_report_from_receipts(
    receipts: Iterable[dict[str, Any]],
    *,
    min_samples: int | None = None,
    min_segment_samples: int | None = None,
    calibration_alert: float | None = None,
    max_ece: float | None = None,
) -> dict[str, Any]:
    """Build a deterministic learning report from immutable ledger receipts."""
    active_policy = policy()
    min_samples = (
        active_policy["minGradedSamples"] if min_samples is None else int(min_samples)
    )
    min_segment_samples = (
        active_policy["minSegmentSamples"]
        if min_segment_samples is None
        else int(min_segment_samples)
    )
    calibration_alert = (
        active_policy["calibrationAlert"]
        if calibration_alert is None
        else float(calibration_alert)
    )
    max_ece = active_policy["maxEce"] if max_ece is None else float(max_ece)

    receipt_list = list(receipts)
    samples = [
        sample for receipt in receipt_list if (sample := _sample(receipt)) is not None
    ]
    overall = _metrics(samples)
    segments = {
        "perMarket": _segment_metrics(samples, "market"),
        "perDecisionGrade": _segment_metrics(samples, "decisionGrade"),
        "perConfidenceGrade": _segment_metrics(samples, "confidenceGrade"),
        "perSide": _segment_metrics(samples, "side"),
    }
    signals = _signals(
        overall,
        segments,
        min_segment_samples=min_segment_samples,
        calibration_alert=calibration_alert,
        max_ece=max_ece,
    )

    graded_samples = int(overall["samples"])
    if graded_samples < min_samples:
        state = "collecting"
        action = "collect_more_outcomes"
    elif signals:
        state = "review"
        action = "review_calibration_segments"
    else:
        state = "stable"
        action = "hold_model"

    return {
        "available": True,
        "model": MODEL_NAME,
        "state": state,
        "recommendedAction": action,
        "autoApply": False,
        "receiptCount": len(receipt_list),
        "gradedCalibrationSamples": graded_samples,
        "samplePolicy": {
            "minGradedSamples": min_samples,
            "minSegmentSamples": min_segment_samples,
            "calibrationAlert": calibration_alert,
            "maxEce": max_ece,
            "receiptWindowLimit": active_policy["receiptWindowLimit"],
        },
        "overall": overall,
        "segments": segments,
        "signals": signals,
        "promotionGate": {
            "eligibleForReview": graded_samples >= min_samples,
            "automaticApply": False,
            "requiresHumanReview": True,
            "reason": (
                "insufficient_samples"
                if graded_samples < min_samples
                else ("calibration_signals_present" if signals else "monitoring_stable")
            ),
        },
    }


def build_learning_report() -> dict[str, Any]:
    """Read the persisted P3.7 ledger and return the current P3.8 learning state."""
    status = decision_ledger.ledger_status()
    if not status.get("available"):
        return {
            "available": False,
            "model": MODEL_NAME,
            "state": "unavailable",
            "recommendedAction": "restore_ledger_persistence",
            "autoApply": False,
            "ledger": status,
        }

    report = build_report_from_receipts(
        decision_ledger.list_receipts(limit=RECEIPT_WINDOW_LIMIT)
    )
    report["ledger"] = status
    return report
