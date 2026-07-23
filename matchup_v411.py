"""Deterministic matchup-intelligence services for NFL Analytics Hub v4.1.1."""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

VERSION = "4.1.1"
_TENDENCY_METRICS = {
    "success_rate": {"weight": 0.45, "scale": 1.0},
    "explosive_rate": {"weight": 0.35, "scale": 1.0},
    "yards_per_play": {"weight": 0.20, "scale": 10.0},
}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _identity(profile: Mapping[str, Any], fallback: str) -> dict[str, str]:
    entity_id = str(
        profile.get("id")
        or profile.get("team_id")
        or profile.get("team")
        or profile.get("name")
        or fallback
    )[:120]
    name = str(profile.get("name") or profile.get("team") or entity_id)[:120]
    return {"id": entity_id, "name": name}


def _bounded_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, low), high)


def _sample(profile: Mapping[str, Any], key: Any = None) -> float | None:
    candidates = [str(key)] if key else []
    candidates.extend(["sample_size", "snaps", "plays"])
    for candidate in candidates:
        value = _number(profile.get(candidate))
        if value is not None and value >= 0:
            return value
    return None


def _sample_context(
    offense_sample: float | None,
    defense_sample: float | None,
    minimum: int,
) -> dict[str, Any]:
    available = [value for value in (offense_sample, defense_sample) if value is not None]
    if not available:
        support = None
        status = "unknown"
    else:
        support = min(min(available) / minimum, 1.0)
        status = "supported" if support >= 1.0 else "limited"
    return {
        "offense": None if offense_sample is None else int(offense_sample),
        "defense": None if defense_sample is None else int(defense_sample),
        "minimum": minimum,
        "support": None if support is None else round(support, 4),
        "status": status,
    }


def _evidence_factor(sample_context: Mapping[str, Any]) -> float:
    support = sample_context.get("support")
    return 0.5 if support is None else float(support)


def _direction(edge: float, threshold: float) -> str:
    if edge >= threshold:
        return "offense"
    if edge <= -threshold:
        return "defense"
    return "neutral"


def _lean(edge: float) -> str:
    if edge >= 0.05:
        return "offense"
    if edge <= -0.05:
        return "defense"
    return "even"


def compare_matchup_profiles(
    offense: Mapping[str, Any],
    defense: Mapping[str, Any],
    metrics: Sequence[Mapping[str, Any]],
    min_sample: int = 20,
    limit: int = 10,
) -> dict[str, Any]:
    """Compare supplied offense and defense metrics using explicit matchup rules."""
    minimum = _bounded_int(min_sample, 20, 1, 10000)
    bounded_limit = _bounded_int(limit, 10, 1, 25)
    valid_rules = [rule for rule in metrics if isinstance(rule, Mapping)]
    comparisons = []
    unavailable = []

    for index, rule in enumerate(valid_rules):
        offense_metric = str(rule.get("offense_metric") or rule.get("metric") or "").strip()
        defense_metric = str(rule.get("defense_metric") or rule.get("metric") or "").strip()
        label = str(
            rule.get("label")
            or rule.get("name")
            or offense_metric
            or defense_metric
            or f"metric-{index + 1}"
        )[:120]
        if not offense_metric or not defense_metric:
            unavailable.append({"label": label, "reason": "metric keys are required"})
            continue

        offense_value = _number(offense.get(offense_metric))
        defense_value = _number(defense.get(defense_metric))
        if offense_value is None or defense_value is None:
            missing = []
            if offense_value is None:
                missing.append(f"offense.{offense_metric}")
            if defense_value is None:
                missing.append(f"defense.{defense_metric}")
            unavailable.append(
                {"label": label, "reason": "missing numeric value", "missing": missing}
            )
            continue

        scale = _number(rule.get("scale"))
        if scale is None or scale <= 0:
            scale = max(abs(offense_value), abs(defense_value), 1.0)
            normalization = "observed_magnitude"
        else:
            normalization = "caller_supplied_scale"
        direction = str(rule.get("direction") or "higher").strip().lower()
        lower_favors_offense = direction in {"lower", "lower_favors_offense", "ascending"}
        raw_delta = offense_value - defense_value
        normalized_edge = raw_delta / scale
        if lower_favors_offense:
            normalized_edge *= -1
        normalized_edge = min(max(normalized_edge, -1.0), 1.0)

        weight = _number(rule.get("weight"))
        weight = min(max(weight if weight is not None else 1.0, 0.1), 5.0)
        threshold = _number(rule.get("neutral_threshold"))
        threshold = min(max(threshold if threshold is not None else 0.05, 0.0), 0.5)
        sample_context = _sample_context(
            _sample(offense, rule.get("offense_sample_metric")),
            _sample(defense, rule.get("defense_sample_metric")),
            minimum,
        )
        side = _direction(normalized_edge, threshold)
        evidence_score = abs(normalized_edge) * weight * _evidence_factor(sample_context)
        comparisons.append(
            {
                "label": label,
                "offense_metric": offense_metric,
                "defense_metric": defense_metric,
                "offense_value": round(offense_value, 4),
                "defense_value": round(defense_value, 4),
                "raw_delta": round(raw_delta, 4),
                "direction": (
                    "lower_favors_offense"
                    if lower_favors_offense
                    else "higher_favors_offense"
                ),
                "scale": round(scale, 4),
                "normalization": normalization,
                "normalized_edge": round(normalized_edge, 4),
                "advantage": side,
                "weight": round(weight, 4),
                "evidence_score": round(evidence_score, 4),
                "sample_context": sample_context,
                "claim": (
                    f"Offense advantage in {label}: {offense_value:.4g} vs {defense_value:.4g}."
                    if side == "offense"
                    else (
                        f"Defense advantage in {label}: "
                        f"{defense_value:.4g} vs {offense_value:.4g}."
                    )
                    if side == "defense"
                    else f"No material edge in {label}: {offense_value:.4g} vs {defense_value:.4g}."
                ),
            }
        )

    comparisons.sort(key=lambda row: (-row["evidence_score"], row["label"]))
    weighted_total = sum(row["normalized_edge"] * row["weight"] for row in comparisons)
    total_weight = sum(row["weight"] for row in comparisons)
    overall_edge = weighted_total / total_weight if total_weight else 0.0
    coverage = len(comparisons) / max(len(valid_rules), 1)
    sample_factors = [_evidence_factor(row["sample_context"]) for row in comparisons]
    sample_support = sum(sample_factors) / len(sample_factors) if sample_factors else 0.0
    confidence_score = coverage * 0.6 + sample_support * 0.4
    confidence_level = (
        "high" if confidence_score >= 0.75 else "moderate" if confidence_score >= 0.45 else "low"
    )
    offense_advantages = [
        row for row in comparisons if row["advantage"] == "offense"
    ][:bounded_limit]
    defense_advantages = [
        row for row in comparisons if row["advantage"] == "defense"
    ][:bounded_limit]
    return {
        "version": VERSION,
        "method": "explicit_metric_matchup",
        "offense": _identity(offense, "offense"),
        "defense": _identity(defense, "defense"),
        "min_sample": minimum,
        "metrics_requested": len(valid_rules),
        "metrics_compared": len(comparisons),
        "feature_coverage": round(coverage, 4),
        "overall_edge": round(overall_edge, 4),
        "lean": _lean(overall_edge),
        "confidence": {
            "score": round(confidence_score, 4),
            "level": confidence_level,
            "coverage": round(coverage, 4),
            "sample_support": round(sample_support, 4),
        },
        "comparisons": comparisons,
        "offensive_advantages": offense_advantages,
        "defensive_advantages": defense_advantages,
        "exploitable_weaknesses": [
            {"target": "defense", **row} for row in offense_advantages
        ],
        "offensive_risks": [{"target": "offense", **row} for row in defense_advantages],
        "unavailable_metrics": unavailable,
    }


def compare_tendencies(
    offense: Sequence[Mapping[str, Any]],
    defense: Sequence[Mapping[str, Any]],
    metrics: Sequence[str] | None = None,
    min_snaps: int = 10,
    limit: int = 10,
) -> dict[str, Any]:
    """Compare matching tendency labels; defense metrics are interpreted as rates allowed."""
    minimum = _bounded_int(min_snaps, 10, 1, 10000)
    bounded_limit = _bounded_int(limit, 10, 1, 25)
    selected = [
        str(metric).strip()
        for metric in (metrics or _TENDENCY_METRICS)
        if str(metric).strip() in _TENDENCY_METRICS
    ]
    selected = list(dict.fromkeys(selected))
    offense_rows = {
        str(row.get("label") or "").strip(): row
        for row in offense
        if isinstance(row, Mapping) and str(row.get("label") or "").strip()
    }
    defense_rows = {
        str(row.get("label") or "").strip(): row
        for row in defense
        if isinstance(row, Mapping) and str(row.get("label") or "").strip()
    }

    matchups = []
    insufficient = []
    for label in sorted(set(offense_rows) & set(defense_rows)):
        offense_row = offense_rows[label]
        defense_row = defense_rows[label]
        offense_snaps = _number(offense_row.get("snaps"))
        defense_snaps = _number(defense_row.get("snaps"))
        if (
            offense_snaps is None
            or defense_snaps is None
            or offense_snaps < minimum
            or defense_snaps < minimum
        ):
            insufficient.append(
                {
                    "label": label,
                    "offense_snaps": None if offense_snaps is None else int(offense_snaps),
                    "defense_snaps": None if defense_snaps is None else int(defense_snaps),
                }
            )
            continue

        metric_evidence = []
        weighted_total = 0.0
        total_weight = 0.0
        for metric in selected:
            offense_value = _number(offense_row.get(metric))
            defense_value = _number(defense_row.get(metric))
            if offense_value is None or defense_value is None:
                continue
            config = _TENDENCY_METRICS[metric]
            normalized_edge = min(
                max((offense_value - defense_value) / config["scale"], -1.0),
                1.0,
            )
            weighted_total += normalized_edge * config["weight"]
            total_weight += config["weight"]
            metric_evidence.append(
                {
                    "metric": metric,
                    "offense_value": round(offense_value, 4),
                    "defense_allowed": round(defense_value, 4),
                    "normalized_edge": round(normalized_edge, 4),
                    "weight": config["weight"],
                }
            )
        if not metric_evidence:
            insufficient.append(
                {
                    "label": label,
                    "offense_snaps": int(offense_snaps),
                    "defense_snaps": int(defense_snaps),
                    "reason": "no shared numeric tendency metrics",
                }
            )
            continue

        edge = weighted_total / total_weight
        offense_usage = _number(offense_row.get("usage_rate"))
        defense_usage = _number(defense_row.get("usage_rate"))
        if offense_usage is None or defense_usage is None:
            relevance = None
            relevance_factor = 1.0
        else:
            relevance = math.sqrt(max(offense_usage, 0.0) * max(defense_usage, 0.0))
            relevance_factor = relevance
        sample_support = min(min(offense_snaps, defense_snaps) / minimum, 1.0)
        evidence_score = abs(edge) * relevance_factor * sample_support
        side = _direction(edge, 0.03)
        matchups.append(
            {
                "label": label,
                "advantage": side,
                "edge": round(edge, 4),
                "evidence_score": round(evidence_score, 4),
                "offense_usage_rate": None if offense_usage is None else round(offense_usage, 4),
                "defense_usage_rate": None if defense_usage is None else round(defense_usage, 4),
                "matchup_relevance": None if relevance is None else round(relevance, 4),
                "sample_context": {
                    "offense_snaps": int(offense_snaps),
                    "defense_snaps": int(defense_snaps),
                    "minimum": minimum,
                    "support": round(sample_support, 4),
                },
                "metrics": metric_evidence,
                "claim": (
                    (
                        f"Offense can attack {label}; supplied tendency metrics "
                        f"produce a {edge:+.3f} edge."
                    )
                    if side == "offense"
                    else (
                        f"Defense is positioned well against {label}; supplied tendency "
                        f"metrics produce a {edge:+.3f} edge."
                    )
                    if side == "defense"
                    else f"{label} projects as neutral from the supplied tendency metrics."
                ),
            }
        )

    matchups.sort(key=lambda row: (-row["evidence_score"], row["label"]))
    weighted_total = sum(row["edge"] * max(row["evidence_score"], 0.0001) for row in matchups)
    total_weight = sum(max(row["evidence_score"], 0.0001) for row in matchups)
    overall_edge = weighted_total / total_weight if total_weight else 0.0
    return {
        "version": VERSION,
        "method": "matched_tendency_comparison",
        "defense_metric_semantics": "rates_allowed",
        "metrics": selected,
        "min_snaps": minimum,
        "labels_compared": len(matchups),
        "overall_edge": round(overall_edge, 4),
        "lean": _lean(overall_edge),
        "matchups": matchups,
        "opportunities": [
            row for row in matchups if row["advantage"] == "offense"
        ][:bounded_limit],
        "risks": [
            row for row in matchups if row["advantage"] == "defense"
        ][:bounded_limit],
        "unmatched_labels": {
            "offense_only": sorted(set(offense_rows) - set(defense_rows)),
            "defense_only": sorted(set(defense_rows) - set(offense_rows)),
        },
        "insufficient_samples": insufficient,
    }


def matchup_brief(
    profile_matchup: Mapping[str, Any],
    tendency_matchup: Mapping[str, Any] | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """Create a bounded matchup brief ranked only from supplied comparison evidence."""
    bounded_limit = _bounded_int(limit, 5, 1, 10)
    evidence = []
    for row in profile_matchup.get("comparisons", []):
        if not isinstance(row, Mapping) or row.get("advantage") == "neutral":
            continue
        evidence.append(
            {
                "source": "profile",
                "label": str(row.get("label") or "metric")[:120],
                "advantage": row.get("advantage"),
                "edge": round(_number(row.get("normalized_edge")) or 0.0, 4),
                "evidence_score": round(_number(row.get("evidence_score")) or 0.0, 4),
                "claim": str(row.get("claim") or "")[:300],
                "sample_context": row.get("sample_context"),
            }
        )
    tendency_matchup = tendency_matchup or {}
    for row in tendency_matchup.get("matchups", []):
        if not isinstance(row, Mapping) or row.get("advantage") == "neutral":
            continue
        evidence.append(
            {
                "source": "tendency",
                "label": str(row.get("label") or "tendency")[:120],
                "advantage": row.get("advantage"),
                "edge": round(_number(row.get("edge")) or 0.0, 4),
                "evidence_score": round(_number(row.get("evidence_score")) or 0.0, 4),
                "claim": str(row.get("claim") or "")[:300],
                "sample_context": row.get("sample_context"),
            }
        )
    evidence.sort(
        key=lambda row: (-row["evidence_score"], row["source"], row["label"])
    )
    strengths = [row for row in evidence if row["advantage"] == "offense"][:bounded_limit]
    risks = [row for row in evidence if row["advantage"] == "defense"][:bounded_limit]
    ranked = evidence[: bounded_limit * 2]
    weighted_total = sum(row["edge"] * max(row["evidence_score"], 0.0001) for row in ranked)
    total_weight = sum(max(row["evidence_score"], 0.0001) for row in ranked)
    overall_edge = weighted_total / total_weight if total_weight else 0.0

    profile_confidence = profile_matchup.get("confidence", {})
    confidence_score = _number(
        profile_confidence.get("score") if isinstance(profile_confidence, Mapping) else None
    )
    if confidence_score is None:
        confidence_score = 0.0
    if tendency_matchup.get("labels_compared"):
        tendency_support = [
            _number(row.get("sample_context", {}).get("support"))
            for row in tendency_matchup.get("matchups", [])
            if isinstance(row, Mapping) and isinstance(row.get("sample_context"), Mapping)
        ]
        tendency_support = [value for value in tendency_support if value is not None]
        if tendency_support:
            confidence_score = (
                confidence_score + sum(tendency_support) / len(tendency_support)
            ) / 2
    confidence_level = (
        "high" if confidence_score >= 0.75 else "moderate" if confidence_score >= 0.45 else "low"
    )
    lean = _lean(overall_edge)
    if not ranked:
        summary = "Insufficient supplied evidence for a matchup lean."
    elif lean == "offense":
        summary = "The supplied evidence gives the offense the matchup lean."
    elif lean == "defense":
        summary = "The supplied evidence gives the defense the matchup lean."
    else:
        summary = "The supplied evidence projects an even matchup."
    return {
        "version": VERSION,
        "method": "evidence_ranked_matchup_brief",
        "grounded": True,
        "offense": profile_matchup.get("offense"),
        "defense": profile_matchup.get("defense"),
        "overall_edge": round(overall_edge, 4),
        "lean": lean,
        "confidence": {
            "score": round(confidence_score, 4),
            "level": confidence_level,
        },
        "summary": summary,
        "strengths": strengths,
        "risks": risks,
        "ranked_evidence": ranked,
        "evidence_count": len(evidence),
    }
