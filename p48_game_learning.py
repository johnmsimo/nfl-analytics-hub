"""P4.8 read-only game outcome learning and calibration monitoring.

P4.4 records immutable first-publication receipts and grades them after final
scores. P4.8 turns those game-market outcomes into transparent calibration and
market-benchmark diagnostics without silently changing P4.0 probabilities,
P4.1 actionability thresholds, P4.5 refresh policy, or P4.6 bankroll sizing.
"""
from __future__ import annotations

import os
from collections import defaultdict
from typing import Any, Iterable

import p44_game_decision_ledger as p44

MODEL_NAME = "p4.8-game-learning-monitor"
MODEL_VERSION = "p48-game-learning-v1"
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
    """Return protected sample thresholds for game-market learning."""
    return {
        "minGradedSamples": _env_int(
            "P48_MIN_GRADED_SAMPLES", 50, minimum=10, maximum=5000
        ),
        "minSegmentSamples": _env_int(
            "P48_MIN_SEGMENT_SAMPLES", 20, minimum=5, maximum=1000
        ),
        "calibrationAlert": _env_float(
            "P48_CALIBRATION_ALERT", 0.05, minimum=0.01, maximum=0.25
        ),
        "maxEce": _env_float("P48_MAX_ECE", 0.08, minimum=0.01, maximum=0.30),
        "marketSkillAlert": _env_float(
            "P48_MARKET_SKILL_ALERT", 0.01, minimum=0.001, maximum=0.15
        ),
        "receiptWindowLimit": RECEIPT_WINDOW_LIMIT,
    }


def _bounded_probability(value: Any) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    return max(0.0, min(1.0, float(value)))


def _sample(receipt: dict[str, Any]) -> dict[str, Any] | None:
    grade = str(receipt.get("grade") or "").lower()
    if grade not in {"win", "loss"}:
        return None
    release = receipt.get("release") if isinstance(receipt.get("release"), dict) else {}
    result = receipt.get("result") if isinstance(receipt.get("result"), dict) else {}
    model_probability = _bounded_probability(
        result.get("probability")
        if result.get("probability") is not None
        else release.get("modelProbability")
    )
    if model_probability is None:
        return None
    market_probability = _bounded_probability(release.get("fairMarketProbability"))
    unit_profit = result.get("unitProfit")
    return {
        "probability": model_probability,
        "marketProbability": market_probability,
        "outcome": 1.0 if grade == "win" else 0.0,
        "unitProfit": float(unit_profit) if isinstance(unit_profit, (int, float)) else None,
        "market": str(release.get("marketKey") or "unknown"),
        "decisionGrade": str(release.get("decisionGrade") or "unknown"),
        "seasonType": str(release.get("seasonType") or "unknown"),
        "side": str(release.get("selectedSide") or "unknown"),
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
            "unitProfit": None,
            "unitRoi": None,
            "marketBenchmarkSamples": 0,
            "marketBrier": None,
            "brierSkillVsMarket": None,
        }

    count = len(samples)
    avg_probability = sum(item["probability"] for item in samples) / count
    hit_rate = sum(item["outcome"] for item in samples) / count
    model_brier = sum(
        (item["probability"] - item["outcome"]) ** 2 for item in samples
    ) / count
    profits = [item["unitProfit"] for item in samples if item["unitProfit"] is not None]
    benchmark = [item for item in samples if item["marketProbability"] is not None]
    market_brier = None
    model_paired_brier = None
    if benchmark:
        market_brier = sum(
            (float(item["marketProbability"]) - item["outcome"]) ** 2
            for item in benchmark
        ) / len(benchmark)
        model_paired_brier = sum(
            (item["probability"] - item["outcome"]) ** 2 for item in benchmark
        ) / len(benchmark)
    return {
        "samples": count,
        "avgProbability": round(avg_probability, 6),
        "hitRate": round(hit_rate, 6),
        "calibrationGap": round(hit_rate - avg_probability, 6),
        "brier": round(model_brier, 6),
        "ece": _ece(samples),
        "pricedSamples": len(profits),
        "unitProfit": round(sum(profits), 6) if profits else None,
        "unitRoi": round(sum(profits) / len(profits), 6) if profits else None,
        "marketBenchmarkSamples": len(benchmark),
        "marketBrier": round(market_brier, 6) if market_brier is not None else None,
        "brierSkillVsMarket": (
            round(market_brier - model_paired_brier, 6)
            if market_brier is not None and model_paired_brier is not None
            else None
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
    market_skill_alert: float,
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
        paired = int(metrics.get("marketBenchmarkSamples") or 0)
        skill = metrics.get("brierSkillVsMarket")
        if (
            paired >= min_segment_samples
            and isinstance(skill, (int, float))
            and skill <= -market_skill_alert
        ):
            signals.append(
                {
                    "scope": scope,
                    "key": key,
                    "type": "negative_market_skill",
                    "samples": paired,
                    "brierSkillVsMarket": round(float(skill), 6),
                    "direction": "review_model_vs_market",
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
    market_skill_alert: float | None = None,
) -> dict[str, Any]:
    """Build a deterministic learning report from immutable P4.4 receipts."""
    active = policy()
    min_samples = active["minGradedSamples"] if min_samples is None else int(min_samples)
    min_segment_samples = (
        active["minSegmentSamples"]
        if min_segment_samples is None
        else int(min_segment_samples)
    )
    calibration_alert = (
        active["calibrationAlert"]
        if calibration_alert is None
        else float(calibration_alert)
    )
    max_ece = active["maxEce"] if max_ece is None else float(max_ece)
    market_skill_alert = (
        active["marketSkillAlert"]
        if market_skill_alert is None
        else float(market_skill_alert)
    )

    receipt_list = list(receipts)
    samples = [
        sample for receipt in receipt_list if (sample := _sample(receipt)) is not None
    ]
    overall = _metrics(samples)
    segments = {
        "perMarket": _segment_metrics(samples, "market"),
        "perDecisionGrade": _segment_metrics(samples, "decisionGrade"),
        "perSeasonType": _segment_metrics(samples, "seasonType"),
        "perSide": _segment_metrics(samples, "side"),
    }
    signals = _signals(
        overall,
        segments,
        min_segment_samples=min_segment_samples,
        calibration_alert=calibration_alert,
        max_ece=max_ece,
        market_skill_alert=market_skill_alert,
    )
    graded_samples = int(overall["samples"])
    if graded_samples < min_samples:
        state = "collecting"
        action = "collect_more_game_outcomes"
    elif signals:
        state = "review"
        action = "review_game_calibration_segments"
    else:
        state = "stable"
        action = "hold_game_model"

    return {
        "available": True,
        "model": MODEL_NAME,
        "modelVersion": MODEL_VERSION,
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
            "marketSkillAlert": market_skill_alert,
            "receiptWindowLimit": active["receiptWindowLimit"],
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
                else ("learning_signals_present" if signals else "monitoring_stable")
            ),
        },
        "safetyContract": {
            "readOnly": True,
            "providerRequests": 0,
            "writesLedger": False,
            "changesModelProbabilities": False,
            "changesActionabilityThresholds": False,
            "changesBankrollPolicy": False,
            "automaticPromotion": False,
        },
    }


def build_learning_report() -> dict[str, Any]:
    """Read the persisted P4.4 game ledger and return current P4.8 state."""
    ledger = p44.ledger_status()
    if not ledger.get("available"):
        return {
            "available": False,
            "model": MODEL_NAME,
            "modelVersion": MODEL_VERSION,
            "state": "unavailable",
            "recommendedAction": "restore_game_ledger_persistence",
            "autoApply": False,
            "ledger": ledger,
            "safetyContract": {
                "readOnly": True,
                "providerRequests": 0,
                "writesLedger": False,
                "changesModelProbabilities": False,
                "changesActionabilityThresholds": False,
                "changesBankrollPolicy": False,
                "automaticPromotion": False,
            },
        }
    report = build_report_from_receipts(
        p44.list_receipts(limit=RECEIPT_WINDOW_LIMIT)
    )
    report["ledger"] = ledger
    return report
