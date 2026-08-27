from pathlib import Path

import scripts.p2_exit_verification as p2_exit

ROOT = Path(__file__).resolve().parents[1]


def test_p2_exit_workflow_is_manual_read_only_and_protected():
    workflow = (ROOT / ".github/workflows/p2-exit-verification.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "RUN_P2_EXIT_VERIFICATION" in workflow
    assert "environment: production" in workflow
    assert "FLY_API_TOKEN" in workflow
    assert "/usr/bin/env PYTHONPATH=/app python /app/scripts/p2_exit_verification.py" in workflow
    assert "https://nfl-analytics-hub.fly.dev/ready" in workflow
    assert "https://nfl-analytics-hub.fly.dev/login" in workflow
    assert "https://nfl-analytics-hub.fly.dev/api/current/capabilities" in workflow

    banned = (
        "sync_external(",
        "sync_commercial(",
        "ENABLE_WAREHOUSE_RETENTION=true",
        "player-identities/reconcile",
        "warehouse-retention/apply",
        "smoke_odds_api",
    )
    assert not any(token in workflow for token in banned)


def test_p2_exit_script_has_no_provider_or_apply_path():
    source = (ROOT / "scripts/p2_exit_verification.py").read_text(encoding="utf-8")
    banned = (
        "sync_external(",
        "sync_commercial(",
        "fetch_week_scoreboard(",
        "get_schedule(",
        "dry_run=False",
        "requests.",
    )
    assert not any(token in source for token in banned)
    assert "build_preview()" in source
    assert '"mode": "read-only"' in source


def test_canonical_api_exit_check_reuses_registered_source_views(app_fixture):
    result = p2_exit._canonical_api_check(app_fixture)

    assert result["passed"] is True
    assert result["blocking"] is True
    assert result["details"]["alias_count"] > 0
    assert result["details"]["missing_alias_count"] == 0
    assert result["details"]["mismatched_alias_count"] == 0
    assert result["details"]["missing_capability_routes"] == []


def test_csp_and_auth_boundary_exit_checks(app_fixture, monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "0")
    results = p2_exit._csp_and_auth_boundary_checks(app_fixture)
    by_name = {result["name"]: result for result in results}

    assert by_name["strict_script_csp"]["passed"] is True
    assert by_name["strict_script_csp"]["details"]["script_unsafe_inline"] is False
    assert by_name["strict_script_csp"]["details"]["script_unsafe_eval"] is False
    assert by_name["canonical_api_auth_boundary"]["passed"] is True
    assert by_name["canonical_api_auth_boundary"]["details"] == {
        "status": 401,
        "code": "AUTH_REQUIRED",
    }


def test_exit_report_blocks_required_failures_but_not_advisories(app_fixture, monkeypatch):
    passed = p2_exit._check("required-pass", True, {})
    failed = p2_exit._check("required-fail", False, {})
    advisory = p2_exit._check("advisory", False, {}, blocking=False)

    monkeypatch.setattr(p2_exit, "_schedule_checks", lambda: [passed, advisory])
    monkeypatch.setattr(p2_exit, "_p21_checks", lambda: [passed])
    monkeypatch.setattr(p2_exit, "_freshness_checks", lambda: [passed])
    monkeypatch.setattr(p2_exit, "_auth_check", lambda: failed)
    monkeypatch.setattr(p2_exit, "_csp_and_auth_boundary_checks", lambda app: [passed])
    monkeypatch.setattr(p2_exit, "_canonical_api_check", lambda app: passed)

    report = p2_exit.build_exit_report(app_fixture)

    assert report["ok"] is False
    assert report["blocking_failures"] == ["required-fail"]
    assert report["advisories"] == ["advisory"]
    assert report["mode"] == "read-only"
    assert report["phase"] == "P2-exit"
