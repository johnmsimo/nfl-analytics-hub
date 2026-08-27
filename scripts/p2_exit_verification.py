#!/usr/bin/env python3
"""Run the sanitized, strictly read-only P2 hardening exit verification.

This script is designed to execute inside the deployed Fly application image.
It must not call external providers, consume Odds API credits, or mutate
production data.  It verifies the production state created by P2.1-P2.5 and
prints one aggregate JSON result suitable for GitHub Actions logs.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

import nfl_data
from database import db
from production_freshness import production_freshness
from routes.current_api import ALIASES
from scripts.p21_production_preview import build_preview
from security import validate_auth_configuration


def _check(
    name: str,
    passed: bool,
    details: dict[str, Any],
    *,
    blocking: bool = True,
) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "blocking": blocking,
        "details": details,
    }


def _schedule_checks() -> list[dict[str, Any]]:
    schedule = nfl_data.schedule_status()
    current = schedule.get("current_week") or {}
    payload = nfl_data._read_json("schedule_2026.json") or {}  # noqa: SLF001 - read-only merged schedule source
    games = payload.get("games", []) if isinstance(payload, dict) else []
    active_games = [
        game
        for game in games
        if game.get("season") == 2026
        and game.get("season_type") == current.get("season_type")
        and game.get("week") == current.get("week")
    ]
    counts = schedule.get("counts") or {}
    integrity_ok = (
        schedule.get("season") == 2026
        and schedule.get("ready") is True
        and int(schedule.get("total_games") or 0) >= 334
        and int(counts.get("PRE") or 0) >= 49
        and int(counts.get("REG") or 0) >= 272
        and int(counts.get("POST") or 0) >= 13
        and current.get("season") == 2026
        and current.get("season_type") in {"PRE", "REG", "POST"}
        and isinstance(current.get("week"), int)
        and len(active_games) > 0
    )
    return [
        _check(
            "schedule_integrity",
            integrity_ok,
            {
                "season": schedule.get("season"),
                "ready": schedule.get("ready"),
                "total_games": schedule.get("total_games"),
                "counts": counts,
                "current_week": current,
                "current_week_game_count": len(active_games),
                "issues": schedule.get("issues") or [],
            },
        ),
        _check(
            "schedule_freshness",
            schedule.get("freshness_status") == "ready",
            {
                "status": schedule.get("freshness_status"),
                "age_seconds": schedule.get("age_seconds"),
                "stale_after_seconds": schedule.get("stale_after_seconds"),
            },
            blocking=False,
        ),
    ]


def _p21_checks() -> list[dict[str, Any]]:
    preview = build_preview()
    latest_sync = preview.get("latest_cached_data_sync") or {}
    identity = preview.get("identity_reconciliation") or {}
    retention = preview.get("warehouse_retention") or {}
    deleted = retention.get("deleted") or {}
    scheduler = preview.get("warehouse_retention_scheduler") or {}
    counts = preview.get("warehouse_counts") or {}

    safety_ok = (
        preview.get("ok") is True
        and preview.get("mode") == "read-only"
        and identity.get("dry_run") is True
        and int(identity.get("players_merged") or 0) == 0
        and int(identity.get("identity_links_added") or 0) == 0
        and retention.get("dry_run") is True
        and not any(int(value or 0) for value in deleted.values())
        and scheduler.get("enabled") is False
    )
    sync_ok = (
        latest_sync.get("status") == "completed"
        and latest_sync.get("error_category") is None
        and latest_sync.get("error_fingerprint") is None
    )
    player_coverage_ok = int(counts.get("players") or 0) > 0 and int(counts.get("player_identities") or 0) > 0

    return [
        _check(
            "p21_read_only_safety",
            safety_ok,
            {
                "mode": preview.get("mode"),
                "identity_dry_run": identity.get("dry_run"),
                "players_merged": identity.get("players_merged"),
                "identity_links_added": identity.get("identity_links_added"),
                "retention_dry_run": retention.get("dry_run"),
                "retention_deleted": deleted,
                "retention_scheduler_enabled": scheduler.get("enabled"),
            },
        ),
        _check(
            "cached_data_sync",
            sync_ok,
            {
                "status": latest_sync.get("status"),
                "records_read": latest_sync.get("records_read"),
                "records_written": latest_sync.get("records_written"),
                "error_category": latest_sync.get("error_category"),
                "error_fingerprint": latest_sync.get("error_fingerprint"),
            },
        ),
        _check(
            "player_warehouse_coverage",
            player_coverage_ok,
            {
                "players": counts.get("players", 0),
                "player_identities": counts.get("player_identities", 0),
            },
            blocking=False,
        ),
    ]


def _freshness_checks() -> list[dict[str, Any]]:
    schedule = nfl_data.schedule_status()
    freshness = production_freshness(schedule)
    components = freshness.get("components") or {}
    providers = components.get("providers") or {}
    scheduler = components.get("scheduler") or {}

    scheduler_ok = scheduler.get("expected") is True and scheduler.get("status") in {"ready", "pending"}
    provider_ok = providers.get("status") == "ready"
    return [
        _check(
            "scheduler_freshness",
            scheduler_ok,
            {
                "status": scheduler.get("status"),
                "expected": scheduler.get("expected"),
                "enabled_job_count": scheduler.get("enabled_job_count"),
                "jobs": [
                    {
                        "key": row.get("key"),
                        "status": row.get("status"),
                        "last_status": row.get("last_status"),
                    }
                    for row in scheduler.get("jobs", [])
                ],
            },
        ),
        _check(
            "provider_freshness",
            provider_ok,
            {
                "status": providers.get("status"),
                "source_count": providers.get("source_count"),
                "observed_http_provider_count": providers.get("observed_http_provider_count"),
                "sources": [
                    {"key": row.get("key"), "status": row.get("status")}
                    for row in providers.get("sources", [])
                ],
            },
            blocking=False,
        ),
    ]


def _auth_check() -> dict[str, Any]:
    users = validate_auth_configuration()
    require_mfa_env = os.getenv("REQUIRE_MFA", "").strip().lower() in {"1", "true", "yes", "on"}
    expanded = len(users) > 1
    all_have_mfa_secret = all(bool(user.get("totp_secret")) for user in users.values())
    policy_ok = bool(users) and (not (expanded or require_mfa_env) or all_have_mfa_secret)
    return _check(
        "role_and_mfa_policy",
        policy_ok,
        {
            "configured_user_count": len(users),
            "expanded_access": expanded,
            "mfa_required_by_policy": bool(expanded or require_mfa_env),
            "all_required_users_have_mfa": all_have_mfa_secret if (expanded or require_mfa_env) else True,
            "configured_roles": sorted({str(user.get("role")) for user in users.values()}),
        },
    )


def _csp_and_auth_boundary_checks(app) -> list[dict[str, Any]]:
    client = app.test_client()
    login = client.get("/login")
    csp = login.headers.get("Content-Security-Policy", "")
    directives: dict[str, str] = {}
    for raw in csp.split(";"):
        raw = raw.strip()
        if not raw:
            continue
        name, _, value = raw.partition(" ")
        directives[name] = value.strip()

    script_src = directives.get("script-src", "")
    csp_ok = (
        login.status_code == 200
        and "'unsafe-inline'" not in script_src
        and "'unsafe-eval'" not in script_src
        and directives.get("object-src") == "'none'"
        and directives.get("base-uri") == "'self'"
        and directives.get("form-action") == "'self'"
        and directives.get("frame-ancestors") == "'none'"
    )

    protected = client.get("/api/current/capabilities")
    payload = protected.get_json(silent=True) or {}
    boundary_ok = protected.status_code == 401 and payload.get("code") == "AUTH_REQUIRED"

    return [
        _check(
            "strict_script_csp",
            csp_ok,
            {
                "login_status": login.status_code,
                "script_unsafe_inline": "'unsafe-inline'" in script_src,
                "script_unsafe_eval": "'unsafe-eval'" in script_src,
                "object_src": directives.get("object-src"),
                "base_uri": directives.get("base-uri"),
                "form_action": directives.get("form-action"),
                "frame_ancestors": directives.get("frame-ancestors"),
            },
        ),
        _check(
            "canonical_api_auth_boundary",
            boundary_ok,
            {"status": protected.status_code, "code": payload.get("code")},
        ),
    ]


def _canonical_api_check(app) -> dict[str, Any]:
    missing: list[str] = []
    mismatched: list[str] = []
    rules = list(app.url_map.iter_rules())
    for spec in ALIASES:
        source = app.view_functions.get(spec.endpoint)
        matching = [
            rule
            for rule in rules
            if rule.rule == spec.rule and all(method in rule.methods for method in spec.methods)
        ]
        if source is None or not matching:
            missing.append(spec.rule)
            continue
        if not any(app.view_functions.get(rule.endpoint) is source for rule in matching):
            mismatched.append(spec.rule)

    required_capability_routes = {
        "/api/current/capabilities",
        "/api/current/intelligence/capabilities",
        "/api/current/analytics/capabilities",
        "/api/current/realtime/capabilities",
        "/api/current/deliveries/capabilities",
    }
    present_rules = {rule.rule for rule in rules}
    missing_capabilities = sorted(required_capability_routes - present_rules)
    passed = not missing and not mismatched and not missing_capabilities
    return _check(
        "canonical_api_facade",
        passed,
        {
            "alias_count": len(ALIASES),
            "missing_alias_count": len(missing),
            "mismatched_alias_count": len(mismatched),
            "missing_capability_routes": missing_capabilities,
        },
    )


def build_exit_report(app) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    checks.extend(_schedule_checks())
    checks.extend(_p21_checks())
    checks.extend(_freshness_checks())
    checks.append(_auth_check())
    checks.extend(_csp_and_auth_boundary_checks(app))
    checks.append(_canonical_api_check(app))

    blockers = [check["name"] for check in checks if check["blocking"] and not check["passed"]]
    advisories = [check["name"] for check in checks if not check["blocking"] and not check["passed"]]
    return {
        "ok": not blockers,
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "read-only",
        "phase": "P2-exit",
        "blocking_failures": blockers,
        "advisories": advisories,
        "checks": checks,
    }


def main() -> int:
    from app import app

    with app.app_context():
        report = build_exit_report(app)
        db.session.rollback()
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
