"""P4.2 live game-market hydration and persisted actionable board.

P4.1 proved the pricing/actionability math but production verification showed
that the shared game-odds snapshot could be empty for the target Week 1 slate.
P4.2 adds a deliberately bounded live hydration path and a separate durable
weekly market snapshot. Product reads remain cache-only: only an explicit
hydration command is allowed to spend provider credits.
"""
from __future__ import annotations

from datetime import UTC, datetime
import time
from collections import Counter
from typing import Any, Iterable

import nfl_data
import odds_api
import p40_game_intelligence as p40
import p41_game_market_pricing as p41
import provider_cache_store

MODEL_NAME = "p4.2-live-market-hydration"
MODEL_VERSION = "p42-hydration-v1"
CACHE_PROVIDER_KEY = "p4.2-game-market-hydration"
GAME_MARKETS = ["h2h", "spreads", "totals"]
MAX_TARGETED_REQUESTS = 16


def _week_key(season: int, week: int, season_type: str) -> str:
    return f"{int(season)}:{str(season_type).upper()}:{int(week)}"


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _nickname(value: Any) -> str:
    parts = _norm(value).split()
    return parts[-1] if parts else ""


def _match_event(game: dict[str, Any], events: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """Match schedule and provider events without requiring provider IDs in schedule."""
    home_name = _norm(game.get("home_name"))
    away_name = _norm(game.get("away_name"))
    rows = list(events)
    if home_name and away_name:
        for event in rows:
            if _norm(event.get("home_team")) == home_name and _norm(event.get("away_team")) == away_name:
                return event

    home_nick = _nickname(game.get("home_name"))
    away_nick = _nickname(game.get("away_name"))
    if not home_nick or not away_nick:
        return None
    for event in rows:
        if _nickname(event.get("home_team")) == home_nick and _nickname(event.get("away_team")) == away_nick:
            return event
    return None


def _event_has_game_markets(event: dict[str, Any] | None) -> bool:
    if not event:
        return False
    wanted = set(GAME_MARKETS)
    return any(
        market.get("key") in wanted
        for bookmaker in event.get("bookmakers", [])
        for market in bookmaker.get("markets", [])
    )


def _merge_event_metadata(catalog_event: dict[str, Any], odds_event: dict[str, Any]) -> dict[str, Any]:
    merged = dict(catalog_event)
    merged.update(odds_event)
    if not merged.get("id"):
        merged["id"] = catalog_event.get("id")
    if not merged.get("home_team"):
        merged["home_team"] = catalog_event.get("home_team")
    if not merged.get("away_team"):
        merged["away_team"] = catalog_event.get("away_team")
    if not merged.get("commence_time"):
        merged["commence_time"] = catalog_event.get("commence_time")
    return merged


def _load_root() -> dict[str, Any]:
    snapshot = provider_cache_store.load_snapshot(CACHE_PROVIDER_KEY)
    root = snapshot.get("game_odds") if isinstance(snapshot, dict) else None
    return dict(root) if isinstance(root, dict) else {"weeks": {}}


def _load_week_snapshot(season: int, week: int, season_type: str) -> dict[str, Any] | None:
    root = _load_root()
    weeks = root.get("weeks") if isinstance(root.get("weeks"), dict) else {}
    payload = weeks.get(_week_key(season, week, season_type))
    return dict(payload) if isinstance(payload, dict) else None


def _save_week_snapshot(season: int, week: int, season_type: str, payload: dict[str, Any]) -> bool:
    root = _load_root()
    weeks = root.get("weeks") if isinstance(root.get("weeks"), dict) else {}
    weeks = dict(weeks)
    weeks[_week_key(season, week, season_type)] = payload
    root["weeks"] = weeks
    root["latestWeekKey"] = _week_key(season, week, season_type)
    root["updatedAt"] = datetime.now(UTC).isoformat()
    return provider_cache_store.save_snapshot(CACHE_PROVIDER_KEY, {"game_odds": root})


def cache_status(season: int | None = None, week: int | None = None, season_type: str = "REG") -> dict[str, Any]:
    root = _load_root()
    weeks = root.get("weeks") if isinstance(root.get("weeks"), dict) else {}
    selected = None
    if season is not None and week is not None:
        selected = weeks.get(_week_key(season, week, season_type))
    hydrated_at = selected.get("hydratedAtEpoch") if isinstance(selected, dict) else None
    age = None
    if isinstance(hydrated_at, (int, float)):
        age = round(max(0.0, time.time() - float(hydrated_at)), 1)
    return {
        "provider": CACHE_PROVIDER_KEY,
        "persistedWeeks": len(weeks),
        "latestWeekKey": root.get("latestWeekKey"),
        "selectedWeekAvailable": isinstance(selected, dict),
        "selectedWeekAgeSeconds": age,
        "persistence": provider_cache_store.cache_status(CACHE_PROVIDER_KEY),
    }


def _provider_catalog() -> list[dict[str, Any]]:
    """Fetch the provider event catalog.

    This intentionally uses the same configured provider gate as odds_api. The
    catalog is used only during an explicit live hydration command.
    """
    data = odds_api._get(f"/sports/{odds_api.SPORT}/events")  # noqa: SLF001
    return list(data) if isinstance(data, list) else []


def hydrate_week(
    season: int,
    week: int,
    season_type: str = "REG",
    *,
    allow_provider_spend: bool = False,
    max_targeted_requests: int = 4,
) -> dict[str, Any]:
    """Hydrate one weekly NFL market snapshot with an explicit spend gate.

    Request strategy is intentionally economical:

    1. one bulk game-odds refresh;
    2. only when schedule games remain unmatched, one event-catalog request;
    3. targeted event-odds requests only for still-missing schedule games,
       capped by ``max_targeted_requests``.
    """
    stype = str(season_type).upper()
    if stype not in {"PRE", "REG", "POST"}:
        raise ValueError("season_type must be PRE, REG, or POST")
    max_targeted = max(0, min(MAX_TARGETED_REQUESTS, int(max_targeted_requests)))
    games = nfl_data.get_week_games(int(season), int(week), stype, live=False)
    base = {
        "phase": "P4.2",
        "model": MODEL_NAME,
        "modelVersion": MODEL_VERSION,
        "season": int(season),
        "seasonType": stype,
        "week": int(week),
        "scheduleGameCount": len(games),
        "providerConfigured": odds_api.is_configured(),
        "providerSpendAllowed": bool(allow_provider_spend),
        "maxTargetedRequests": max_targeted,
    }
    if not allow_provider_spend:
        return {
            **base,
            "ok": False,
            "state": "blocked",
            "reason": "explicit_provider_spend_confirmation_required",
            "providerRequests": 0,
        }
    if not odds_api.is_configured():
        return {
            **base,
            "ok": False,
            "state": "unavailable",
            "reason": "odds_provider_not_configured",
            "providerRequests": 0,
        }
    if not games:
        return {
            **base,
            "ok": False,
            "state": "unavailable",
            "reason": "schedule_week_not_available",
            "providerRequests": 0,
        }

    provider_requests = 0
    bulk_events = odds_api.get_game_odds(force=True)
    provider_requests += 1
    bulk_events = list(bulk_events) if isinstance(bulk_events, list) else []

    event_by_game: dict[str, dict[str, Any]] = {}
    source_by_game: dict[str, str] = {}
    for game in games:
        match = _match_event(game, bulk_events)
        if match is not None:
            game_id = str(game.get("game_id"))
            event_by_game[game_id] = match
            source_by_game[game_id] = "bulk"

    unmatched = [game for game in games if str(game.get("game_id")) not in event_by_game]
    catalog: list[dict[str, Any]] = []
    targeted_requests = 0
    catalog_matched = 0
    targeted_with_markets = 0

    if unmatched:
        catalog = _provider_catalog()
        provider_requests += 1
        for game in unmatched:
            if targeted_requests >= max_targeted:
                break
            catalog_event = _match_event(game, catalog)
            if catalog_event is None or not catalog_event.get("id"):
                continue
            catalog_matched += 1
            targeted_requests += 1
            provider_requests += 1
            odds_event = odds_api.fetch_event_odds_live(
                str(catalog_event["id"]),
                markets=GAME_MARKETS,
            )
            if not isinstance(odds_event, dict):
                continue
            merged = _merge_event_metadata(catalog_event, odds_event)
            if _event_has_game_markets(merged):
                targeted_with_markets += 1
            game_id = str(game.get("game_id"))
            event_by_game[game_id] = merged
            source_by_game[game_id] = "targeted"

    hydrated_at_epoch = time.time()
    hydrated_at = datetime.fromtimestamp(hydrated_at_epoch, UTC).isoformat()
    market_ready_games = [
        game_id for game_id, event in event_by_game.items() if _event_has_game_markets(event)
    ]
    missing_games = [
        {
            "gameId": str(game.get("game_id")),
            "homeTeam": game.get("home_team"),
            "awayTeam": game.get("away_team"),
            "homeName": game.get("home_name"),
            "awayName": game.get("away_name"),
            "reason": "provider_event_or_markets_not_available",
        }
        for game in games
        if str(game.get("game_id")) not in market_ready_games
    ]
    snapshot = {
        "season": int(season),
        "seasonType": stype,
        "week": int(week),
        "hydratedAt": hydrated_at,
        "hydratedAtEpoch": hydrated_at_epoch,
        "scheduleGameCount": len(games),
        "bulkEventCount": len(bulk_events),
        "catalogEventCount": len(catalog),
        "matchedGameCount": len(event_by_game),
        "marketReadyGameCount": len(market_ready_games),
        "providerRequests": provider_requests,
        "targetedRequests": targeted_requests,
        "targetedWithMarkets": targeted_with_markets,
        "catalogMatched": catalog_matched,
        "gameEventIds": {
            game_id: str(event.get("id") or "") for game_id, event in event_by_game.items()
        },
        "sourceByGame": source_by_game,
        "events": list(event_by_game.values()),
        "missingGames": missing_games,
    }
    persisted = _save_week_snapshot(int(season), int(week), stype, snapshot)
    return {
        **base,
        "ok": persisted,
        "state": "hydrated" if persisted else "persistence_failed",
        "reason": None if persisted else "weekly_market_snapshot_not_persisted",
        "providerRequests": provider_requests,
        "bulkEventCount": len(bulk_events),
        "catalogEventCount": len(catalog),
        "matchedGameCount": len(event_by_game),
        "marketReadyGameCount": len(market_ready_games),
        "missingGameCount": len(missing_games),
        "targetedRequests": targeted_requests,
        "targetedWithMarkets": targeted_with_markets,
        "persisted": persisted,
        "hydratedAt": hydrated_at,
    }


def build_cached_week_board(season: int, week: int, season_type: str = "REG") -> dict[str, Any]:
    """Build the user-facing game board from the durable P4.2 cache only."""
    stype = str(season_type).upper()
    snapshot = _load_week_snapshot(int(season), int(week), stype)
    model_report = p40.build_week_report(int(season), int(week), stype)
    if snapshot is None:
        rows = [
            p41.price_game_decision(decision, None, fetched_at=None)
            for decision in model_report.get("decisions", [])
        ]
        return {
            "available": model_report.get("available", False),
            "model": MODEL_NAME,
            "modelVersion": MODEL_VERSION,
            "sourceModelVersion": model_report.get("modelVersion"),
            "season": int(season),
            "seasonType": stype,
            "week": int(week),
            "hydrationState": "missing",
            "hydratedAt": None,
            "hydrationAgeSeconds": None,
            "gameCount": model_report.get("gameCount", 0),
            "decisionCount": len(rows),
            "pricedGameCount": 0,
            "freshPricedGameCount": 0,
            "actionableGameCount": 0,
            "marketCoverage": {},
            "freshMarketCoverage": {},
            "actionableMarkets": {},
            "rows": rows,
            "safety": {
                "cacheOnlyProductReads": True,
                "freshQuoteRequired": True,
                "pairedFairBookRequired": True,
                "liveHydrationRequiresExplicitWorkflow": True,
            },
        }

    event_by_id = {
        str(event.get("id")): event
        for event in snapshot.get("events", [])
        if isinstance(event, dict) and event.get("id")
    }
    game_event_ids = snapshot.get("gameEventIds") if isinstance(snapshot.get("gameEventIds"), dict) else {}
    fetched_at = snapshot.get("hydratedAtEpoch")
    rows: list[dict[str, Any]] = []
    for decision in model_report.get("decisions", []):
        event_id = str(game_event_ids.get(str(decision.get("gameId"))) or "")
        event = event_by_id.get(event_id)
        rows.append(
            p41.price_game_decision(
                decision,
                event,
                fetched_at=float(fetched_at) if isinstance(fetched_at, (int, float)) else None,
            )
        )

    market_rows = [
        (market_key, market)
        for row in rows
        for market_key, market in (row.get("markets") or {}).items()
    ]
    market_coverage = Counter(
        market_key
        for market_key, market in market_rows
        if (market.get("pricing") or {}).get("quoteStatus") != "unpriced"
    )
    fresh_coverage = Counter(
        market_key
        for market_key, market in market_rows
        if (market.get("pricing") or {}).get("quoteStatus") == "fresh"
    )
    actionable_markets = Counter(
        market_key for market_key, market in market_rows if market.get("actionable")
    )
    priced = [row for row in rows if row.get("marketStatus") in {"fresh", "stale"}]
    fresh_priced = [row for row in rows if row.get("marketStatus") == "fresh"]
    actionable = [row for row in rows if row.get("actionable")]
    hydration_age = None
    if isinstance(fetched_at, (int, float)):
        hydration_age = round(max(0.0, time.time() - float(fetched_at)), 1)

    return {
        "available": model_report.get("available", False),
        "model": MODEL_NAME,
        "modelVersion": MODEL_VERSION,
        "sourceModelVersion": model_report.get("modelVersion"),
        "season": int(season),
        "seasonType": stype,
        "week": int(week),
        "hydrationState": "available",
        "hydratedAt": snapshot.get("hydratedAt"),
        "hydrationAgeSeconds": hydration_age,
        "gameCount": model_report.get("gameCount", 0),
        "decisionCount": len(rows),
        "pricedGameCount": len(priced),
        "freshPricedGameCount": len(fresh_priced),
        "actionableGameCount": len(actionable),
        "marketCoverage": dict(sorted(market_coverage.items())),
        "freshMarketCoverage": dict(sorted(fresh_coverage.items())),
        "actionableMarkets": dict(sorted(actionable_markets.items())),
        "hydration": {
            "providerRequests": snapshot.get("providerRequests"),
            "bulkEventCount": snapshot.get("bulkEventCount"),
            "catalogEventCount": snapshot.get("catalogEventCount"),
            "matchedGameCount": snapshot.get("matchedGameCount"),
            "marketReadyGameCount": snapshot.get("marketReadyGameCount"),
            "targetedRequests": snapshot.get("targetedRequests"),
            "targetedWithMarkets": snapshot.get("targetedWithMarkets"),
            "missingGames": snapshot.get("missingGames", []),
        },
        "rows": rows,
        "safety": {
            "cacheOnlyProductReads": True,
            "freshQuoteRequired": True,
            "pairedFairBookRequired": True,
            "liveHydrationRequiresExplicitWorkflow": True,
        },
    }


def verify_board(board: dict[str, Any]) -> dict[str, Any]:
    rows = list(board.get("rows") or [])
    pricing_audit = p41.verify_actionability(rows)
    priced = int(board.get("pricedGameCount") or 0)
    fresh_priced = int(board.get("freshPricedGameCount") or 0)
    actionable = int(board.get("actionableGameCount") or 0)
    gates = {
        "decision_coverage": int(board.get("decisionCount") or 0) == int(board.get("gameCount") or 0),
        "real_priced_market_present": priced >= 1,
        "fresh_real_market_present": fresh_priced >= 1,
        "actionable_count_bounded": 0 <= actionable <= priced,
        "pricing_actionability_integrity": pricing_audit.get("ok") is True,
        "cache_only_product_contract": (board.get("safety") or {}).get("cacheOnlyProductReads") is True,
    }
    return {
        "ok": all(gates.values()),
        "gates": gates,
        "pricingAudit": pricing_audit,
    }
