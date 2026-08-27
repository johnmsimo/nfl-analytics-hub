"""P2.4 API-generation lifecycle metadata and canonical-route registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class ApiGeneration:
    key: str
    legacy_prefix: str
    canonical_base: str
    status: str
    source_contract: str


GENERATIONS: Final[tuple[ApiGeneration, ...]] = (
    ApiGeneration(
        key="v2",
        legacy_prefix="/api/v2",
        canonical_base="/api/current/intelligence",
        status="legacy-compatibility",
        source_contract="2.0",
    ),
    ApiGeneration(
        key="v3",
        legacy_prefix="/api/v3/analytics",
        canonical_base="/api/current/analytics",
        status="legacy-compatibility",
        source_contract="3.1",
    ),
    ApiGeneration(
        key="v3.2",
        legacy_prefix="/api/v3.2",
        canonical_base="/api/current",
        status="legacy-compatibility",
        source_contract="3.2",
    ),
    ApiGeneration(
        key="v4.5",
        legacy_prefix="/api/v4.5",
        canonical_base="/api/current/deliveries",
        status="stable-compatibility",
        source_contract="4.5.3",
    ),
)

CANONICAL_CONTRACT: Final[str] = "2026.1"
CANONICAL_ROOT: Final[str] = "/api/current"


def generation_for_path(path: str) -> ApiGeneration | None:
    """Return the most-specific versioned generation matching a request path."""
    for generation in sorted(GENERATIONS, key=lambda item: len(item.legacy_prefix), reverse=True):
        if path == generation.legacy_prefix or path.startswith(generation.legacy_prefix + "/"):
            return generation
    return None


def lifecycle_headers(path: str) -> dict[str, str]:
    """Return compatibility headers for a versioned API request."""
    generation = generation_for_path(path)
    if generation is None:
        return {}

    headers = {
        "X-API-Generation": generation.key,
        "X-API-Contract": generation.source_contract,
        "X-API-Canonical-Base": generation.canonical_base,
        "Link": f'<{CANONICAL_ROOT}/capabilities>; rel="successor-version"',
    }
    if generation.status == "legacy-compatibility":
        headers["Deprecation"] = "true"
        headers["X-API-Lifecycle"] = "legacy-compatibility"
    else:
        headers["X-API-Lifecycle"] = "stable-compatibility"
    return headers


def capability_manifest() -> dict:
    """Describe the consolidated API without exposing credentials or runtime secrets."""
    return {
        "contract": CANONICAL_CONTRACT,
        "canonical_root": CANONICAL_ROOT,
        "status": "stable",
        "domains": {
            "intelligence": {
                "canonical_base": "/api/current/intelligence",
                "source_contract": "2.0",
                "legacy_base": "/api/v2",
                "endpoints": [
                    "GET /api/current/intelligence/platform/health",
                    "GET /api/current/intelligence/live",
                    "GET /api/current/intelligence/teams/{abbr}/intelligence",
                    "GET /api/current/intelligence/games/{game_id}/intelligence",
                ],
            },
            "analytics": {
                "canonical_base": "/api/current/analytics",
                "source_contract": "3.1",
                "legacy_base": "/api/v3/analytics",
                "endpoints": [
                    "POST /api/current/analytics/win-probability",
                    "POST /api/current/analytics/epa",
                    "POST /api/current/analytics/drives",
                    "POST /api/current/analytics/simulate",
                    "POST /api/current/analytics/power-rating",
                    "POST /api/current/analytics/injury-impact",
                    "POST /api/current/analytics/player-similarity",
                    "POST /api/current/analytics/matchup",
                    "POST /api/current/analytics/game-intelligence",
                    "POST /api/current/analytics/live-center",
                    "POST /api/current/analytics/player-intelligence",
                    "POST /api/current/analytics/team-intelligence",
                    "POST /api/current/analytics/betting-intelligence",
                    "POST /api/current/analytics/assistant",
                    "POST /api/current/analytics/watchlist",
                ],
            },
            "realtime": {
                "canonical_base": "/api/current/realtime",
                "source_contract": "3.2",
                "legacy_base": "/api/v3.2",
                "endpoints": [
                    "GET /api/current/realtime/events",
                    "POST /api/current/realtime/events/publish",
                    "POST /api/current/realtime/preferences/normalize",
                    "POST /api/current/realtime/filters/normalize",
                    "POST /api/current/realtime/search",
                ],
            },
            "profile_models_reports": {
                "canonical_base": "/api/current",
                "source_contract": "3.2",
                "legacy_base": "/api/v3.2",
                "endpoints": [
                    "GET /api/current/profile",
                    "PUT /api/current/profile",
                    "POST /api/current/models/calibration",
                    "POST /api/current/models/backtest",
                    "POST /api/current/models/drift",
                    "POST /api/current/providers/freshness",
                    "GET /api/current/observability",
                    "POST /api/current/reports/generate",
                ],
            },
            "deliveries": {
                "canonical_base": "/api/current/deliveries",
                "source_contract": "4.5.3",
                "legacy_base": "/api/v4.5",
                "endpoints": [
                    "POST /api/current/deliveries",
                    "GET /api/current/deliveries",
                    "GET /api/current/deliveries/{delivery_id}",
                    "GET /api/current/deliveries/dead-letters",
                    "GET /api/current/deliveries/metrics",
                    "POST /api/current/deliveries/{delivery_id}/replay",
                    "GET /api/current/deliveries/health",
                ],
            },
        },
        "compatibility": [
            {
                "generation": item.key,
                "legacy_base": item.legacy_prefix,
                "canonical_base": item.canonical_base,
                "status": item.status,
                "source_contract": item.source_contract,
            }
            for item in GENERATIONS
        ],
    }
