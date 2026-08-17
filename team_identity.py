"""Canonical team identity for the warehouse.

Two abbreviation conventions meet in this app. The disk cache under ``data/``
carries ESPN codes (``JAX``, ``WSH``) because ``nfl_data.py`` is ESPN-backed,
while the nflverse release assets read by ``external_providers.py`` and the
contract in ``coverage_service.EXPECTED_TEAMS`` use ``JAC`` and ``WAS``.

Warehouse writers must funnel every abbreviation through :func:`normalize_team`
so one franchise owns exactly one ``Team`` row. Without it the ESPN importer
creates ``JAX``/``WSH`` and the nflverse importer then looks up ``JAC``/``WAS``,
silently dropping every Jacksonville and Washington row it was asked to load.

This module is deliberately warehouse-only: ``nfl_data.py`` keeps speaking ESPN
codes so cached files and frontend logo URLs stay valid.
"""

from __future__ import annotations

# ESPN and legacy relocation codes -> nflverse canonical abbreviation.
TEAM_ALIASES = {
    "JAX": "JAC",
    "WSH": "WAS",
    "LA": "LAR",
    "STL": "LAR",
    "SD": "LAC",
    "OAK": "LV",
}

# Schedule feeds carry conference all-star and placeholder entries that are not
# franchises; they must never become Team rows.
NON_TEAM_CODES = frozenset({"AFC", "NFC", "NFL", "TBD", "TBA"})


def normalize_team(value) -> str | None:
    """Return the canonical abbreviation, or None if it is not a real team."""
    key = str(value or "").strip().upper()
    if not key:
        return None
    key = TEAM_ALIASES.get(key, key)
    if key in NON_TEAM_CODES:
        return None
    return key
