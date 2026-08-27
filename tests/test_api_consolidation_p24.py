"""P2.4 regression coverage for API-generation consolidation."""

from __future__ import annotations

from pathlib import Path

from api_lifecycle import CANONICAL_CONTRACT, GENERATIONS, capability_manifest, generation_for_path
from routes.current_api import ALIASES

ROOT = Path(__file__).resolve().parents[1]


def _rule_for(app, rule_text: str):
    return next((rule for rule in app.url_map.iter_rules() if rule.rule == rule_text), None)


def test_canonical_manifest_consolidates_expected_generations():
    manifest = capability_manifest()
    assert manifest["contract"] == CANONICAL_CONTRACT
    assert manifest["canonical_root"] == "/api/current"
    assert {row["generation"] for row in manifest["compatibility"]} == {
        "v2",
        "v3",
        "v3.2",
        "v4.5",
    }
    assert {item.key for item in GENERATIONS} == {"v2", "v3", "v3.2", "v4.5"}


def test_generation_match_is_specific_and_does_not_capture_current():
    assert generation_for_path("/api/v2/live").key == "v2"
    assert generation_for_path("/api/v3/analytics/epa").key == "v3"
    assert generation_for_path("/api/v3.2/profile").key == "v3.2"
    assert generation_for_path("/api/v4.5/deliveries").key == "v4.5"
    assert generation_for_path("/api/current/analytics/epa") is None


def test_every_canonical_alias_reuses_source_view_function(app_fixture):
    for spec in ALIASES:
        source = app_fixture.view_functions.get(spec.endpoint)
        assert source is not None, spec.endpoint
        rule = _rule_for(app_fixture, spec.rule)
        assert rule is not None, spec.rule
        assert app_fixture.view_functions[rule.endpoint] is source


def test_current_capabilities_is_the_single_discovery_entrypoint(client):
    response = client.get("/api/current/capabilities")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["contract"] == "2026.1"
    assert set(payload["domains"]) == {
        "intelligence",
        "analytics",
        "realtime",
        "profile_models_reports",
        "deliveries",
    }
    assert "Deprecation" not in response.headers


def test_v2_legacy_route_and_current_alias_share_behavior(client):
    legacy = client.get("/api/v2/live?season=2025&week=1")
    current = client.get("/api/current/intelligence/live?season=2025&week=1")
    assert legacy.status_code == current.status_code == 200
    assert legacy.get_json() == current.get_json()
    assert legacy.headers["Deprecation"] == "true"
    assert legacy.headers["X-API-Canonical-Base"] == "/api/current/intelligence"
    assert "Deprecation" not in current.headers


def test_v3_analytics_legacy_route_and_current_alias_share_behavior(client):
    payload = {
        "score_diff": 3,
        "seconds_remaining": 900,
        "possession": 1,
        "pregame_home_edge": 0.05,
    }
    legacy = client.post("/api/v3/analytics/win-probability", json=payload)
    current = client.post("/api/current/analytics/win-probability", json=payload)
    assert legacy.status_code == current.status_code == 200
    assert legacy.get_json() == current.get_json()
    assert legacy.headers["Deprecation"] == "true"
    assert legacy.headers["X-API-Canonical-Base"] == "/api/current/analytics"


def test_v32_legacy_route_and_current_alias_share_behavior(client):
    payload = {"density": "compact", "refresh_seconds": 30, "modules": ["live_games"]}
    legacy = client.post("/api/v3.2/preferences/normalize", json=payload)
    current = client.post("/api/current/realtime/preferences/normalize", json=payload)
    assert legacy.status_code == current.status_code == 200
    assert legacy.get_json() == current.get_json()
    assert legacy.headers["Deprecation"] == "true"
    assert legacy.headers["X-API-Canonical-Base"] == "/api/current/realtime"


def test_v45_remains_stable_compatibility_not_deprecated(client):
    legacy = client.get("/api/v4.5/capabilities")
    assert legacy.status_code == 200
    assert legacy.headers["X-API-Lifecycle"] == "stable-compatibility"
    assert legacy.headers["X-API-Canonical-Base"] == "/api/current/deliveries"
    assert "Deprecation" not in legacy.headers


def test_canonical_delivery_namespace_accepts_api_key_auth_path(client):
    response = client.get("/api/current/deliveries/health", headers={"X-API-Key": "not-a-real-key"})
    assert response.status_code == 401
    assert response.get_json()["code"] == "INVALID_API_KEY"

    unsupported = client.post(
        "/api/current/analytics/epa",
        json={"plays": []},
        headers={"X-API-Key": "not-a-real-key"},
    )
    assert unsupported.status_code == 401
    assert unsupported.get_json()["code"] == "API_KEY_ROUTE_UNSUPPORTED"


def test_v32_browser_workspace_uses_canonical_routes():
    text = (ROOT / "static" / "v32.html").read_text(encoding="utf-8")
    assert "/api/current/realtime/events" in text
    assert "/api/current/profile" in text
    assert "/api/current/reports/generate" in text
    assert "/api/current/observability" in text
    assert "/api/v3.2/" not in text
