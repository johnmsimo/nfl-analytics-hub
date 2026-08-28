"""P3.6 production verification for fresh market price actionability."""
from __future__ import annotations

import time
from typing import Any

import decision_delivery as dd
import market_pricing as mp
import nfl_data
import odds_api
import projections as pj
from routes.props import _build_week_rows


def _unique_target_game(rows: list[dict[str, Any]]) -> str | None:
    delivery = dd.build_delivery(rows, limit=100)
    for row in delivery.get("picks") or []:
        game_id = str(row.get("gameId") or "")
        if game_id:
            return game_id
    return None


def _priced_game_ids(rows: list[dict[str, Any]]) -> set[str]:
    return {
        str(row.get("gameId"))
        for row in rows
        if row.get("gameId") and row.get("priceStatus") not in {None, "unpriced"}
    }


def _select_refresh_target(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Prefer a model pick from a game already known to produce price rows."""
    priced_games = _priced_game_ids(rows)
    delivery = dd.build_delivery(rows, limit=max(len(rows), 100))
    for row in delivery.get("picks") or []:
        game_id = str(row.get("gameId") or "")
        if game_id and game_id in priced_games:
            return {
                "gameId": game_id,
                "reason": "ranked_pick_with_known_pricing",
                "knownPricedGames": len(priced_games),
            }

    for row in rows:
        game_id = str(row.get("gameId") or "")
        if game_id and game_id in priced_games:
            return {
                "gameId": game_id,
                "reason": "known_priced_game_fallback",
                "knownPricedGames": len(priced_games),
            }

    return {
        "gameId": _unique_target_game(rows),
        "reason": "model_pick_fallback_no_known_pricing",
        "knownPricedGames": 0,
    }


def _price_game_diagnostics(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_game: dict[str, dict[str, Any]] = {}
    for row in rows:
        game_id = str(row.get("gameId") or "")
        if not game_id:
            continue
        cell = by_game.setdefault(
            game_id,
            {
                "gameId": game_id,
                "rows": 0,
                "pricedRows": 0,
                "freshRows": 0,
                "actionableRows": 0,
                "snapshotAgeSeconds": None,
            },
        )
        cell["rows"] += 1
        if row.get("priceStatus") not in {None, "unpriced"}:
            cell["pricedRows"] += 1
        if row.get("quoteStatus") == "fresh":
            cell["freshRows"] += 1
        if row.get("actionable"):
            cell["actionableRows"] += 1
        age = row.get("oddsSnapshotAgeSeconds")
        if isinstance(age, (int, float)):
            current = cell.get("snapshotAgeSeconds")
            cell["snapshotAgeSeconds"] = round(
                float(age) if current is None else min(float(current), float(age)),
                1,
            )
    return sorted(
        by_game.values(),
        key=lambda cell: (-int(cell["freshRows"]), -int(cell["pricedRows"]), cell["gameId"]),
    )


def _provider_payload_diagnostic(
    game: dict[str, Any],
    model_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Explain why one cached provider snapshot does or does not map to app rows.

    This is cache-only. It never requests provider data and intentionally emits
    counts plus tiny samples rather than the full payload.
    """
    game_id = str(game.get("game_id") or "")
    event = odds_api.find_event_for_game(game, cache_only=True)
    if not event:
        return {
            "gameId": game_id,
            "eventId": None,
            "diagnosis": "provider_event_not_found",
            "snapshotAvailable": False,
        }

    event_id = str(event.get("id") or "")
    snapshot = odds_api.event_props_snapshot(event_id)
    data = snapshot.get("data")
    if not isinstance(data, dict):
        return {
            "gameId": game_id,
            "eventId": event_id,
            "diagnosis": "props_snapshot_missing_or_empty",
            "snapshotAvailable": bool(snapshot.get("available")),
            "snapshotAgeSeconds": snapshot.get("age_seconds"),
        }

    bookmakers = data.get("bookmakers") or []
    if not isinstance(bookmakers, list):
        bookmakers = []
    market_keys: set[str] = set()
    raw_outcomes = 0
    for bookmaker in bookmakers:
        if not isinstance(bookmaker, dict):
            continue
        markets = bookmaker.get("markets") or []
        if not isinstance(markets, list):
            continue
        for market in markets:
            if not isinstance(market, dict):
                continue
            key = str(market.get("key") or "")
            if key:
                market_keys.add(key)
            outcomes = market.get("outcomes") or []
            if isinstance(outcomes, list):
                raw_outcomes += len(outcomes)

    parsed = odds_api.parse_prop_markets(data, fetched_at=snapshot.get("fetched_at"))
    usable: list[tuple[dict[str, Any], str]] = []
    for quote in parsed:
        market = pj.ODDS_KEY_TO_MARKET.get(str(quote.get("base_key") or ""))
        if market and isinstance(quote.get("line"), (int, float)):
            usable.append((quote, market))

    projected_pairs = {
        (odds_api.norm_player_name(str(row.get("player") or "")), str(row.get("marketKey") or ""))
        for row in model_rows
        if str(row.get("gameId") or "") == game_id and row.get("player") and row.get("marketKey")
    }
    projected_players = {player for player, _ in projected_pairs if player}
    projected_markets = {market for _, market in projected_pairs if market}
    provider_pairs = {
        (odds_api.norm_player_name(str(quote.get("player") or "")), market)
        for quote, market in usable
        if quote.get("player")
    }
    provider_players = {player for player, _ in provider_pairs if player}
    provider_markets = {market for _, market in provider_pairs if market}
    player_overlap = provider_players & projected_players
    market_overlap = provider_markets & projected_markets
    pair_overlap = provider_pairs & projected_pairs

    recognized_market_keys = sorted(
        key
        for key in market_keys
        if pj.ODDS_KEY_TO_MARKET.get(key.replace("_alternate", "")) is not None
    )
    if not bookmakers:
        diagnosis = "provider_props_not_posted"
    elif not market_keys:
        diagnosis = "provider_props_not_posted"
    elif not parsed:
        diagnosis = "provider_markets_not_parseable"
    elif not usable:
        diagnosis = "provider_markets_not_supported_by_app"
    elif not player_overlap:
        diagnosis = "provider_player_names_do_not_overlap_model"
    elif not market_overlap:
        diagnosis = "provider_markets_do_not_overlap_model"
    elif not pair_overlap:
        diagnosis = "provider_player_market_pairs_do_not_overlap_model"
    else:
        diagnosis = "matchable_quotes_present"

    return {
        "gameId": game_id,
        "eventId": event_id,
        "diagnosis": diagnosis,
        "snapshotAvailable": bool(snapshot.get("available")),
        "snapshotAgeSeconds": snapshot.get("age_seconds"),
        "bookmakers": len(bookmakers),
        "marketKeys": sorted(market_keys)[:20],
        "recognizedMarketKeys": recognized_market_keys[:20],
        "rawOutcomes": raw_outcomes,
        "parsedQuoteRows": len(parsed),
        "usableQuoteRows": len(usable),
        "providerPlayers": len(provider_players),
        "projectedPlayers": len(projected_players),
        "playerOverlap": len(player_overlap),
        "providerMarkets": sorted(provider_markets),
        "projectedMarkets": sorted(projected_markets),
        "marketOverlap": sorted(market_overlap),
        "matchablePlayerMarketPairs": len(pair_overlap),
        "providerPlayerSamples": sorted(provider_players)[:5],
        "modelPlayerSamples": sorted(projected_players)[:5],
    }


def _provider_payload_diagnostics(
    target_season: int,
    week: int,
    season_type: str,
    model_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    games = nfl_data.get_week_games(target_season, week, season_type)
    details = [_provider_payload_diagnostic(game, model_rows) for game in games]
    snapshots = [item for item in details if item.get("snapshotAvailable")]
    with_bookmakers = [item for item in snapshots if int(item.get("bookmakers") or 0) > 0]
    with_usable_quotes = [item for item in snapshots if int(item.get("usableQuoteRows") or 0) > 0]
    with_matchable_quotes = [
        item for item in snapshots if int(item.get("matchablePlayerMarketPairs") or 0) > 0
    ]

    if not snapshots:
        classification = "no_cached_prop_snapshots"
    elif not with_bookmakers:
        classification = "provider_player_props_not_posted"
    elif not with_usable_quotes:
        classification = "provider_payload_not_supported_or_parseable"
    elif not with_matchable_quotes:
        classification = "provider_payload_does_not_overlap_model"
    else:
        classification = "matchable_provider_quotes_available"

    return {
        "classification": classification,
        "games": len(games),
        "snapshotsAvailable": len(snapshots),
        "gamesWithBookmakers": len(with_bookmakers),
        "gamesWithUsableQuotes": len(with_usable_quotes),
        "gamesWithMatchableQuotes": len(with_matchable_quotes),
        "details": details,
    }


def _select_verification_period(target_season: int) -> dict[str, Any]:
    """Choose the earliest current-or-future period represented by provider cache."""
    current = nfl_data.current_week(target_season)
    current_week = int(current["week"])
    current_type = str(current.get("season_type") or "REG")
    current_key = (current_type, current_week)
    schedule = nfl_data.get_schedule(target_season)

    periods: list[tuple[str, int]] = []
    for game in schedule:
        season_type = str(game.get("season_type") or "")
        week = game.get("week")
        if not season_type or not isinstance(week, int):
            continue
        key = (season_type, week)
        if key not in periods:
            periods.append(key)

    try:
        start_index = periods.index(current_key)
    except ValueError:
        start_index = 0

    cached_provider_events = len(odds_api.peek_game_odds())
    for season_type, week in periods[start_index:]:
        games = [
            game
            for game in schedule
            if game.get("season_type") == season_type and game.get("week") == week
        ]
        matched = sum(
            1
            for game in games
            if odds_api.find_event_for_game(game, cache_only=True) is not None
        )
        if matched:
            return {
                "week": week,
                "seasonType": season_type,
                "reason": (
                    "current_period_provider_match"
                    if (season_type, week) == current_key
                    else "provider_catalog_fallback"
                ),
                "currentWeek": current_week,
                "currentSeasonType": current_type,
                "providerMatchedGames": matched,
                "cachedProviderEvents": cached_provider_events,
            }

    return {
        "week": current_week,
        "seasonType": current_type,
        "reason": "no_provider_catalog_match",
        "currentWeek": current_week,
        "currentSeasonType": current_type,
        "providerMatchedGames": 0,
        "cachedProviderEvents": cached_provider_events,
    }


def readiness_snapshot(
    target_season: int = 2026,
    *,
    refresh_mode: str = "cache",
) -> dict[str, Any]:
    """Verify P3.6 using cache-only reads, with one explicit optional refresh."""
    started = time.monotonic()
    period = _select_verification_period(target_season)
    week = int(period["week"])
    season_type = str(period["seasonType"])
    refresh = str(refresh_mode).lower() == "one-event"
    refresh_result = None

    cached_rows, cached_errors, _ = _build_week_rows(
        target_season,
        week,
        season_type,
        include_odds=True,
        cache_only_odds=True,
    )
    payload_diagnostics = _provider_payload_diagnostics(
        target_season,
        week,
        season_type,
        cached_rows,
    )

    if refresh:
        target = _select_refresh_target(cached_rows)
        target_game_id = target.get("gameId")
        games = {
            str(game["game_id"]): game
            for game in nfl_data.get_week_games(target_season, week, season_type)
        }
        game = games.get(str(target_game_id or ""))
        if game is not None:
            refresh_result = {
                **target,
                **odds_api.refresh_game_props(game),
            }
        else:
            refresh_result = {
                **target,
                "ok": False,
                "reason": "no_refresh_target_game_available",
            }
        if cached_errors:
            refresh_result["modelErrors"] = cached_errors

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
    game_diagnostics = _price_game_diagnostics(rows)
    if refresh_result is not None:
        refreshed_game = str(refresh_result.get("gameId") or "")
        refresh_result["postRefreshPricing"] = next(
            (cell for cell in game_diagnostics if cell["gameId"] == refreshed_game),
            None,
        )
        refreshed_game_obj = next(
            (
                game
                for game in nfl_data.get_week_games(target_season, week, season_type)
                if str(game.get("game_id") or "") == refreshed_game
            ),
            None,
        )
        if refreshed_game_obj is not None:
            refresh_result["providerPayload"] = _provider_payload_diagnostic(
                refreshed_game_obj,
                rows,
            )
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
        "provider_period_match": int(period.get("providerMatchedGames") or 0) > 0,
        "decision_volume": len(rows) >= thresholds["minimumDecisionRows"],
        "priced_row_pool": len(priced) >= thresholds["minimumPricedRows"],
        "fresh_row_pool": len(fresh) >= thresholds["minimumFreshRows"],
        "fresh_book_coverage": len(fresh_books) >= thresholds["minimumFreshBooks"],
        "game_errors": errors <= thresholds["maximumGameErrors"],
        "bounded_build_time": build_seconds <= thresholds["maximumBuildSeconds"],
    }
    if refresh:
        payload_ok = bool(
            refresh_result
            and isinstance(refresh_result.get("providerPayload"), dict)
            and int(refresh_result["providerPayload"].get("usableQuoteRows") or 0) > 0
        )
        gates["explicit_refresh_succeeded"] = bool(refresh_result and refresh_result.get("ok"))
        gates["explicit_refresh_returned_usable_props"] = payload_ok
        gates["refresh_target_known_priced"] = bool(
            refresh_result and int(refresh_result.get("knownPricedGames") or 0) > 0
        )

    return {
        "phase": "P3.6",
        "mode": "one-event-refresh-then-cache-only" if refresh else "cache-only",
        "targetSeason": target_season,
        "week": week,
        "seasonType": season_type,
        "periodSelection": period,
        "games": game_count,
        "buildSeconds": build_seconds,
        "provider": provider,
        "providerPayloadDiagnostics": payload_diagnostics,
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
            "gameDiagnostics": game_diagnostics,
        },
        "thresholds": thresholds,
        "gates": gates,
        "ok": all(gates.values()),
    }
