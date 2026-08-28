"""P3.6 production verification for fresh market price actionability."""
from __future__ import annotations

import time
from typing import Any

import decision_delivery as dd
import market_pricing as mp
import nfl_data
import odds_api
from routes.props import _build_week_rows


def _unique_target_game(rows: list[dict[str, Any]]) -> str | None:
    delivery = dd.build_delivery(rows, limit=25)
    for row in delivery.get("picks") or []:
        game_id = str(row.get("gameId") or "")
        if game_id:
            return game_id
    return None


def readiness_snapshot(
    target_season: int = 2026,
    *,
    refresh_mode: str = "cache",
) -> dict[str, Any]:
    """Verify P3.6 using cache-only reads, with one explicit optional refresh.

    ``refresh_mode='one-event'`` may spend Odds API credits and is only intended
    for the protected workflow's explicit confirmation option. All verification
    after that targeted refresh is cache-only.
    """
    started = time.monotonic()
    current = nfl_data.current_week(target_season)
    week = int(current["week"])
    season_type = str(current.get("season_type") or "REG")
    refresh = str(refresh_mode).lower() == "one-event"
    refresh_result = None

    if refresh:
        model_rows, model_errors, _ = _build_week_rows(
            target_season,
            week,
            season_type,
            include_odds=False,
        )
        target_game_id = _unique_target_game(model_rows)
        games = {
            str(game["game_id"]): game
            for game in nfl_data.get_week_games(target_season, week, season_type)
        }
        game = games.get(target_game_id or "")
        if game is not None:
            refresh_result = {
                "gameId": target_game_id,
                **odds_api.refresh_game_props(game),
            }
        else:
            refresh_result = {
                "gameId": target_game_id,
                "ok": False,
                "reason": "no_model_pick_game_available",
            }
        if model_errors:
            refresh_result["modelErrors"] = model_errors

    rows, errors, game_count = _build_week_rows(
        target_season,
        week,
        season_type,
        include_odds=True,
        cache_only_odds=True,
    )
    contract = mp.verify_price_contract(rows)
    priced = [row for row in rows if row.get("priceStatus") not in {None, "unpriced"}]
    fresh = [row for row in rows if row.get("quoteStatus") == "fresh"]
    positive = [row for row in rows if row.get("priceStatus") == "positive_value"]
    actionable = [row for row in rows if row.get("actionable")]
    games_with_prices = {str(row.get("gameId")) for row in priced if row.get("gameId")}
    fresh_books = {
        str((row.get("bestPrice") or {}).get("book"))
        for row in fresh
        if (row.get("bestPrice") or {}).get("book")
    }
    max_paired_books = max((int(row.get("pairedFairBookCount") or 0) for row in fresh), default=0)
    provider = odds_api.snapshot_status()
    build_seconds = round(time.monotonic() - started, 3)

    thresholds = {
        "minimumDecisionRows": 50,
        "minimumPricedRows": 5,
        "minimumFreshRows": 5,
        "minimumFreshBooks": 1,
        "maximumGameErrors": 0,
        "maximumBuildSeconds": 20.0,
    }
    gates = {
        **contract["gates"],
        "provider_configured": bool(provider.get("configured")),
        "decision_volume": len(rows) >= thresholds["minimumDecisionRows"],
        "priced_row_pool": len(priced) >= thresholds["minimumPricedRows"],
        "fresh_row_pool": len(fresh) >= thresholds["minimumFreshRows"],
        "fresh_book_coverage": len(fresh_books) >= thresholds["minimumFreshBooks"],
        "game_errors": errors <= thresholds["maximumGameErrors"],
        "bounded_build_time": build_seconds <= thresholds["maximumBuildSeconds"],
    }
    if refresh:
        gates["explicit_refresh_succeeded"] = bool(refresh_result and refresh_result.get("ok"))

    return {
        "phase": "P3.6",
        "mode": "one-event-refresh-then-cache-only" if refresh else "cache-only",
        "targetSeason": target_season,
        "week": week,
        "seasonType": season_type,
        "games": game_count,
        "buildSeconds": build_seconds,
        "provider": provider,
        "refresh": refresh_result,
        "pricing": {
            "rows": len(rows),
            "pricedRows": len(priced),
            "freshRows": len(fresh),
            "positiveValueRows": len(positive),
            "actionableRows": len(actionable),
            "gamesWithPrices": len(games_with_prices),
            "freshBooks": len(fresh_books),
            "maxPairedFairBooks": max_paired_books,
        },
        "thresholds": thresholds,
        "gates": gates,
        "ok": all(gates.values()),
    }
