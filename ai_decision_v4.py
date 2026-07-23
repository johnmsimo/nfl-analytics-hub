"""NFL Analytics Hub v4.0 AI decision-intelligence primitives."""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any


def _probability(value: Any) -> float:
    number = float(value)
    if number > 1:
        number /= 100.0
    return min(max(number, 0.001), 0.999)


def _reliability(model: Mapping[str, Any]) -> float:
    calibration = min(max(float(model.get("calibration", 0.7)), 0.0), 1.0)
    recency = min(max(float(model.get("recency", 0.8)), 0.0), 1.0)
    sample = max(0.0, float(model.get("sample_size", 0)))
    sample_score = min(math.log1p(sample) / math.log1p(10_000), 1.0)
    return calibration * 0.55 + recency * 0.25 + sample_score * 0.20


def ensemble_decision(models: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Blend model probabilities using transparent reliability weights."""
    rows = []
    for raw in models:
        try:
            probability = _probability(raw.get("probability"))
            reliability = _reliability(raw)
        except (TypeError, ValueError):
            continue
        rows.append(
            {
                "name": str(raw.get("name") or "unnamed-model")[:80],
                "probability": probability,
                "reliability": reliability,
                "version": str(raw.get("version") or "unknown")[:40],
            }
        )
    if not rows:
        return {"status": "insufficient_models", "version": "4.0", "models": []}

    total_weight = sum(row["reliability"] for row in rows)
    if total_weight <= 0:
        total_weight = float(len(rows))
        for row in rows:
            row["reliability"] = 1.0
    probability = sum(row["probability"] * row["reliability"] for row in rows) / total_weight
    variance = sum(
        row["reliability"] * (row["probability"] - probability) ** 2 for row in rows
    ) / total_weight
    disagreement = math.sqrt(variance)
    confidence = min(max((1.0 - disagreement * 2.0) * (total_weight / len(rows)), 0.0), 1.0)

    contributors = sorted(rows, key=lambda row: row["reliability"], reverse=True)
    for row in contributors:
        row["weight"] = round(row["reliability"] / total_weight, 6)
        row["probability"] = round(row["probability"], 6)
        row["reliability"] = round(row["reliability"], 6)

    return {
        "status": "ok",
        "version": "4.0",
        "probability": round(probability, 6),
        "decision": "home" if probability >= 0.5 else "away",
        "confidence": round(confidence, 6),
        "disagreement": round(disagreement, 6),
        "models": contributors,
        "primary_model": contributors[0]["name"],
    }


def scenario_decision(
    baseline: Mapping[str, Any],
    scenarios: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Apply explainable scenario impacts to an ensemble baseline."""
    base_probability = _probability(baseline.get("probability", 0.5))
    applied = []
    net_delta = 0.0
    for raw in scenarios:
        try:
            delta = float(raw.get("probability_delta", 0.0))
        except (TypeError, ValueError):
            continue
        delta = min(max(delta, -0.35), 0.35)
        if not bool(raw.get("active", True)):
            continue
        net_delta += delta
        applied.append(
            {
                "name": str(raw.get("name") or "scenario")[:80],
                "probability_delta": round(delta, 6),
                "reason": str(raw.get("reason") or "No reason supplied")[:240],
            }
        )
    adjusted = min(max(base_probability + net_delta, 0.001), 0.999)
    applied.sort(key=lambda item: abs(item["probability_delta"]), reverse=True)
    return {
        "version": "4.0",
        "baseline_probability": round(base_probability, 6),
        "adjusted_probability": round(adjusted, 6),
        "net_delta": round(adjusted - base_probability, 6),
        "decision": "home" if adjusted >= 0.5 else "away",
        "drivers": applied,
        "biggest_driver": applied[0] if applied else None,
    }


def decision_brief(
    ensemble: Mapping[str, Any],
    scenario: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a grounded decision explanation from structured outputs."""
    probability = float((scenario or ensemble).get("adjusted_probability", ensemble.get("probability", 0.5)))
    decision = "home" if probability >= 0.5 else "away"
    confidence = float(ensemble.get("confidence", 0.0))
    disagreement = float(ensemble.get("disagreement", 0.0))
    risk = "high" if disagreement >= 0.18 or confidence < 0.45 else "moderate" if disagreement >= 0.10 or confidence < 0.7 else "low"
    primary = str(ensemble.get("primary_model") or "ensemble")
    summary = (
        f"The v4 ensemble favors the {decision} side at {probability:.1%}. "
        f"Primary influence: {primary}. Decision risk is {risk}."
    )
    return {
        "version": "4.0",
        "decision": decision,
        "probability": round(probability, 6),
        "confidence": round(confidence, 6),
        "disagreement": round(disagreement, 6),
        "risk": risk,
        "summary": summary,
        "grounded": True,
    }
