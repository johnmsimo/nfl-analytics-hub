"""Deterministic AI insight services for NFL Analytics Hub v4.0."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any


def _number(data: Mapping[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float(data.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def _probability(data: Mapping[str, Any]) -> float:
    value = _number(data, "probability", _number(data, "home_win_probability", 0.5))
    if value > 1:
        value /= 100.0
    return min(max(value, 0.0), 1.0)


def explain_prediction_change(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
    evidence: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Explain a prediction change using supplied structured evidence."""
    before = _probability(previous)
    after = _probability(current)
    delta = after - before
    drivers = []
    for item in evidence:
        try:
            impact = float(item.get("probability_impact", item.get("impact", 0.0)) or 0.0)
        except (TypeError, ValueError):
            continue
        drivers.append(
            {
                "name": str(item.get("name") or item.get("factor") or "evidence")[:120],
                "impact": round(max(-0.25, min(0.25, impact)), 4),
                "source": str(item.get("source") or "supplied")[:120],
                "observed_at": item.get("observed_at"),
            }
        )
    drivers.sort(key=lambda row: (-abs(row["impact"]), row["name"]))
    explained = round(sum(row["impact"] for row in drivers), 4)
    direction = "up" if delta > 0.002 else "down" if delta < -0.002 else "stable"
    return {
        "version": "4.0",
        "previous_probability": round(before, 4),
        "current_probability": round(after, 4),
        "probability_delta": round(delta, 4),
        "direction": direction,
        "drivers": drivers[:10],
        "explained_delta": explained,
        "unexplained_delta": round(delta - explained, 4),
        "material_change": abs(delta) >= 0.03,
    }


def upset_alert(decision: Mapping[str, Any], market: Mapping[str, Any]) -> dict[str, Any]:
    """Identify material disagreement where the model favors the market underdog."""
    model_probability = _probability(decision)
    market_probability = _probability(market)
    model_side = str(decision.get("side") or decision.get("decision") or "home").lower()
    market_side = str(market.get("favored_side") or market.get("side") or "home").lower()
    edge = model_probability - market_probability
    side_disagreement = model_side != market_side
    triggered = side_disagreement and abs(edge) >= 0.08
    severity = "high" if abs(edge) >= 0.16 else "moderate" if triggered else "none"
    return {
        "version": "4.0",
        "triggered": triggered,
        "severity": severity,
        "model_side": model_side,
        "market_side": market_side,
        "model_probability": round(model_probability, 4),
        "market_probability": round(market_probability, 4),
        "probability_edge": round(edge, 4),
        "message": (
            f"Upset alert: model favors {model_side} while the market favors {market_side}."
            if triggered
            else "No material upset signal."
        ),
    }


def confidence_reasoning(decision: Mapping[str, Any]) -> dict[str, Any]:
    """Explain confidence from probability, disagreement, sample, and freshness."""
    probability = _probability(decision)
    disagreement = min(max(_number(decision, "disagreement", 0.0), 0.0), 1.0)
    sample_size = max(0.0, _number(decision, "sample_size", 0.0))
    freshness_hours = max(0.0, _number(decision, "freshness_hours", 0.0))
    probability_strength = abs(probability - 0.5) * 2.0
    sample_strength = min(sample_size / 500.0, 1.0)
    freshness_strength = max(0.0, 1.0 - freshness_hours / 72.0)
    score = 100.0 * (
        probability_strength * 0.45
        + (1.0 - disagreement) * 0.30
        + sample_strength * 0.15
        + freshness_strength * 0.10
    )
    score = min(max(score, 0.0), 100.0)
    level = "high" if score >= 72 else "moderate" if score >= 45 else "low"
    reasons = [
        {"factor": "probability strength", "score": round(probability_strength, 4)},
        {"factor": "model agreement", "score": round(1.0 - disagreement, 4)},
        {"factor": "sample support", "score": round(sample_strength, 4)},
        {"factor": "data freshness", "score": round(freshness_strength, 4)},
    ]
    reasons.sort(key=lambda row: (-row["score"], row["factor"]))
    return {
        "version": "4.0",
        "confidence_score": round(score, 1),
        "confidence_level": level,
        "reasons": reasons,
        "primary_reason": reasons[0]["factor"],
    }


def evidence_recommendations(
    decision: Mapping[str, Any], evidence: Sequence[Mapping[str, Any]] = ()
) -> dict[str, Any]:
    """Create grounded recommendations linked only to supplied evidence."""
    confidence = confidence_reasoning(decision)
    recommendations = []
    for item in evidence:
        strength = min(max(_number(item, "strength", 0.5), 0.0), 1.0)
        action = str(item.get("recommendation") or item.get("action") or "Review evidence")[:240]
        recommendations.append(
            {
                "action": action,
                "priority": "high" if strength >= 0.75 else "medium" if strength >= 0.45 else "low",
                "strength": round(strength, 4),
                "source": str(item.get("source") or "supplied")[:120],
                "evidence_id": item.get("id"),
            }
        )
    recommendations.sort(key=lambda row: (-row["strength"], row["action"]))
    if confidence["confidence_level"] == "low":
        recommendations.insert(
            0,
            {
                "action": "Wait for stronger model agreement or fresher evidence before acting.",
                "priority": "high",
                "strength": 1.0,
                "source": "confidence_reasoning",
                "evidence_id": None,
            },
        )
    return {
        "version": "4.0",
        "grounded": True,
        "confidence": confidence,
        "recommendations": recommendations[:10],
        "count": min(len(recommendations), 10),
    }


def decision_history(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Normalize decision snapshots and summarize material changes over time."""
    normalized = []
    for index, event in enumerate(events):
        timestamp = str(event.get("timestamp") or event.get("observed_at") or "")
        if not timestamp:
            timestamp = datetime.now(timezone.utc).isoformat()
        normalized.append(
            {
                "id": str(event.get("id") or f"decision-{index + 1}"),
                "timestamp": timestamp,
                "probability": round(_probability(event), 4),
                "side": str(event.get("side") or event.get("decision") or "home").lower(),
                "confidence": round(_number(event, "confidence", 0.0), 2),
                "reason": str(event.get("reason") or event.get("summary") or "")[:300],
            }
        )
    normalized.sort(key=lambda row: (row["timestamp"], row["id"]))
    changes = []
    for previous, current in zip(normalized, normalized[1:]):
        delta = current["probability"] - previous["probability"]
        side_changed = current["side"] != previous["side"]
        if abs(delta) >= 0.03 or side_changed:
            changes.append(
                {
                    "from_id": previous["id"],
                    "to_id": current["id"],
                    "probability_delta": round(delta, 4),
                    "side_changed": side_changed,
                }
            )
    return {
        "version": "4.0",
        "count": len(normalized),
        "events": normalized,
        "material_changes": changes,
        "latest": normalized[-1] if normalized else None,
    }
