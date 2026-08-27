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
        canonical_base="/api/current/realtime",
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
            },
            "analytics": {
                "canonical_base": "/api/current/analytics",
                "source_contract": "3.1",
                "legacy_base": "/api/v3/analytics",
            },
            "realtime": {
                "canonical_base": "/api/current/realtime",
                "source_contract": "3.2",
                "legacy_base": "/api/v3.2",
            },
            "profile_models_reports": {
                "canonical_base": "/api/current",
                "source_contract": "3.2",
                "legacy_base": "/api/v3.2",
            },
            "deliveries": {
                "canonical_base": "/api/current/deliveries",
                "source_contract": "4.5.3",
                "legacy_base": "/api/v4.5",
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
