"""Deterministic scouting-workspace services for NFL Analytics Hub v4.1.3."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

VERSION = "4.1.3"
REPORT_TYPES = {
    "player-comparison",
    "team-style-map",
    "tendency-explorer",
    "matchup-card",
    "history-review",
}
_PANELS = (
    {
        "id": "player-comparison",
        "label": "Player comparison",
        "endpoint": "/api/v4.1/scouting/player-similarity",
        "method": "POST",
    },
    {
        "id": "team-style-map",
        "label": "Team-style map",
        "endpoint": "/api/v4.1/scouting/team-styles/cluster",
        "method": "POST",
    },
    {
        "id": "tendency-explorer",
        "label": "Tendency explorer",
        "endpoint": "/api/v4.1/scouting/tendencies",
        "method": "POST",
    },
    {
        "id": "matchup-card",
        "label": "Matchup card",
        "endpoint": "/api/v4.1/scouting/matchups/brief",
        "method": "POST",
    },
    {
        "id": "history-review",
        "label": "History review",
        "endpoint": "/api/v4.1/scouting/history/tendencies",
        "method": "POST",
    },
)


def _bounded_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, low), high)


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _json_safe(value: Any) -> Any:
    """Return a bounded JSON-compatible copy or raise ValueError."""
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("result must contain JSON-compatible values") from exc
    if len(encoded.encode("utf-8")) > 250_000:
        raise ValueError("result must be 250 KB or smaller")
    return json.loads(encoded)


def workspace_manifest() -> dict[str, Any]:
    """Describe the stable v4.1.3 workspace contract."""
    return {
        "version": VERSION,
        "release": "scouting-workspace",
        "panels": [dict(panel) for panel in _PANELS],
        "report_types": sorted(REPORT_TYPES),
        "saved_reports": {
            "storage": "browser-local",
            "server_persistence": False,
            "normalization_endpoint": (
                "/api/v4.1/scouting/workspace/reports/normalize"
            ),
            "review_endpoint": "/api/v4.1/scouting/workspace/reports/review",
        },
        "guardrails": [
            "Analyses use only caller-supplied structured inputs.",
            "Saved reports remain in the current browser unless exported.",
            "Every report preserves its source endpoint and complete result.",
            "Review ordering is deterministic and bounded.",
        ],
    }


def normalize_workspace_report(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize one browser-stored scouting report."""
    if not isinstance(payload, Mapping):
        raise ValueError("report must be a JSON object")
    report_type = _text(payload.get("type"), 40)
    if report_type not in REPORT_TYPES:
        raise ValueError("type must be a supported workspace report type")
    title = _text(payload.get("title"), 120)
    if not title:
        raise ValueError("title is required")
    result = payload.get("result")
    if not isinstance(result, (Mapping, list)):
        raise ValueError("result must be a JSON object or list")
    safe_result = _json_safe(result)
    source_endpoint = _text(payload.get("source_endpoint"), 200)
    if not source_endpoint.startswith("/api/v4.1/scouting/"):
        raise ValueError("source_endpoint must be a v4.1 scouting endpoint")

    tags = []
    supplied_tags = payload.get("tags", [])
    if supplied_tags is not None and not isinstance(supplied_tags, Sequence):
        raise ValueError("tags must be a list")
    if isinstance(supplied_tags, (str, bytes)):
        raise ValueError("tags must be a list")
    for value in supplied_tags or []:
        tag = _text(value, 30)
        if tag and tag not in tags:
            tags.append(tag)
        if len(tags) == 10:
            break

    identity = {
        "type": report_type,
        "title": title,
        "source_endpoint": source_endpoint,
        "result": safe_result,
    }
    report_id = _text(payload.get("report_id"), 80)
    if not report_id:
        canonical = json.dumps(
            identity,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        report_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]

    return {
        "version": VERSION,
        "report": {
            "report_id": report_id,
            "type": report_type,
            "title": title,
            "source_endpoint": source_endpoint,
            "created_at": _text(payload.get("created_at"), 40) or None,
            "updated_at": _text(payload.get("updated_at"), 40) or None,
            "tags": tags,
            "pinned": bool(payload.get("pinned", False)),
            "result": safe_result,
        },
    }


def build_review_queue(
    reports: Sequence[Mapping[str, Any]],
    limit: int = 25,
) -> dict[str, Any]:
    """Build a compact deterministic queue for phone-sized report review."""
    if isinstance(reports, (str, bytes)) or not isinstance(reports, Sequence):
        raise ValueError("reports must be a list")
    bounded_limit = _bounded_int(limit, 25, 1, 50)
    normalized = []
    invalid = []
    for index, report in enumerate(reports[:100]):
        try:
            item = normalize_workspace_report(report)["report"]
        except ValueError as exc:
            invalid.append({"index": index, "reason": str(exc)})
            continue
        result = item["result"]
        if isinstance(result, Mapping):
            evidence_count = next(
                (
                    len(result[key])
                    for key in (
                        "ranked_evidence",
                        "matches",
                        "clusters",
                        "tendencies",
                        "changes",
                    )
                    if isinstance(result.get(key), list)
                ),
                len(result),
            )
        else:
            evidence_count = len(result)
        normalized.append(
            {
                "report_id": item["report_id"],
                "type": item["type"],
                "title": item["title"],
                "updated_at": item["updated_at"] or item["created_at"],
                "tags": item["tags"],
                "pinned": item["pinned"],
                "evidence_count": evidence_count,
            }
        )

    normalized.sort(
        key=lambda row: (
            not row["pinned"],
            -(len(row["updated_at"] or "")),
            row["updated_at"] or "",
            row["title"].lower(),
            row["report_id"],
        )
    )
    pinned = [row for row in normalized if row["pinned"]]
    unpinned = [row for row in normalized if not row["pinned"]]
    pinned.sort(key=lambda row: (row["title"].lower(), row["report_id"]))
    unpinned.sort(
        key=lambda row: (
            row["updated_at"] or "",
            row["title"].lower(),
            row["report_id"],
        ),
        reverse=True,
    )
    queue = (pinned + unpinned)[:bounded_limit]
    return {
        "version": VERSION,
        "reports_received": len(reports),
        "reports_available": len(normalized),
        "limit": bounded_limit,
        "queue": queue,
        "invalid_reports": invalid,
    }
