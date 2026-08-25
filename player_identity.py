"""Canonical, source-scoped player identity resolution.

Provider identifiers are not interchangeable.  ESPN and SportsDataIO both use
numeric ids, while nflverse uses GSIS ids and snap counts use PFR ids.  This
module keeps those namespaces separate and consolidates a legacy duplicate only
when a trusted row supplies identifiers that bridge both player records.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Mapping

from sqlalchemy import and_, func, select

from database import db
from db_models import (
    DepthChartEntry,
    InjuryReport,
    LeagueTransaction,
    Player,
    PlayerExternalIdentity,
    PlayerGameStat,
    PlayerSeasonStat,
    PlayerTeamSeason,
    RawIngestRecord,
    SnapCount,
)

SOURCE_ALIASES = {
    "gsis": "nflverse",
    "nflverse": "nflverse",
    "espn": "espn",
    "pfr": "pfr",
    "sportsdata": "sportsdataio",
    "sportsdataio": "sportsdataio",
    "legacy": "legacy",
}


def normalize_source(value: str) -> str:
    source = str(value or "").strip().lower()
    if source not in SOURCE_ALIASES:
        raise ValueError(f"unsupported player identity source: {source or '<empty>'}")
    return SOURCE_ALIASES[source]


def normalize_external_id(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def normalized_player_name(value: object) -> str:
    text = re.sub(r"[.'\u2019-]", "", str(value or "").lower().strip())
    text = re.sub(r"\s+(jr|sr|ii|iii|iv|v)$", "", text)
    return re.sub(r"\s+", " ", text)


def _identity_map(identities: Mapping[str, object]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for source, value in identities.items():
        external_id = normalize_external_id(value)
        if external_id:
            normalized[normalize_source(source)] = external_id
    return normalized


def _lookup_players(identities: Mapping[str, str]) -> list[Player]:
    found: dict[int, Player] = {}
    for source, external_id in identities.items():
        identity = db.session.scalar(
            select(PlayerExternalIdentity).where(
                PlayerExternalIdentity.source_key == source,
                PlayerExternalIdentity.external_id == external_id,
            )
        )
        if identity:
            player = db.session.get(Player, identity.player_id)
            if player:
                found[player.id] = player

        legacy_queries = []
        if source == "nflverse":
            legacy_queries.append(Player.external_id == external_id)
        elif source == "espn":
            # Numeric ``players.external_id`` values were historically shared
            # by ESPN and SportsDataIO. The migration gives those rows a
            # source-scoped alias, so only the dedicated ESPN column is a safe
            # legacy fallback here.
            legacy_queries.append(Player.espn_id == external_id)
        elif source == "pfr":
            legacy_queries.append(Player.pfr_id == external_id)
        elif source == "sportsdataio":
            legacy_queries.append(Player.external_id == f"sportsdataio:{external_id}")
        elif source == "legacy":
            legacy_queries.append(Player.external_id == external_id)
        for condition in legacy_queries:
            player = db.session.scalar(select(Player).where(condition))
            if player:
                found[player.id] = player
    return list(found.values())


def _team_name_match(
    full_name: str | None,
    team_id: int | None,
    season: int | None,
) -> Player | None:
    folded = normalized_player_name(full_name)
    if not folded or team_id is None or season is None:
        return None
    candidates = db.session.scalars(
        select(Player)
        .join(PlayerTeamSeason, PlayerTeamSeason.player_id == Player.id)
        .where(
            PlayerTeamSeason.team_id == team_id,
            PlayerTeamSeason.season == season,
        )
    ).all()
    matches = [player for player in candidates if normalized_player_name(player.full_name) == folded]
    return matches[0] if len(matches) == 1 else None


def _condition(column, value):
    return column.is_(None) if value is None else column == value


def _merge_payload(target, duplicate, protected: set[str]) -> None:
    """Fill missing values without replacing an established canonical fact."""
    for column in duplicate.__table__.columns:
        name = column.name
        if name in protected:
            continue
        current = getattr(target, name)
        incoming = getattr(duplicate, name)
        if current is None and incoming is not None:
            setattr(target, name, incoming)


def _merge_children(primary: Player, duplicate: Player, model, natural_key: tuple[str, ...]) -> None:
    rows = db.session.scalars(select(model).where(model.player_id == duplicate.id)).all()
    for row in rows:
        clauses = [model.player_id == primary.id]
        for field in natural_key:
            clauses.append(_condition(getattr(model, field), getattr(row, field)))
        existing = db.session.scalar(select(model).where(and_(*clauses)))
        if existing:
            _merge_payload(
                existing,
                row,
                {"id", "player_id", "created_at", "updated_at", *natural_key},
            )
            db.session.delete(row)
        else:
            row.player_id = primary.id
    db.session.flush()


def attach_identity(player: Player, source: str, external_id: object) -> PlayerExternalIdentity:
    source = normalize_source(source)
    normalized_id = normalize_external_id(external_id)
    if not normalized_id:
        raise ValueError("player external id is required")
    identity = db.session.scalar(
        select(PlayerExternalIdentity).where(
            PlayerExternalIdentity.source_key == source,
            PlayerExternalIdentity.external_id == normalized_id,
        )
    )
    if identity:
        if identity.player_id != player.id:
            raise ValueError(f"{source} player id {normalized_id} belongs to another player")
        return identity
    identity = PlayerExternalIdentity(
        player_id=player.id,
        source_key=source,
        external_id=normalized_id,
    )
    db.session.add(identity)
    db.session.flush()
    return identity


def merge_players(primary: Player, duplicate: Player) -> Player:
    """Move every player-owned fact to ``primary`` and remove ``duplicate``."""
    if primary.id == duplicate.id:
        return primary

    identity_rows = db.session.scalars(
        select(PlayerExternalIdentity).where(PlayerExternalIdentity.player_id == duplicate.id)
    ).all()
    for identity in identity_rows:
        existing = db.session.scalar(
            select(PlayerExternalIdentity).where(
                PlayerExternalIdentity.source_key == identity.source_key,
                PlayerExternalIdentity.external_id == identity.external_id,
                PlayerExternalIdentity.player_id == primary.id,
            )
        )
        if existing:
            db.session.delete(identity)
        else:
            identity.player_id = primary.id
    db.session.flush()

    for model, natural_key in (
        (PlayerTeamSeason, ("team_id", "season")),
        (PlayerGameStat, ("game_id",)),
        (PlayerSeasonStat, ("team_id", "season", "season_type")),
        (InjuryReport, ("team_id", "season", "week")),
        (DepthChartEntry, ("team_id", "season", "week", "chart_date", "depth_position")),
        (SnapCount, ("game_id",)),
    ):
        _merge_children(primary, duplicate, model, natural_key)

    for transaction in db.session.scalars(
        select(LeagueTransaction).where(LeagueTransaction.player_id == duplicate.id)
    ).all():
        transaction.player_id = primary.id

    attach_identity(primary, "legacy", duplicate.external_id)
    for attribute in ("pfr_id", "espn_id"):
        incoming = getattr(duplicate, attribute)
        if incoming and not getattr(primary, attribute):
            setattr(duplicate, attribute, None)
            db.session.flush()
            setattr(primary, attribute, incoming)

    _merge_payload(
        primary,
        duplicate,
        {"id", "external_id", "pfr_id", "espn_id", "created_at", "updated_at"},
    )
    db.session.delete(duplicate)
    db.session.flush()
    return primary


def _preferred_player(players: list[Player], identities: Mapping[str, str]) -> Player:
    nflverse_id = identities.get("nflverse")
    if nflverse_id:
        for player in players:
            if player.external_id == nflverse_id:
                return player
        nflverse_owner = db.session.scalar(
            select(PlayerExternalIdentity).where(
                PlayerExternalIdentity.source_key == "nflverse",
                PlayerExternalIdentity.external_id == nflverse_id,
            )
        )
        if nflverse_owner:
            for player in players:
                if player.id == nflverse_owner.player_id:
                    return player
    return min(players, key=lambda player: player.id)


def _generated_external_id(identities: Mapping[str, str]) -> str:
    for source in ("nflverse", "espn", "sportsdataio", "pfr", "legacy"):
        if source not in identities:
            continue
        raw = identities[source]
        candidate = raw if source in {"nflverse", "espn", "legacy"} else f"{source}:{raw}"
        if db.session.scalar(select(Player.id).where(Player.external_id == candidate)):
            candidate = f"{source}:{raw}"
        if len(candidate) <= 40 and not db.session.scalar(
            select(Player.id).where(Player.external_id == candidate)
        ):
            return candidate
        digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:16]
        candidate = f"{source}:{digest}"[:40]
        if not db.session.scalar(select(Player.id).where(Player.external_id == candidate)):
            return candidate
        raise ValueError(f"unable to allocate canonical id for {source} player {raw}")
    raise ValueError("at least one player identity is required")


def resolve_player(
    identities: Mapping[str, object],
    *,
    full_name: str | None,
    position: str | None = None,
    team_id: int | None = None,
    season: int | None = None,
    create: bool = True,
) -> Player | None:
    """Resolve provider ids to one player, merging only an explicit id bridge."""
    normalized = _identity_map(identities)
    if not normalized:
        return None
    players = _lookup_players(normalized)
    if not players:
        team_match = _team_name_match(full_name, team_id, season)
        if team_match:
            players = [team_match]
    if not players:
        if not create:
            return None
        name = str(full_name or "").strip() or next(iter(normalized.values()))
        player = Player(external_id=_generated_external_id(normalized), full_name=name, position=position)
        db.session.add(player)
        db.session.flush()
    else:
        player = _preferred_player(players, normalized)
        for duplicate in players:
            if duplicate.id != player.id:
                player = merge_players(player, duplicate)

    nflverse_id = normalized.get("nflverse")
    if nflverse_id and player.external_id != nflverse_id:
        attach_identity(player, "legacy", player.external_id)
        player.external_id = nflverse_id

    for source, external_id in normalized.items():
        attach_identity(player, source, external_id)
        if source == "pfr" and not player.pfr_id:
            player.pfr_id = external_id
        elif source == "espn" and not player.espn_id:
            player.espn_id = external_id

    clean_name = str(full_name or "").strip()
    if clean_name:
        player.full_name = clean_name
        parts = clean_name.split(" ", 1)
        player.first_name = player.first_name or parts[0]
        if len(parts) > 1:
            player.last_name = player.last_name or parts[1]
    if position:
        player.position = position
    db.session.flush()
    return player


def nflverse_identities(row: Mapping[str, object]) -> dict[str, object]:
    return {
        "nflverse": row.get("gsis_id") or row.get("player_id") or row.get("nflverse_id"),
        "pfr": row.get("pfr_id"),
        "espn": row.get("espn_id"),
    }


def reconcile_raw_player_identities(*, dry_run: bool = True) -> dict:
    """Use captured nflverse roster bridges to repair pre-P2.1 duplicates."""
    limit = int(os.environ.get("PLAYER_IDENTITY_RECONCILE_LIMIT", "100000"))
    if limit < 1 or limit > 1_000_000:
        raise ValueError("PLAYER_IDENTITY_RECONCILE_LIMIT must be between 1 and 1000000")
    rows = db.session.scalars(
        select(RawIngestRecord)
        .where(RawIngestRecord.entity_type == "roster")
        .order_by(RawIngestRecord.ingested_at.desc(), RawIngestRecord.id.desc())
        .limit(limit)
    ).all()
    bridges: dict[tuple[tuple[str, str], ...], tuple[dict[str, str], dict]] = {}
    for row in rows:
        payload = row.payload if isinstance(row.payload, dict) else {}
        identities = _identity_map(nflverse_identities(payload))
        if "nflverse" not in identities or len(identities) < 2:
            continue
        key = tuple(sorted(identities.items()))
        bridges.setdefault(key, (identities, payload))

    duplicate_sets = 0
    missing_aliases = 0
    for identities, _payload in bridges.values():
        if len(_lookup_players(identities)) > 1:
            duplicate_sets += 1
        for source, external_id in identities.items():
            if not db.session.scalar(
                select(PlayerExternalIdentity.id).where(
                    PlayerExternalIdentity.source_key == source,
                    PlayerExternalIdentity.external_id == external_id,
                )
            ):
                missing_aliases += 1

    result = {
        "dry_run": dry_run,
        "raw_roster_records_scanned": len(rows),
        "trusted_identity_bridges": len(bridges),
        "duplicate_player_sets": duplicate_sets,
        "missing_identity_links": missing_aliases,
        "players_merged": 0,
        "identity_links_added": 0,
    }
    if dry_run:
        return result

    players_before = int(db.session.scalar(select(func.count()).select_from(Player)) or 0)
    identities_before = int(db.session.scalar(select(func.count()).select_from(PlayerExternalIdentity)) or 0)
    try:
        for identities, payload in bridges.values():
            resolve_player(
                identities,
                full_name=(
                    payload.get("full_name")
                    or payload.get("player_display_name")
                    or payload.get("player_name")
                    or payload.get("name")
                ),
                position=payload.get("position"),
                create=False,
            )
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    players_after = int(db.session.scalar(select(func.count()).select_from(Player)) or 0)
    identities_after = int(db.session.scalar(select(func.count()).select_from(PlayerExternalIdentity)) or 0)
    result["players_merged"] = players_before - players_after
    result["identity_links_added"] = identities_after - identities_before
    return result
