"""Deterministic scouting-history services for NFL Analytics Hub v4.1.2."""
from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

VERSION = "4.1.2"
_TREND_METRICS = {
    "usage_rate": 1.0,
    "pass_rate": 1.0,
    "success_rate": 1.0,
    "explosive_rate": 1.0,
    "yards_per_play": 10.0,
}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _bounded_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, low), high)


def _period(row: Mapping[str, Any], index: int) -> tuple[str, float, int]:
    label = row.get("period")
    if label is None:
        season = row.get("season")
        week = row.get("week")
        label = (
            f"{season}-W{week}"
            if season is not None and week is not None
            else season if season is not None else f"period-{index + 1}"
        )
    order = _number(row.get("sort_key"))
    if order is None:
        season = _number(row.get("season"))
        week = _number(row.get("week"))
        order = season * 100 + (week or 0) if season is not None else float(index)
    return str(label)[:120], order, index


def _sample(row: Mapping[str, Any]) -> float | None:
    for key in ("sample_size", "snaps", "plays"):
        value = _number(row.get(key))
        if value is not None and value >= 0:
            return value
    return None


def _direction(delta: float, threshold: float = 0.01) -> str:
    if delta > threshold:
        return "increased"
    if delta < -threshold:
        return "decreased"
    return "stable"


def analyze_tendency_changes(
    snapshots: Sequence[Mapping[str, Any]],
    metrics: Sequence[str] | None = None,
    min_snaps: int = 10,
    limit: int = 10,
) -> dict[str, Any]:
    """Compare the earliest and latest supplied tendency snapshots by label."""
    minimum = _bounded_int(min_snaps, 10, 1, 10000)
    bounded_limit = _bounded_int(limit, 10, 1, 25)
    requested = metrics or list(_TREND_METRICS)
    selected = list(
        dict.fromkeys(
            str(metric).strip()
            for metric in requested
            if str(metric).strip() in _TREND_METRICS
        )
    )
    grouped: dict[str, list[tuple[tuple[str, float, int], Mapping[str, Any]]]] = (
        defaultdict(list)
    )
    insufficient = []
    valid_rows = [row for row in snapshots if isinstance(row, Mapping)]
    for index, row in enumerate(valid_rows):
        label = str(row.get("label") or "").strip()[:120]
        sample = _sample(row)
        if not label:
            insufficient.append({"index": index, "reason": "label is required"})
        elif sample is None or sample < minimum:
            insufficient.append(
                {
                    "label": label,
                    "period": _period(row, index)[0],
                    "sample_size": None if sample is None else int(sample),
                    "reason": "minimum sample not met",
                }
            )
        else:
            grouped[label].append((_period(row, index), row))

    changes = []
    for label, observations in grouped.items():
        observations.sort(key=lambda item: (item[0][1], item[0][2], item[0][0]))
        if len(observations) < 2:
            insufficient.append(
                {
                    "label": label,
                    "period": observations[0][0][0],
                    "reason": "at least two periods are required",
                }
            )
            continue
        first_period, first = observations[0]
        last_period, last = observations[-1]
        metric_changes = []
        for metric in selected:
            first_value = _number(first.get(metric))
            last_value = _number(last.get(metric))
            if first_value is None or last_value is None:
                continue
            raw_delta = last_value - first_value
            normalized_delta = min(
                max(raw_delta / _TREND_METRICS[metric], -1.0),
                1.0,
            )
            percent_change = None if first_value == 0 else raw_delta / abs(first_value)
            metric_changes.append(
                {
                    "metric": metric,
                    "first_value": round(first_value, 4),
                    "last_value": round(last_value, 4),
                    "raw_delta": round(raw_delta, 4),
                    "normalized_delta": round(normalized_delta, 4),
                    "percent_change": (
                        None if percent_change is None else round(percent_change, 4)
                    ),
                    "direction": _direction(normalized_delta),
                }
            )
        if not metric_changes:
            insufficient.append(
                {
                    "label": label,
                    "reason": "no shared numeric tendency metrics",
                }
            )
            continue
        change_score = sum(
            abs(item["normalized_delta"]) for item in metric_changes
        ) / len(metric_changes)
        changes.append(
            {
                "label": label,
                "first_period": first_period[0],
                "last_period": last_period[0],
                "periods_observed": len(observations),
                "first_sample_size": int(_sample(first) or 0),
                "last_sample_size": int(_sample(last) or 0),
                "change_score": round(change_score, 4),
                "metrics": metric_changes,
            }
        )

    changes.sort(key=lambda row: (-row["change_score"], row["label"]))
    return {
        "version": VERSION,
        "method": "earliest_latest_tendency_change",
        "metrics": selected,
        "min_snaps": minimum,
        "snapshots_received": len(valid_rows),
        "labels_analyzed": len(changes),
        "changes": changes[:bounded_limit],
        "insufficient_history": sorted(
            insufficient,
            key=lambda row: (
                str(row.get("label") or ""),
                str(row.get("period") or ""),
                str(row.get("reason") or ""),
            ),
        ),
    }


def track_role_transitions(
    snapshots: Sequence[Mapping[str, Any]],
    min_snaps: int = 1,
    limit: int = 25,
) -> dict[str, Any]:
    """Detect supplied team, role, and snap-share transitions for each player."""
    minimum = _bounded_int(min_snaps, 1, 0, 10000)
    bounded_limit = _bounded_int(limit, 25, 1, 50)
    grouped: dict[str, list[tuple[tuple[str, float, int], Mapping[str, Any]]]] = (
        defaultdict(list)
    )
    excluded = []
    valid_rows = [row for row in snapshots if isinstance(row, Mapping)]
    for index, row in enumerate(valid_rows):
        player_id = str(
            row.get("player_id") or row.get("id") or row.get("player_name") or ""
        ).strip()[:120]
        sample = _sample(row)
        if not player_id:
            excluded.append({"index": index, "reason": "player_id is required"})
        elif sample is not None and sample < minimum:
            excluded.append(
                {
                    "player_id": player_id,
                    "period": _period(row, index)[0],
                    "sample_size": int(sample),
                    "reason": "minimum sample not met",
                }
            )
        else:
            grouped[player_id].append((_period(row, index), row))

    transitions = []
    current_roster = []
    for player_id, observations in grouped.items():
        observations.sort(key=lambda item: (item[0][1], item[0][2], item[0][0]))
        latest_period, latest = observations[-1]
        current_roster.append(
            {
                "player_id": player_id,
                "player_name": str(latest.get("player_name") or player_id)[:120],
                "period": latest_period[0],
                "team": str(latest.get("team") or latest.get("team_id") or "")[:120],
                "role": str(latest.get("role") or "")[:120],
                "snap_share": _number(latest.get("snap_share")),
            }
        )
        for (previous_period, previous), (current_period, current) in zip(
            observations,
            observations[1:],
        ):
            previous_team = str(
                previous.get("team") or previous.get("team_id") or ""
            )[:120]
            current_team = str(
                current.get("team") or current.get("team_id") or ""
            )[:120]
            previous_role = str(previous.get("role") or "")[:120]
            current_role = str(current.get("role") or "")[:120]
            previous_share = _number(previous.get("snap_share"))
            current_share = _number(current.get("snap_share"))
            share_delta = (
                None
                if previous_share is None or current_share is None
                else current_share - previous_share
            )
            events = []
            if previous_team != current_team:
                events.append("team_change")
            if previous_role != current_role:
                events.append("role_change")
            if share_delta is not None and abs(share_delta) >= 0.05:
                events.append("snap_share_change")
            if not events:
                continue
            significance = max(
                abs(share_delta) if share_delta is not None else 0.0,
                0.5 if "team_change" in events else 0.0,
                0.25 if "role_change" in events else 0.0,
            )
            transitions.append(
                {
                    "player_id": player_id,
                    "player_name": str(
                        current.get("player_name")
                        or previous.get("player_name")
                        or player_id
                    )[:120],
                    "from_period": previous_period[0],
                    "to_period": current_period[0],
                    "events": events,
                    "from_team": previous_team,
                    "to_team": current_team,
                    "from_role": previous_role,
                    "to_role": current_role,
                    "from_snap_share": (
                        None if previous_share is None else round(previous_share, 4)
                    ),
                    "to_snap_share": (
                        None if current_share is None else round(current_share, 4)
                    ),
                    "snap_share_delta": (
                        None if share_delta is None else round(share_delta, 4)
                    ),
                    "significance": round(significance, 4),
                }
            )

    transitions.sort(
        key=lambda row: (
            -row["significance"],
            row["player_name"],
            row["to_period"],
        )
    )
    current_roster.sort(key=lambda row: (row["team"], row["role"], row["player_name"]))
    return {
        "version": VERSION,
        "method": "observed_roster_role_transitions",
        "min_snaps": minimum,
        "snapshots_received": len(valid_rows),
        "players_observed": len(grouped),
        "transition_count": len(transitions),
        "transitions": transitions[:bounded_limit],
        "current_roster": current_roster,
        "excluded_snapshots": excluded,
    }


def opponent_adjusted_splits(
    splits: Sequence[Mapping[str, Any]],
    metrics: Sequence[Mapping[str, Any]],
    min_sample: int = 10,
    limit: int = 25,
) -> dict[str, Any]:
    """Compare supplied split values with explicit opponent baselines."""
    minimum = _bounded_int(min_sample, 10, 1, 10000)
    bounded_limit = _bounded_int(limit, 25, 1, 50)
    rules = [rule for rule in metrics if isinstance(rule, Mapping)]
    valid_rows = [row for row in splits if isinstance(row, Mapping)]
    adjusted = []
    unavailable = []
    aggregates: dict[str, dict[str, float]] = defaultdict(
        lambda: {"weighted_delta": 0.0, "weight": 0.0, "samples": 0.0}
    )
    for index, row in enumerate(valid_rows):
        opponent = str(row.get("opponent") or row.get("opponent_id") or "").strip()[:120]
        context = str(row.get("context") or row.get("split") or "overall")[:120]
        sample = _sample(row)
        if not opponent:
            unavailable.append({"index": index, "reason": "opponent is required"})
            continue
        if sample is None or sample < minimum:
            unavailable.append(
                {
                    "opponent": opponent,
                    "context": context,
                    "sample_size": None if sample is None else int(sample),
                    "reason": "minimum sample not met",
                }
            )
            continue
        evidence = []
        for rule_index, rule in enumerate(rules):
            metric = str(rule.get("metric") or "").strip()
            baseline_metric = str(
                rule.get("baseline_metric") or f"{metric}_opponent_baseline"
            ).strip()
            label = str(rule.get("label") or metric or f"metric-{rule_index + 1}")[:120]
            actual = _number(row.get(metric))
            baseline = _number(row.get(baseline_metric))
            if not metric or actual is None or baseline is None:
                unavailable.append(
                    {
                        "opponent": opponent,
                        "context": context,
                        "label": label,
                        "reason": "metric and opponent baseline are required",
                    }
                )
                continue
            scale = _number(rule.get("scale"))
            scale = scale if scale is not None and scale > 0 else max(
                abs(actual),
                abs(baseline),
                1.0,
            )
            raw_delta = actual - baseline
            direction = str(rule.get("direction") or "higher").strip().lower()
            lower_is_better = direction in {"lower", "lower_is_better", "ascending"}
            adjusted_delta = -raw_delta if lower_is_better else raw_delta
            normalized_delta = min(max(adjusted_delta / scale, -1.0), 1.0)
            evidence.append(
                {
                    "label": label,
                    "metric": metric,
                    "baseline_metric": baseline_metric,
                    "actual": round(actual, 4),
                    "opponent_baseline": round(baseline, 4),
                    "raw_delta": round(raw_delta, 4),
                    "adjusted_delta": round(adjusted_delta, 4),
                    "normalized_delta": round(normalized_delta, 4),
                    "direction": (
                        "lower_is_better" if lower_is_better else "higher_is_better"
                    ),
                }
            )
            aggregate = aggregates[label]
            aggregate["weighted_delta"] += normalized_delta * sample
            aggregate["weight"] += sample
            aggregate["samples"] += sample
        if not evidence:
            continue
        score = sum(item["normalized_delta"] for item in evidence) / len(evidence)
        adjusted.append(
            {
                "opponent": opponent,
                "context": context,
                "sample_size": int(sample),
                "adjusted_score": round(score, 4),
                "performance": (
                    "above_expected"
                    if score > 0.01
                    else "below_expected" if score < -0.01 else "as_expected"
                ),
                "metrics": evidence,
            }
        )

    adjusted.sort(
        key=lambda row: (-abs(row["adjusted_score"]), row["opponent"], row["context"])
    )
    metric_summary = []
    for label, aggregate in aggregates.items():
        score = (
            aggregate["weighted_delta"] / aggregate["weight"]
            if aggregate["weight"]
            else 0.0
        )
        metric_summary.append(
            {
                "label": label,
                "opponent_adjusted_score": round(score, 4),
                "sample_size": int(aggregate["samples"]),
            }
        )
    metric_summary.sort(
        key=lambda row: (-abs(row["opponent_adjusted_score"]), row["label"])
    )
    return {
        "version": VERSION,
        "method": "explicit_opponent_baseline_adjustment",
        "min_sample": minimum,
        "splits_received": len(valid_rows),
        "splits_adjusted": len(adjusted),
        "metric_summary": metric_summary,
        "adjusted_splits": adjusted[:bounded_limit],
        "unavailable_evidence": unavailable,
    }


def compare_seasons(
    seasons: Sequence[Mapping[str, Any]],
    metrics: Sequence[Mapping[str, Any]],
    min_sample: int = 1,
    limit: int = 10,
) -> dict[str, Any]:
    """Compare consecutive supplied season profiles with explicit metric semantics."""
    minimum = _bounded_int(min_sample, 1, 1, 100000)
    bounded_limit = _bounded_int(limit, 10, 1, 25)
    rules = [rule for rule in metrics if isinstance(rule, Mapping)]
    eligible = []
    excluded = []
    valid_rows = [row for row in seasons if isinstance(row, Mapping)]
    for index, row in enumerate(valid_rows):
        sample = _sample(row)
        if sample is not None and sample < minimum:
            excluded.append(
                {
                    "season": str(row.get("season") or f"season-{index + 1}")[:120],
                    "sample_size": int(sample),
                    "reason": "minimum sample not met",
                }
            )
        else:
            eligible.append((_period(row, index), row))
    eligible.sort(key=lambda item: (item[0][1], item[0][2], item[0][0]))

    comparisons = []
    unavailable = []
    for (previous_period, previous), (current_period, current) in zip(
        eligible,
        eligible[1:],
    ):
        evidence = []
        for rule_index, rule in enumerate(rules):
            metric = str(rule.get("metric") or "").strip()
            label = str(rule.get("label") or metric or f"metric-{rule_index + 1}")[:120]
            previous_value = _number(previous.get(metric))
            current_value = _number(current.get(metric))
            if not metric or previous_value is None or current_value is None:
                unavailable.append(
                    {
                        "from_season": previous_period[0],
                        "to_season": current_period[0],
                        "label": label,
                        "reason": "shared numeric metric is required",
                    }
                )
                continue
            scale = _number(rule.get("scale"))
            scale = scale if scale is not None and scale > 0 else max(
                abs(previous_value),
                abs(current_value),
                1.0,
            )
            raw_delta = current_value - previous_value
            direction = str(rule.get("direction") or "higher").strip().lower()
            lower_is_better = direction in {"lower", "lower_is_better", "ascending"}
            improvement = -raw_delta if lower_is_better else raw_delta
            normalized_change = min(max(improvement / scale, -1.0), 1.0)
            evidence.append(
                {
                    "label": label,
                    "metric": metric,
                    "previous_value": round(previous_value, 4),
                    "current_value": round(current_value, 4),
                    "raw_delta": round(raw_delta, 4),
                    "normalized_change": round(normalized_change, 4),
                    "outcome": (
                        "improved"
                        if normalized_change > 0.01
                        else "declined" if normalized_change < -0.01 else "stable"
                    ),
                }
            )
        if not evidence:
            continue
        score = sum(item["normalized_change"] for item in evidence) / len(evidence)
        comparisons.append(
            {
                "from_season": previous_period[0],
                "to_season": current_period[0],
                "change_score": round(score, 4),
                "trend": (
                    "improved"
                    if score > 0.01
                    else "declined" if score < -0.01 else "stable"
                ),
                "metrics": evidence,
                "sample_context": {
                    "previous": (
                        None
                        if _sample(previous) is None
                        else int(_sample(previous) or 0)
                    ),
                    "current": (
                        None if _sample(current) is None else int(_sample(current) or 0)
                    ),
                    "minimum": minimum,
                },
            }
        )

    comparisons = comparisons[-bounded_limit:]
    identity = eligible[-1][1] if eligible else {}
    entity_id = str(
        identity.get("entity_id")
        or identity.get("player_id")
        or identity.get("team_id")
        or identity.get("id")
        or "entity"
    )[:120]
    return {
        "version": VERSION,
        "method": "consecutive_season_comparison",
        "entity": {
            "id": entity_id,
            "name": str(
                identity.get("name")
                or identity.get("player_name")
                or identity.get("team")
                or entity_id
            )[:120],
        },
        "min_sample": minimum,
        "seasons_received": len(valid_rows),
        "seasons_compared": len(eligible),
        "comparison_count": len(comparisons),
        "latest_comparison": comparisons[-1] if comparisons else None,
        "comparisons": comparisons,
        "excluded_seasons": excluded,
        "unavailable_evidence": unavailable,
    }
