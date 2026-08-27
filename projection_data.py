"""Warehouse-backed player evidence for projections and prop intelligence.

P3.2 moves the decision surfaces off request-time CSV/boxscore discovery. The
normalized warehouse becomes the canonical source for player histories while
``nfl_data`` remains responsible for schedule/live-score collection.

The target-season roster is deliberately separated from the statistics season:
before enough current regular-season evidence exists, returning players use the
prior season's game history but are assigned to their *current* roster/team.
Preseason facts are retained in the warehouse for role/coverage analysis but
are excluded from projection distributions so exhibition usage cannot distort
regular-season yardage and touchdown expectations.
"""
from __future__ import annotations

import os
import threading
import time
from collections import defaultdict
from typing import Any

from sqlalchemy import distinct, func, select

from database import db
from db_models import Game, Player, PlayerGameStat, PlayerTeamSeason, Team

SKILL_POSITIONS = frozenset({"QB", "RB", "FB", "WR", "TE"})
STAT_FIELDS = (
    "completions",
    "attempts",
    "passing_yards",
    "passing_tds",
    "interceptions",
    "sacks",
    "carries",
    "rushing_yards",
    "rushing_tds",
    "receptions",
    "targets",
    "receiving_yards",
    "receiving_tds",
    "fumbles_lost",
)
_DVP_FIELDS = (
    "passing_yards",
    "rushing_yards",
    "receiving_yards",
    "receptions",
    "targets",
    "carries",
    "passing_tds",
    "rushing_tds",
    "receiving_tds",
)
_CACHE: dict[tuple[Any, ...], tuple[float, Any]] = {}
_CACHE_LOCK = threading.RLock()
_CACHE_MISS = object()


def _cache_ttl() -> float:
    return max(float(os.environ.get("PROJECTION_DATA_CACHE_SECONDS", "300")), 0.0)


def _cache_get(key: tuple[Any, ...]):
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        _CACHE.pop(key, None)
    return _CACHE_MISS


def _cache_set(key: tuple[Any, ...], value: Any) -> Any:
    ttl = _cache_ttl()
    if ttl <= 0:
        return value
    with _CACHE_LOCK:
        _CACHE[key] = (time.monotonic() + ttl, value)
    return value


def clear_projection_cache() -> None:
    """Invalidate short-lived normalized evidence after an explicit sync."""
    with _CACHE_LOCK:
        _CACHE.clear()


def player_key(player: Player) -> str:
    """Stable public key, preferring the ESPN id used by the existing UI."""
    return str(player.espn_id or player.external_id or player.id)


def _row_count(season: int) -> int:
    return int(
        db.session.scalar(
            select(func.count())
            .select_from(PlayerGameStat)
            .join(Game, Game.id == PlayerGameStat.game_id)
            .where(Game.season == season)
        )
        or 0
    )


def regular_weeks_with_stats(season: int) -> list[int]:
    rows = db.session.scalars(
        select(distinct(Game.week))
        .select_from(Game)
        .join(PlayerGameStat, PlayerGameStat.game_id == Game.id)
        .where(Game.season == season, Game.season_type == "REG")
        .order_by(Game.week)
    ).all()
    return [int(week) for week in rows if week is not None]


def stats_season(target_season: int) -> int:
    """Choose a statistically defensible season for player projections.

    Preseason data is stored and queryable, but it cannot replace a full prior
    regular-season baseline. The current season becomes primary only after the
    configured number of regular-season weeks has landed in the warehouse.
    """
    minimum_weeks = max(int(os.environ.get("P32_CURRENT_REG_WEEKS", "3")), 1)
    if len(regular_weeks_with_stats(target_season)) >= minimum_weeks:
        return target_season
    prior = target_season - 1
    if _row_count(prior) > 0:
        return prior
    if _row_count(target_season) > 0:
        return target_season
    return prior


def player_game_logs(season: int) -> dict[str, list[dict[str, Any]]]:
    """Return REG/POST canonical histories in the legacy projection schema."""
    cache_key = ("logs", season)
    cached = _cache_get(cache_key)
    if cached is not _CACHE_MISS:
        return cached

    teams = {
        int(team_id): abbreviation
        for team_id, abbreviation in db.session.execute(select(Team.id, Team.abbreviation)).all()
    }
    query = (
        select(PlayerGameStat, Game, Player)
        .join(Game, Game.id == PlayerGameStat.game_id)
        .join(Player, Player.id == PlayerGameStat.player_id)
        .where(Game.season == season, Game.season_type.in_(("REG", "POST")))
        .order_by(Game.kickoff_at, Game.week, Game.id, PlayerGameStat.id)
    )
    logs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for stat, game, player in db.session.execute(query).all():
        key = player_key(player)
        row: dict[str, Any] = {
            "season": game.season,
            "season_type": game.season_type,
            "week": game.week,
            "game_id": game.external_id,
            "gameday": game.kickoff_at.date().isoformat() if game.kickoff_at else "",
            "team": teams.get(stat.team_id, ""),
            "opponent": teams.get(stat.opponent_id, ""),
            "home": int(bool(stat.home)),
            "player_id": key,
            "player_name": player.full_name,
            "position": stat.position or player.position or "",
        }
        for field in STAT_FIELDS:
            value = getattr(stat, field, 0) or 0
            row[field] = float(value)
        row["completions"] = int(row["completions"])
        row["attempts"] = int(row["attempts"])
        logs[key].append(row)
    return _cache_set(cache_key, dict(logs))


def player_index(target_season: int, evidence_season: int | None = None) -> dict[str, dict[str, Any]]:
    """Current roster metadata joined to the chosen statistical history.

    If a player changed teams, the most recently touched current-season
    membership wins. P3.1 imports weekly rosters chronologically, so this is the
    latest observed team while retaining every historical membership for
    auditability.
    """
    evidence_season = evidence_season or stats_season(target_season)
    cache_key = ("index", target_season, evidence_season)
    cached = _cache_get(cache_key)
    if cached is not _CACHE_MISS:
        return cached

    logs = player_game_logs(evidence_season)
    current: dict[str, dict[str, Any]] = {}
    query = (
        select(PlayerTeamSeason, Player, Team)
        .join(Player, Player.id == PlayerTeamSeason.player_id)
        .join(Team, Team.id == PlayerTeamSeason.team_id)
        .where(PlayerTeamSeason.season == target_season)
        .order_by(PlayerTeamSeason.updated_at, PlayerTeamSeason.id)
    )
    for membership, player, team in db.session.execute(query).all():
        key = player_key(player)
        position = (player.position or membership.depth_position or "").upper()
        current[key] = {
            "player_id": key,
            "canonicalPlayerId": player.id,
            "name": player.full_name,
            "team": team.abbreviation,
            "position": position,
            "status": membership.status,
            "games": len(logs.get(key, [])),
            "evidenceSeason": evidence_season,
            "rosterSeason": target_season,
            "rosterVerified": True,
        }

    if current:
        return _cache_set(cache_key, current)

    # Test/degraded environments may not yet have the target-season roster.
    # Preserve read availability from the evidence season, but mark every row
    # unverified so production readiness cannot mistake the fallback for P3.1.
    for key, history in logs.items():
        if not history:
            continue
        last = history[-1]
        current[key] = {
            "player_id": key,
            "canonicalPlayerId": None,
            "name": last["player_name"],
            "team": last["team"],
            "position": last["position"],
            "status": None,
            "games": len(history),
            "evidenceSeason": evidence_season,
            "rosterSeason": target_season,
            "rosterVerified": False,
        }
    return _cache_set(cache_key, current)


def _position_group(position: str) -> str | None:
    pos = (position or "").upper()
    if pos in {"QB", "RB", "WR", "TE"}:
        return pos
    if pos == "FB":
        return "RB"
    return None


def defense_vs_position(season: int) -> dict[str, dict[str, Any]]:
    """Defense-vs-position aggregates derived from normalized player facts."""
    cache_key = ("dvp", season)
    cached = _cache_get(cache_key)
    if cached is not _CACHE_MISS:
        return cached

    acc: dict[str, dict[str, dict[str, float]]] = {}
    games_per_team: dict[str, set[str]] = {}
    for history in player_game_logs(season).values():
        for row in history:
            group = _position_group(str(row.get("position") or ""))
            defense = str(row.get("opponent") or "")
            if not group or not defense:
                continue
            games_per_team.setdefault(defense, set()).add(str(row["game_id"]))
            cell = acc.setdefault(defense, {}).setdefault(
                group, {field: 0.0 for field in _DVP_FIELDS}
            )
            for field in _DVP_FIELDS:
                cell[field] += float(row.get(field) or 0)

    league: dict[str, dict[str, list[float]]] = {}
    out: dict[str, dict[str, Any]] = {}
    for team, groups in acc.items():
        games = max(len(games_per_team.get(team, ())), 1)
        out[team] = {"games": games}
        for group, totals in groups.items():
            per_game = {field: round(totals[field] / games, 2) for field in _DVP_FIELDS}
            out[team][group] = per_game
            for field, value in per_game.items():
                league.setdefault(group, {}).setdefault(field, []).append(value)

    for groups in out.values():
        for group, per_game in list(groups.items()):
            if group == "games":
                continue
            ratios: dict[str, float] = {}
            for field, value in per_game.items():
                values = league.get(group, {}).get(field, [])
                mean = sum(values) / len(values) if values else 0.0
                ratios[f"{field}_ratio"] = round(value / mean, 3) if mean else 1.0
            per_game.update(ratios)
    return _cache_set(cache_key, out)


def projection_pool_snapshot(target_season: int) -> dict[str, Any]:
    """Aggregate-only readiness metrics safe to emit in production CI logs."""
    evidence = stats_season(target_season)
    logs = player_game_logs(evidence)
    index = player_index(target_season, evidence)
    roster_verified = {
        key: meta for key, meta in index.items() if bool(meta.get("rosterVerified"))
    }
    skill = {
        key: meta
        for key, meta in roster_verified.items()
        if str(meta.get("position") or "").upper() in SKILL_POSITIONS
    }
    ready = {
        key: meta for key, meta in skill.items() if len(logs.get(key, [])) >= 3
    }
    coverage = round(len(ready) / len(skill), 4) if skill else 0.0
    return {
        "target_season": target_season,
        "evidence_season": evidence,
        "evidence_rows": sum(len(rows) for rows in logs.values()),
        "evidence_players": len(logs),
        "current_roster_players": len(index),
        "roster_verified_players": len(roster_verified),
        "current_skill_players": len(skill),
        "projection_ready_skill_players": len(ready),
        "projection_ready_skill_coverage": coverage,
        "current_regular_weeks": regular_weeks_with_stats(target_season),
    }
