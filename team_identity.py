"""Canonical team identity for the warehouse.

Provider feeds do not agree on every NFL abbreviation. ESPN-backed cache files,
nflverse releases, historical relocation data, and older NFL/PFR identifiers all
need to collapse onto the same 32 current-franchise rows.

Warehouse writers must funnel every abbreviation through :func:`normalize_team`
so one franchise owns exactly one ``Team`` row. This is especially important for
rosters: an unrecognized alias silently drops every player-team membership for
that franchise and turns an otherwise healthy player warehouse into 31/32 team
coverage.

This module is deliberately warehouse-only: ``nfl_data.py`` keeps speaking ESPN
codes so cached files and frontend logo URLs stay valid.
"""

from __future__ import annotations

# Provider / historical aliases -> warehouse canonical abbreviation.
#
# The AZ/ARZ/BLT/CLV/HST spellings surface in upstream roster-related datasets.
# Relocation aliases stay here as well because historical provider rows may be
# replayed into the current warehouse.
TEAM_ALIASES = {
    "AZ": "ARI",
    "ARZ": "ARI",
    "BLT": "BAL",
    "CLV": "CLE",
    "HST": "HOU",
    "JAX": "JAC",
    "WSH": "WAS",
    "LA": "LAR",
    "SL": "LAR",
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
