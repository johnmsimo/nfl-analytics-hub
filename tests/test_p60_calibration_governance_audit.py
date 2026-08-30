from __future__ import annotations

import p60_calibration_governance_audit as p60


FP = "a" * 64


def _moneyline_event(
    event_id: str,
    action: str,
    created_at: str,
    candidate: str | None = "p49-moneyline-test",
    fingerprint: str = FP,
) -> dict:
    return {
        "eventId": event_id,
        "action": action,
        "candidateId": candidate if action == "promote" else None,
        "family": "logit-affine" if action == "promote" else "identity",
        "parameters": {"slope": 0.9, "intercept": 0.01}
        if action == "promote"
        else {"slope": None, "intercept": None},
        "baseModelVersion": "p40-transparent-v1",
        "approvedBy": "owner",
        "governanceFingerprint": fingerprint,
        "createdAt": created_at,
    }


def _market_event(
    market: str,
    event_id: str,
    action: str,
    created_at: str,
    candidate: str | None = None,
    fingerprint: str = FP,
) -> dict:
    if candidate is None and action == "promote":
        candidate = "p54-sp-test" if market == "spread" else "p54-to-test"
    return {
        "eventId": event_id,
        "market": market,
        "action": action,
        "candidateId": candidate if action == "promote" else None,
        "family": "logit-affine" if action == "promote" else "identity",
        "parameters": {"slope": 0.9, "intercept": 0.01}
        if action == "promote"
        else {"slope": None, "intercept": None},
        "baseModelVersion": "p41-pricing-v1",
        "approvedBy": "owner",
        "governanceFingerprint": fingerprint,
        "createdAt": created_at,
    }


def _baseline_champions() -> dict:
    return {
        market: {
            "available": True,
            "state": "baseline",
            "applied": False,
            "candidateId": None,
        }
        for market in p60.MARKETS
    }


def test_p60_empty_append_only_registries_are_valid_baseline():
    report = p60.build_audit_report([], [], _baseline_champions())
    assert report["ok"] is True
    assert report["state"] == "audit-ready"
    assert report["eventCount"] == 0
    assert len(report["portfolioDigest"]) == 64
    assert report["integrity"]["failedChecks"] == []
    assert all(report["markets"][market]["derivedState"] == "baseline" for market in p60.MARKETS)


def test_p60_normalizes_cross_registry_history_oldest_first():
    moneyline = [
        _moneyline_event("p50-b", "rollback", "2026-09-04T00:00:00+00:00"),
        _moneyline_event("p50-a", "promote", "2026-09-01T00:00:00+00:00"),
    ]
    markets = [
        _market_event("spread", "p54-s", "promote", "2026-09-02T00:00:00+00:00"),
        _market_event("total", "p54-t", "promote", "2026-09-03T00:00:00+00:00"),
    ]
    champions = _baseline_champions()
    champions["spread"] = {
        "available": True,
        "state": "promoted",
        "applied": True,
        "candidateId": "p54-sp-test",
    }
    champions["total"] = {
        "available": True,
        "state": "promoted",
        "applied": True,
        "candidateId": "p54-to-test",
    }
    report = p60.build_audit_report(moneyline, markets, champions)
    assert report["ok"] is True
    assert [row["eventId"] for row in report["events"]] == [
        "p50-a",
        "p54-s",
        "p54-t",
        "p50-b",
    ]
    assert [row["sequence"] for row in report["events"]] == [1, 2, 3, 4]
    assert all(len(row["eventDigest"]) == 64 for row in report["events"])
    assert report["markets"]["moneyline"]["derivedState"] == "baseline"
    assert report["markets"]["spread"]["derivedCandidateId"] == "p54-sp-test"


def test_p60_detects_duplicate_event_ids():
    moneyline = [_moneyline_event("duplicate", "promote", "2026-09-01T00:00:00+00:00")]
    markets = [_market_event("spread", "duplicate", "promote", "2026-09-02T00:00:00+00:00")]
    champions = _baseline_champions()
    champions["moneyline"].update({"state": "promoted", "applied": True, "candidateId": "p49-moneyline-test"})
    champions["spread"].update({"state": "promoted", "applied": True, "candidateId": "p54-sp-test"})
    report = p60.build_audit_report(moneyline, markets, champions)
    assert report["ok"] is False
    assert "eventIdsUnique" in report["integrity"]["failedChecks"]


def test_p60_detects_malformed_governance_fingerprint():
    event = _moneyline_event(
        "p50-a",
        "promote",
        "2026-09-01T00:00:00+00:00",
        fingerprint="not-a-sha256",
    )
    champions = _baseline_champions()
    champions["moneyline"].update({"state": "promoted", "applied": True, "candidateId": "p49-moneyline-test"})
    report = p60.build_audit_report([event], [], champions)
    assert report["state"] == "audit-degraded"
    assert "governanceFingerprintsWellFormed" in report["integrity"]["failedChecks"]


def test_p60_detects_candidate_lineage_cross_market_contamination():
    bad = _market_event(
        "spread",
        "p54-bad",
        "promote",
        "2026-09-01T00:00:00+00:00",
        candidate="p54-to-total-candidate",
    )
    champions = _baseline_champions()
    champions["spread"].update({"state": "promoted", "applied": True, "candidateId": "p54-to-total-candidate"})
    report = p60.build_audit_report([], [bad], champions)
    assert report["ok"] is False
    assert "candidateLineageValid" in report["integrity"]["failedChecks"]


def test_p60_detects_rollback_without_active_champion():
    rollback = _market_event(
        "total",
        "p54-r",
        "rollback",
        "2026-09-01T00:00:00+00:00",
    )
    report = p60.build_audit_report([], [rollback], _baseline_champions())
    assert report["ok"] is False
    assert "stateTransitionsValid" in report["integrity"]["failedChecks"]
    assert report["markets"]["total"]["transitionErrors"]


def test_p60_detects_live_champion_mismatch():
    promote = _moneyline_event("p50-a", "promote", "2026-09-01T00:00:00+00:00")
    report = p60.build_audit_report([promote], [], _baseline_champions())
    assert report["ok"] is False
    assert "liveChampionsMatchHistory" in report["integrity"]["failedChecks"]
    consistency = report["markets"]["moneyline"]["championConsistency"]
    assert consistency["checks"]["appliedStateMatchesHistory"] is False


def test_p60_audit_route_is_read_only(client, monkeypatch):
    monkeypatch.setattr(
        p60,
        "build_production_report",
        lambda: {
            "available": True,
            "state": "audit-ready",
            "ok": True,
            "eventCount": 0,
            "portfolioDigest": "0" * 64,
            "integrity": {"ok": True, "checks": {}, "failedChecks": []},
            "markets": {},
            "events": [],
            "safetyContract": {
                "readOnly": True,
                "providerRequests": 0,
                "createsMutationEndpoint": False,
            },
        },
    )
    response = client.get("/api/game-calibration/audit-ledger")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["state"] == "audit-ready"
    assert payload["safetyContract"]["readOnly"] is True
    assert payload["safetyContract"]["providerRequests"] == 0
    assert payload["safetyContract"]["createsMutationEndpoint"] is False
