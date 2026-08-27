"""P2.4 stable API facade over the historical versioned blueprints.

The canonical routes deliberately reuse the already-tested Flask view functions.
That keeps one implementation per capability while preserving the older URLs as
compatibility contracts.
"""

from __future__ import annotations

from dataclasses import dataclass

from flask import Blueprint, Flask, jsonify

from api_lifecycle import capability_manifest

current_api_bp = Blueprint("current_api", __name__, url_prefix="/api/current")


def _domain_capabilities(domain: str):
    manifest = capability_manifest()
    return jsonify(
        {
            "contract": manifest["contract"],
            "domain": domain,
            **manifest["domains"][domain],
        }
    )


@current_api_bp.get("/capabilities")
def capabilities():
    return jsonify(capability_manifest())


@current_api_bp.get("/intelligence/capabilities")
def intelligence_capabilities():
    return _domain_capabilities("intelligence")


@current_api_bp.get("/analytics/capabilities")
def analytics_capabilities():
    return _domain_capabilities("analytics")


@current_api_bp.get("/realtime/capabilities")
def realtime_capabilities():
    return _domain_capabilities("realtime")


@current_api_bp.get("/deliveries/capabilities")
def delivery_capabilities():
    return _domain_capabilities("deliveries")


@dataclass(frozen=True)
class AliasSpec:
    endpoint: str
    rule: str
    methods: tuple[str, ...]
    domain: str


ALIASES: tuple[AliasSpec, ...] = (
    # v2 warehouse-backed intelligence
    AliasSpec(
        "v2.health",
        "/api/current/intelligence/platform/health",
        ("GET",),
        "intelligence",
    ),
    AliasSpec(
        "v2.live",
        "/api/current/intelligence/live",
        ("GET",),
        "intelligence",
    ),
    AliasSpec(
        "v2.team",
        "/api/current/intelligence/teams/<abbr>/intelligence",
        ("GET",),
        "intelligence",
    ),
    AliasSpec(
        "v2.matchup",
        "/api/current/intelligence/games/<game_id>/intelligence",
        ("GET",),
        "intelligence",
    ),
    # v3.1 dependency-light analytics
    AliasSpec(
        "analytics_api.win_probability",
        "/api/current/analytics/win-probability",
        ("POST",),
        "analytics",
    ),
    AliasSpec("analytics_api.epa", "/api/current/analytics/epa", ("POST",), "analytics"),
    AliasSpec("analytics_api.drives", "/api/current/analytics/drives", ("POST",), "analytics"),
    AliasSpec("analytics_api.simulate", "/api/current/analytics/simulate", ("POST",), "analytics"),
    AliasSpec("analytics_api.rating", "/api/current/analytics/power-rating", ("POST",), "analytics"),
    AliasSpec(
        "analytics_api.injuries",
        "/api/current/analytics/injury-impact",
        ("POST",),
        "analytics",
    ),
    AliasSpec(
        "analytics_api.similarity",
        "/api/current/analytics/player-similarity",
        ("POST",),
        "analytics",
    ),
    AliasSpec("analytics_api.matchup", "/api/current/analytics/matchup", ("POST",), "analytics"),
    AliasSpec(
        "analytics_api.intelligence",
        "/api/current/analytics/game-intelligence",
        ("POST",),
        "analytics",
    ),
    AliasSpec(
        "analytics_api.live_center",
        "/api/current/analytics/live-center",
        ("POST",),
        "analytics",
    ),
    AliasSpec(
        "analytics_api.player_ai",
        "/api/current/analytics/player-intelligence",
        ("POST",),
        "analytics",
    ),
    AliasSpec(
        "analytics_api.team_ai",
        "/api/current/analytics/team-intelligence",
        ("POST",),
        "analytics",
    ),
    AliasSpec(
        "analytics_api.betting_ai",
        "/api/current/analytics/betting-intelligence",
        ("POST",),
        "analytics",
    ),
    AliasSpec("analytics_api.assistant", "/api/current/analytics/assistant", ("POST",), "analytics"),
    AliasSpec("analytics_api.watchlist", "/api/current/analytics/watchlist", ("POST",), "analytics"),
    # v3.2 real-time/discovery
    AliasSpec("v32_api.events", "/api/current/realtime/events", ("GET",), "realtime"),
    AliasSpec(
        "v32_api.publish_event",
        "/api/current/realtime/events/publish",
        ("POST",),
        "realtime",
    ),
    AliasSpec(
        "v32_api.preferences",
        "/api/current/realtime/preferences/normalize",
        ("POST",),
        "realtime",
    ),
    AliasSpec(
        "v32_api.saved_filter",
        "/api/current/realtime/filters/normalize",
        ("POST",),
        "realtime",
    ),
    AliasSpec("v32_api.search", "/api/current/realtime/search", ("POST",), "realtime"),
    # v3.2 persisted profile/model/report completion surface
    AliasSpec(
        "v32_release_api.get_profile",
        "/api/current/profile",
        ("GET",),
        "profile_models_reports",
    ),
    AliasSpec(
        "v32_release_api.put_profile",
        "/api/current/profile",
        ("PUT",),
        "profile_models_reports",
    ),
    AliasSpec(
        "v32_release_api.model_calibration",
        "/api/current/models/calibration",
        ("POST",),
        "profile_models_reports",
    ),
    AliasSpec(
        "v32_release_api.model_backtest",
        "/api/current/models/backtest",
        ("POST",),
        "profile_models_reports",
    ),
    AliasSpec(
        "v32_release_api.model_drift",
        "/api/current/models/drift",
        ("POST",),
        "profile_models_reports",
    ),
    AliasSpec(
        "v32_release_api.provider_freshness",
        "/api/current/providers/freshness",
        ("POST",),
        "profile_models_reports",
    ),
    AliasSpec(
        "v32_release_api.observability",
        "/api/current/observability",
        ("GET",),
        "profile_models_reports",
    ),
    AliasSpec(
        "v32_release_api.reports_generate",
        "/api/current/reports/generate",
        ("POST",),
        "profile_models_reports",
    ),
    # v4.5 delivery operations
    AliasSpec("v45_api.enqueue_delivery", "/api/current/deliveries", ("POST",), "deliveries"),
    AliasSpec("v45_api.list_deliveries", "/api/current/deliveries", ("GET",), "deliveries"),
    AliasSpec(
        "v45_api.get_delivery_health",
        "/api/current/deliveries/health",
        ("GET",),
        "deliveries",
    ),
    AliasSpec(
        "v45_api.get_dead_letters",
        "/api/current/deliveries/dead-letters",
        ("GET",),
        "deliveries",
    ),
    AliasSpec(
        "v45_api.get_delivery_metrics",
        "/api/current/deliveries/metrics",
        ("GET",),
        "deliveries",
    ),
    AliasSpec(
        "v45_api.replay_delivery_route",
        "/api/current/deliveries/<delivery_id>/replay",
        ("POST",),
        "deliveries",
    ),
    AliasSpec(
        "v45_api.get_delivery",
        "/api/current/deliveries/<delivery_id>",
        ("GET",),
        "deliveries",
    ),
)


def register_current_api(app: Flask) -> None:
    """Register the stable facade after all source blueprints are present."""
    app.register_blueprint(current_api_bp)
    missing: list[str] = []
    for index, spec in enumerate(ALIASES):
        view_func = app.view_functions.get(spec.endpoint)
        if view_func is None:
            missing.append(spec.endpoint)
            continue
        app.add_url_rule(
            spec.rule,
            endpoint=f"current_alias_{index}_{spec.endpoint.replace('.', '_')}",
            view_func=view_func,
            methods=list(spec.methods),
        )
    if missing:
        raise RuntimeError(
            "cannot register canonical API aliases; missing endpoints: " + ", ".join(missing)
        )
