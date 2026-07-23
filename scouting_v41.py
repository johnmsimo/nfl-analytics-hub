"""Deterministic advanced-scouting services for NFL Analytics Hub v4.1."""
from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

VERSION = "4.1.0"
_IDENTITY_FIELDS = {
    "id",
    "name",
    "player_id",
    "player_name",
    "team_id",
    "team",
    "season",
    "week",
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
        or profile.get("player_id")
        or profile.get("team_id")
        or profile.get("name")
        or fallback
    )[:120]
    name = str(
        profile.get("name")
        or profile.get("player_name")
        or profile.get("team")
        or entity_id
    )[:120]
    return {"id": entity_id, "name": name}


def _feature_names(
    profiles: Sequence[Mapping[str, Any]], metrics: Sequence[str] | None
) -> list[str]:
    if metrics:
        requested = [str(metric).strip() for metric in metrics]
        return sorted({metric for metric in requested if metric and metric not in _IDENTITY_FIELDS})
    names = {
        str(key)
        for profile in profiles
        for key, value in profile.items()
        if key not in _IDENTITY_FIELDS and _number(value) is not None
    }
    return sorted(names)


def _ranges(
    profiles: Sequence[Mapping[str, Any]], features: Sequence[str]
) -> dict[str, tuple[float, float]]:
    result = {}
    for feature in features:
        values = [
            value
            for profile in profiles
            if (value := _number(profile.get(feature))) is not None
        ]
        if values:
            result[feature] = (min(values), max(values))
    return result


def _scaled(value: float, bounds: tuple[float, float]) -> float:
    low, high = bounds
    return 0.5 if high == low else (value - low) / (high - low)


def player_similarity(
    target: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    metrics: Sequence[str] | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """Rank comparable players using transparent range-normalized distance."""
    valid_candidates = [candidate for candidate in candidates if isinstance(candidate, Mapping)]
    profiles = [target, *valid_candidates]
    features = _feature_names(profiles, metrics)
    ranges = _ranges(profiles, features)
    target_id = _identity(target, "target")
    matches = []
    for index, candidate in enumerate(valid_candidates):
        deltas = []
        for feature in features:
            target_value = _number(target.get(feature))
            candidate_value = _number(candidate.get(feature))
            bounds = ranges.get(feature)
            if target_value is None or candidate_value is None or bounds is None:
                continue
            normalized_delta = abs(_scaled(target_value, bounds) - _scaled(candidate_value, bounds))
            deltas.append(
                {
                    "feature": feature,
                    "target": round(target_value, 4),
                    "candidate": round(candidate_value, 4),
                    "normalized_delta": round(normalized_delta, 4),
                }
            )
        distance = math.sqrt(sum(item["normalized_delta"] ** 2 for item in deltas) / len(deltas)) if deltas else 1.0
        similarity = max(0.0, 1.0 - distance)
        deltas.sort(key=lambda item: (-item["normalized_delta"], item["feature"]))
        matches.append(
            {
                **_identity(candidate, f"candidate-{index + 1}"),
                "similarity": round(similarity, 4),
                "feature_coverage": round(len(deltas) / max(len(features), 1), 4),
                "largest_differences": deltas[:5],
            }
        )
    matches.sort(key=lambda item: (-item["similarity"], item["name"], item["id"]))
    bounded_limit = min(max(int(limit), 1), 25)
    return {
        "version": VERSION,
        "method": "range_normalized_euclidean",
        "target": target_id,
        "features": features,
        "candidate_count": len(valid_candidates),
        "matches": matches[:bounded_limit],
    }


def cluster_team_styles(
    teams: Sequence[Mapping[str, Any]],
    metrics: Sequence[str] | None = None,
    cluster_count: int = 3,
    max_iterations: int = 25,
) -> dict[str, Any]:
    """Cluster supplied team profiles with deterministic, explainable k-means."""
    profiles = [team for team in teams if isinstance(team, Mapping)]
    features = _feature_names(profiles, metrics)
    ranges = _ranges(profiles, features)
    usable_features = [feature for feature in features if feature in ranges]
    if not profiles or not usable_features:
        return {
            "version": VERSION,
            "method": "deterministic_kmeans",
            "features": usable_features,
            "team_count": len(profiles),
            "clusters": [],
            "iterations": 0,
        }

    ordered = sorted(enumerate(profiles), key=lambda item: tuple(_identity(item[1], str(item[0])).values()))
    vectors = []
    for original_index, profile in ordered:
        vector = []
        for feature in usable_features:
            value = _number(profile.get(feature))
            low, high = ranges[feature]
            vector.append(_scaled(value if value is not None else (low + high) / 2, (low, high)))
        vectors.append((original_index, profile, vector))

    k = min(max(int(cluster_count), 1), min(8, len(vectors)))
    seeds = [min((index * len(vectors)) // k, len(vectors) - 1) for index in range(k)]
    centroids = [vectors[index][2][:] for index in seeds]
    assignments = [-1] * len(vectors)
    iteration_count = 0
    for iteration_count in range(1, min(max(int(max_iterations), 1), 100) + 1):
        new_assignments = []
        for _, _, vector in vectors:
            distances = [
                sum((value - centroid[pos]) ** 2 for pos, value in enumerate(vector))
                for centroid in centroids
            ]
            new_assignments.append(min(range(k), key=lambda index: (distances[index], index)))
        if new_assignments == assignments:
            break
        assignments = new_assignments
        for cluster_id in range(k):
            members = [vectors[index][2] for index, value in enumerate(assignments) if value == cluster_id]
            if members:
                centroids[cluster_id] = [
                    sum(member[pos] for member in members) / len(members)
                    for pos in range(len(usable_features))
                ]

    clusters = []
    for cluster_id in range(k):
        member_rows = [
            _identity(vectors[index][1], str(index))
            for index, assignment in enumerate(assignments)
            if assignment == cluster_id
        ]
        signature = sorted(
            (
                {
                    "feature": feature,
                    "normalized_level": round(centroids[cluster_id][position], 4),
                }
                for position, feature in enumerate(usable_features)
            ),
            key=lambda item: (-abs(item["normalized_level"] - 0.5), item["feature"]),
        )
        clusters.append(
            {
                "cluster_id": cluster_id + 1,
                "member_count": len(member_rows),
                "members": member_rows,
                "style_signature": signature[:5],
            }
        )
    return {
        "version": VERSION,
        "method": "deterministic_kmeans",
        "features": usable_features,
        "team_count": len(profiles),
        "cluster_count": k,
        "iterations": iteration_count,
        "clusters": clusters,
    }


def _play_flag(play: Mapping[str, Any], key: str) -> bool:
    value = play.get(key)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "pass", "success", "explosive"}
    return bool(value)


def personnel_tendencies(
    plays: Sequence[Mapping[str, Any]], min_snaps: int = 1
) -> dict[str, Any]:
    """Summarize grounded personnel, formation, and combination tendencies."""
    valid_plays = [play for play in plays if isinstance(play, Mapping)]
    minimum = min(max(int(min_snaps), 1), 500)

    def aggregate(keys: Sequence[str]) -> list[dict[str, Any]]:
        groups: dict[str, dict[str, float]] = defaultdict(
            lambda: {"snaps": 0.0, "passes": 0.0, "successes": 0.0, "explosives": 0.0, "yards": 0.0}
        )
        for play in valid_plays:
            values = [str(play.get(key) or "unknown")[:80] for key in keys]
            label = " | ".join(values)
            row = groups[label]
            row["snaps"] += 1
            play_type = str(play.get("play_type") or "").lower()
            row["passes"] += float(_play_flag(play, "is_pass") or play_type == "pass")
            row["successes"] += float(_play_flag(play, "success"))
            yards = _number(play.get("yards_gained")) or 0.0
            row["explosives"] += float(_play_flag(play, "explosive") or yards >= 20)
            row["yards"] += yards
        result = []
        for label, row in groups.items():
            snaps = int(row["snaps"])
            if snaps < minimum:
                continue
            result.append(
                {
                    "label": label,
                    "snaps": snaps,
                    "usage_rate": round(snaps / max(len(valid_plays), 1), 4),
                    "pass_rate": round(row["passes"] / snaps, 4),
                    "success_rate": round(row["successes"] / snaps, 4),
                    "explosive_rate": round(row["explosives"] / snaps, 4),
                    "yards_per_play": round(row["yards"] / snaps, 2),
                }
            )
        result.sort(key=lambda item: (-item["snaps"], item["label"]))
        return result

    return {
        "version": VERSION,
        "method": "supplied_play_aggregation",
        "play_count": len(valid_plays),
        "min_snaps": minimum,
        "personnel": aggregate(["personnel"]),
        "formations": aggregate(["formation"]),
        "combinations": aggregate(["personnel", "formation"]),
    }
