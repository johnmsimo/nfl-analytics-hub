"""P3.1 production player warehouse population and coverage verification.

The existing nflverse roster importer owns provider retrieval and provenance.
This module layers a deterministic normalization/coverage contract on top of it
so production can prove that player rows, team memberships, and source-scoped
identities are actually usable before projection work begins.
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any

from sqlalchemy import distinct, func, select

from database import db
from db_models import Player, PlayerExternalIdentity, PlayerTeamSeason, RawIngestRecord, Team
from external_providers import sync_rosters
from player_identity import nflverse_identities, resolve_player
from team_identity import normalize_team


def _date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _height_inches(value: object) -> int | None:
    """Normalize roster height values expressed as inches or feet-inches."""
    if value in (None, ""):
        return None
    text = str(value).strip().lower().replace("′", "'").replace("″", '"')
    if "-" in text:
        feet, _, inches = text.partition("-")
        try:
            return int(feet) * 12 + int(inches)
        except ValueError:
            return None
    if "'" in text:
        feet, _, remainder = text.partition("'")
        inches = remainder.replace('"', "").strip() or "0"
        try:
            return int(feet) * 12 + int(inches)
        except ValueError:
            return None
    parsed = _int(value)
    return parsed if parsed and 48 <= parsed <= 96 else None


def _display_name(payload: dict[str, Any]) -> str:
    return str(
        payload.get("full_name")
        or payload.get("player_display_name")
        or payload.get("display_name")
        or payload.get("player_name")
        or payload.get("name")
        or ""
    ).strip()


def _hydrate_player(player: Player, payload: dict[str, Any]) -> None:
    """Fill canonical player bio fields from the trusted nflverse roster row."""
    name = _display_name(payload)
    if name:
        player.full_name = name
    first = str(
        payload.get("common_first_name")
        or payload.get("first_name")
        or payload.get("football_name")
        or ""
    ).strip()
    last = str(payload.get("last_name") or "").strip()
    if first:
        player.first_name = first
    elif name and not player.first_name:
        player.first_name = name.split(" ", 1)[0]
    if last:
        player.last_name = last
    elif name and " " in name and not player.last_name:
        player.last_name = name.split(" ", 1)[1]

    position = str(payload.get("position") or "").strip().upper()
    if position:
        player.position = position
    player.birth_date = _date(payload.get("birth_date")) or player.birth_date
    player.height_inches = (
        _height_inches(payload.get("height") or payload.get("height_inches"))
        or player.height_inches
    )
    player.weight_lbs = _int(payload.get("weight") or payload.get("weight_lbs")) or player.weight_lbs
    college = str(payload.get("college_name") or payload.get("college") or "").strip()
    if college:
        player.college = college


def normalize_roster_records(season: int) -> dict[str, int]:
    """Re-resolve captured roster rows and hydrate canonical player metadata."""
    rows = db.session.scalars(
        select(RawIngestRecord)
        .where(
            RawIngestRecord.entity_type == "roster",
            RawIngestRecord.season == season,
        )
        .order_by(RawIngestRecord.ingested_at, RawIngestRecord.id)
    ).all()
    teams = {
        team.abbreviation: team
        for team in db.session.scalars(select(Team)).all()
    }
    processed = normalized = skipped = 0
    try:
        for raw in rows:
            processed += 1
            payload = raw.payload if isinstance(raw.payload, dict) else {}
            identities = nflverse_identities(payload)
            name = _display_name(payload)
            team = teams.get(normalize_team(payload.get("team")) or "")
            if not name or not any(str(value or "").strip() for value in identities.values()):
                skipped += 1
                continue
            player = resolve_player(
                identities,
                full_name=name,
                position=payload.get("position"),
                team_id=team.id if team else None,
                season=season,
            )
            if not player:
                skipped += 1
                continue
            _hydrate_player(player, payload)
            normalized += 1
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return {"processed": processed, "normalized": normalized, "skipped": skipped}


def player_warehouse_snapshot(season: int) -> dict[str, Any]:
    """Return aggregate-only player coverage metrics safe for CI logs."""
    roster_ids = select(distinct(PlayerTeamSeason.player_id)).where(
        PlayerTeamSeason.season == season
    ).subquery()
    rostered_players = int(db.session.scalar(select(func.count()).select_from(roster_ids)) or 0)
    teams_covered = int(
        db.session.scalar(
            select(func.count(distinct(PlayerTeamSeason.team_id))).where(
                PlayerTeamSeason.season == season
            )
        )
        or 0
    )
    total_players = int(db.session.scalar(select(func.count()).select_from(Player)) or 0)
    total_identities = int(
        db.session.scalar(select(func.count()).select_from(PlayerExternalIdentity)) or 0
    )

    rostered_with_identity = int(
        db.session.scalar(
            select(func.count(distinct(PlayerExternalIdentity.player_id)))
            .select_from(PlayerExternalIdentity)
            .join(roster_ids, roster_ids.c.player_id == PlayerExternalIdentity.player_id)
        )
        or 0
    )
    rostered_with_nflverse = int(
        db.session.scalar(
            select(func.count(distinct(PlayerExternalIdentity.player_id)))
            .select_from(PlayerExternalIdentity)
            .join(roster_ids, roster_ids.c.player_id == PlayerExternalIdentity.player_id)
            .where(PlayerExternalIdentity.source_key == "nflverse")
        )
        or 0
    )
    rostered_with_position = int(
        db.session.scalar(
            select(func.count(distinct(Player.id)))
            .select_from(Player)
            .join(roster_ids, roster_ids.c.player_id == Player.id)
            .where(Player.position.is_not(None), Player.position != "")
        )
        or 0
    )

    identity_coverage = round(rostered_with_identity / rostered_players, 4) if rostered_players else 0.0
    nflverse_coverage = round(rostered_with_nflverse / rostered_players, 4) if rostered_players else 0.0
    position_coverage = round(rostered_with_position / rostered_players, 4) if rostered_players else 0.0

    minimum_players = max(int(os.environ.get("P31_MIN_ROSTERED_PLAYERS", "1000")), 1)
    minimum_teams = max(int(os.environ.get("P31_MIN_TEAMS", "32")), 1)
    minimum_identity = float(os.environ.get("P31_MIN_IDENTITY_COVERAGE", "0.95"))
    minimum_nflverse = float(os.environ.get("P31_MIN_NFLVERSE_COVERAGE", "0.90"))
    minimum_position = float(os.environ.get("P31_MIN_POSITION_COVERAGE", "0.90"))

    gates = {
        "rostered_players": rostered_players >= minimum_players,
        "team_coverage": teams_covered >= minimum_teams,
        "identity_coverage": identity_coverage >= minimum_identity,
        "nflverse_identity_coverage": nflverse_coverage >= minimum_nflverse,
        "position_coverage": position_coverage >= minimum_position,
    }
    return {
        "season": season,
        "total_players": total_players,
        "total_identities": total_identities,
        "rostered_players": rostered_players,
        "teams_covered": teams_covered,
        "rostered_with_identity": rostered_with_identity,
        "rostered_with_nflverse_identity": rostered_with_nflverse,
        "rostered_with_position": rostered_with_position,
        "identity_coverage": identity_coverage,
        "nflverse_identity_coverage": nflverse_coverage,
        "position_coverage": position_coverage,
        "thresholds": {
            "minimum_rostered_players": minimum_players,
            "minimum_teams": minimum_teams,
            "minimum_identity_coverage": minimum_identity,
            "minimum_nflverse_identity_coverage": minimum_nflverse,
            "minimum_position_coverage": minimum_position,
        },
        "gates": gates,
        "ok": all(gates.values()),
    }


def populate_player_warehouse(season: int) -> dict[str, Any]:
    """Run the public roster sync, normalize its records, then verify coverage."""
    roster_sync = sync_rosters(season)
    normalization = normalize_roster_records(season)
    snapshot = player_warehouse_snapshot(season)
    return {
        "season": season,
        "provider": "nflverse",
        "dataset": "rosters",
        "sync": {
            "read": int(roster_sync.get("read") or 0),
            "written": int(roster_sync.get("written") or 0),
        },
        "normalization": normalization,
        "warehouse": snapshot,
        "ok": bool(snapshot.get("ok")),
    }
