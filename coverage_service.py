"""Multi-source coverage and freshness reporting for the NFL warehouse."""
from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import func, select

from database import db
from db_models import (
    DataSource,
    DataSyncRun,
    DepthChartEntry,
    Game,
    InjuryReport,
    PlayerSeasonStat,
    PlayerTeamSeason,
    RawIngestRecord,
    SnapCount,
    Team,
    TeamGameStat,
)

EXPECTED_TEAMS = (
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE",
    "DAL", "DEN", "DET", "GB", "HOU", "IND", "JAC", "KC",
    "LAC", "LAR", "LV", "MIA", "MIN", "NE", "NO", "NYG",
    "NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WAS",
)

DATASET_LABELS = {
    "rosters": "Roster membership",
    "injuries": "Injury reports",
    "depth_charts": "Depth charts",
    "snap_counts": "Snap counts",
    "team_stats": "Team game statistics",
    "player_stats": "Player season statistics",
    "transactions": "League transactions",
}

DATASET_PROVIDERS = {
    "rosters": ["nflverse"],
    "injuries": ["nflverse"],
    "depth_charts": ["nflverse"],
    "snap_counts": ["nflverse"],
    "team_stats": ["nflverse"],
    "player_stats": ["nflverse"],
    "transactions": ["sportsdataio"],
}


def expected_team_abbreviations() -> tuple[str, ...]:
    """Return the canonical 32-team set used by coverage checks."""
    return EXPECTED_TEAMS


def summarize_team_coverage(observed: set[str] | list[str] | tuple[str, ...]) -> dict:
    """Summarize a team set without requiring the Flask application or DB."""
    present = {str(value).upper() for value in observed if value}
    expected = set(EXPECTED_TEAMS)
    covered = sorted(present & expected)
    missing = sorted(expected - set(covered))
    percentage = round(len(covered) / len(expected) * 100, 1)
    status = "complete" if not missing else ("empty" if not covered else "partial")
    return {
        "covered_team_count": len(covered),
        "expected_team_count": len(expected),
        "coverage_pct": percentage,
        "covered_teams": covered,
        "missing_teams": missing,
        "status": status,
    }


def _team_rows(model, season: int) -> set[str]:
    rows = db.session.execute(
        select(Team.abbreviation)
        .join(model, model.team_id == Team.id)
        .where(model.season == season)
        .distinct()
    ).scalars()
    return {value for value in rows if value}


def _team_game_rows(season: int) -> set[str]:
    rows = db.session.execute(
        select(Team.abbreviation)
        .join(TeamGameStat, TeamGameStat.team_id == Team.id)
        .join(Game, Game.id == TeamGameStat.game_id)
        .where(Game.season == season)
        .distinct()
    ).scalars()
    return {value for value in rows if value}


def _dataset_rows(dataset: str, season: int) -> set[str]:
    if dataset == "team_stats":
        return _team_game_rows(season)
    if dataset == "transactions":
        return set()
    model_map = {
        "rosters": PlayerTeamSeason,
        "injuries": InjuryReport,
        "depth_charts": DepthChartEntry,
        "snap_counts": SnapCount,
        "player_stats": PlayerSeasonStat,
    }
    return _team_rows(model_map[dataset], season)


def _dataset_count(dataset: str, season: int) -> int:
    if dataset == "team_stats":
        query = (
            select(func.count())
            .select_from(TeamGameStat)
            .join(Game, Game.id == TeamGameStat.game_id)
            .where(Game.season == season)
        )
    elif dataset == "transactions":
        query = (
            select(func.count())
            .select_from(RawIngestRecord)
            .join(DataSource, DataSource.id == RawIngestRecord.source_id)
            .where(
                DataSource.key == "sportsdataio",
                RawIngestRecord.entity_type == "transaction",
                RawIngestRecord.season == season,
            )
        )
    else:
        model_map = {
            "rosters": PlayerTeamSeason,
            "injuries": InjuryReport,
            "depth_charts": DepthChartEntry,
            "snap_counts": SnapCount,
            "player_stats": PlayerSeasonStat,
        }
        query = (
            select(func.count())
            .select_from(model_map[dataset])
            .where(model_map[dataset].season == season)
        )
    return int(db.session.scalar(query) or 0)


def _source_snapshot(source: DataSource | None) -> dict:
    if not source:
        return {
            "registered": False,
            "last_success_at": None,
            "last_failure_at": None,
            "freshness": "never_synced",
            "raw_versions": 0,
        }

    raw_versions = db.session.scalar(
        select(func.count())
        .select_from(RawIngestRecord)
        .where(RawIngestRecord.source_id == source.id)
    )
    success = source.last_success_at
    failure = source.last_failure_at
    if not success:
        freshness = "never_synced"
    elif failure and failure > success:
        freshness = "degraded"
    else:
        freshness = "healthy"
    return {
        "registered": True,
        "last_success_at": success.isoformat() if success else None,
        "last_failure_at": failure.isoformat() if failure else None,
        "freshness": freshness,
        "raw_versions": int(raw_versions or 0),
    }


def _provider_status() -> list[dict]:
    try:
        from integration_hub import integration_status

        return integration_status()["providers"]
    except Exception:
        return []


def _provider_chain() -> list[dict]:
    enabled = {
        item.strip()
        for item in os.environ.get("ENABLED_PROVIDERS", "nflverse,nws").split(",")
        if item.strip()
    }
    providers = []
    if "nflverse" in enabled:
        providers.append({
            "provider": "nflverse",
            "role": "primary",
            "reason": "public rosters, injuries, depth charts, snap counts, and historical statistics",
        })
    if "sportsdataio" in enabled and os.getenv("SPORTSDATAIO_API_KEY"):
        providers.append({
            "provider": "sportsdataio",
            "role": "credentialed_fallback",
            "reason": "live games, transactions, and licensed production feeds",
        })
    if not providers:
        providers.append({
            "provider": "nflverse",
            "role": "primary",
            "reason": "default public source",
        })
    return providers


def coverage_report(season: int) -> dict:
    """Build a read-only report for operator and dashboard consumption."""
    active_teams = set(
        db.session.scalars(
            select(Team.abbreviation).where(Team.active.is_(True))
        ).all()
    )
    team_master = summarize_team_coverage(active_teams)
    datasets = {}
    for dataset, label in DATASET_LABELS.items():
        observed = _dataset_rows(dataset, season)
        summary = summarize_team_coverage(observed)
        summary.update({
            "label": label,
            "record_count": _dataset_count(dataset, season),
            "providers": DATASET_PROVIDERS[dataset],
        })
        datasets[dataset] = summary

    source_rows = db.session.scalars(
        select(DataSource).order_by(DataSource.key)
    ).all()
    sources = []
    for source in source_rows:
        sources.append({
            "key": source.key,
            "name": source.name,
            "type": source.source_type,
            "enabled": source.enabled,
            "base_url": source.base_url,
            **_source_snapshot(source),
        })

    known = {item["key"] for item in sources}
    for provider in _provider_status():
        if provider["key"] not in known:
            sources.append({
                "key": provider["key"],
                "name": provider["label"],
                "type": provider["kind"],
                "enabled": provider["enabled"],
                "configured": provider["configured"],
                "last_success_at": None,
                "last_failure_at": None,
                "freshness": "never_synced",
                "raw_versions": 0,
            })

    coverage_values = [
        datasets[name]["coverage_pct"]
        for name in ("rosters", "team_stats", "player_stats")
    ]
    overall_pct = min(coverage_values) if coverage_values else 0.0
    return {
        "contract_version": "4.5.5",
        "season": season,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "team_master": team_master,
        "datasets": datasets,
        "overall": {
            "coverage_pct": overall_pct,
            "status": "complete" if overall_pct == 100.0 else "partial",
        },
        "provider_chain": _provider_chain(),
        "sources": sources,
        "latest_sync": _latest_sync(),
    }


def _latest_sync() -> dict | None:
    latest = db.session.scalar(
        select(DataSyncRun)
        .where(DataSyncRun.finished_at.is_not(None))
        .order_by(DataSyncRun.finished_at.desc())
        .limit(1)
    )
    if not latest:
        return None
    return {
        "source": latest.source,
        "status": latest.status,
        "finished_at": latest.finished_at.isoformat() if latest.finished_at else None,
        "records_read": latest.records_read,
        "records_written": latest.records_written,
        "error": latest.error,
    }
