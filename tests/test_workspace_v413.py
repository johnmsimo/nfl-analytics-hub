import pytest

from workspace_v413 import (
    build_review_queue,
    normalize_workspace_report,
    workspace_manifest,
)


def _report(**updates):
    report = {
        "type": "matchup-card",
        "title": "PHI offense vs DAL defense",
        "source_endpoint": "/api/v4.1/scouting/matchups/brief",
        "result": {"ranked_evidence": [{"label": "Success rate"}]},
        "tags": ["week-1", "NFC East"],
    }
    report.update(updates)
    return report


def test_manifest_exposes_all_workspace_panels_and_storage_disclosure():
    result = workspace_manifest()
    assert result["version"] == "4.1.3"
    assert {panel["id"] for panel in result["panels"]} == {
        "player-comparison",
        "team-style-map",
        "tendency-explorer",
        "matchup-card",
        "history-review",
    }
    assert result["saved_reports"]["storage"] == "browser-local"
    assert result["saved_reports"]["server_persistence"] is False


def test_report_normalization_is_deterministic():
    first = normalize_workspace_report(_report())
    second = normalize_workspace_report(_report())
    assert first == second
    assert len(first["report"]["report_id"]) == 20


def test_report_normalization_preserves_explicit_identity_and_deduplicates_tags():
    result = normalize_workspace_report(
        _report(report_id="scout-13", tags=["week-1", "week-1", "red-zone"])
    )
    assert result["report"]["report_id"] == "scout-13"
    assert result["report"]["tags"] == ["week-1", "red-zone"]


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"type": "unknown"}, "supported workspace report type"),
        ({"title": ""}, "title is required"),
        ({"result": "not-json-structure"}, "JSON object or list"),
        ({"source_endpoint": "/api/v3/legacy"}, "v4.1 scouting endpoint"),
        ({"tags": "week-1"}, "tags must be a list"),
    ],
)
def test_report_normalization_rejects_invalid_contracts(update, message):
    with pytest.raises(ValueError, match=message):
        normalize_workspace_report(_report(**update))


def test_report_normalization_rejects_non_finite_numbers():
    with pytest.raises(ValueError, match="JSON-compatible"):
        normalize_workspace_report(_report(result={"score": float("nan")}))


def test_review_queue_prioritizes_pinned_then_recent_reports():
    result = build_review_queue(
        [
            _report(
                title="Older",
                report_id="old",
                updated_at="2026-01-01T00:00:00Z",
            ),
            _report(
                title="Pinned",
                report_id="pin",
                pinned=True,
                updated_at="2025-01-01T00:00:00Z",
            ),
            _report(
                title="Newest",
                report_id="new",
                updated_at="2026-07-23T12:00:00Z",
            ),
        ]
    )
    assert [item["report_id"] for item in result["queue"]] == [
        "pin",
        "new",
        "old",
    ]
    assert result["queue"][0]["evidence_count"] == 1


def test_review_queue_reports_invalid_entries_and_bounds_limit():
    result = build_review_queue(
        [_report(report_id="good"), {"type": "unknown"}],
        limit=1000,
    )
    assert result["limit"] == 50
    assert result["reports_available"] == 1
    assert result["invalid_reports"][0]["index"] == 1
