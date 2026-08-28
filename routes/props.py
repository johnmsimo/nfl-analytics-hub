"""
Props routes: player intelligence, P3.5 decision delivery, and P3.6 market actionability.

P3.4 owns model decisions. P3.5 owns deterministic delivery. P3.6 owns the
sportsbook layer: quote provenance, freshness, best price, de-vig market
probability, edge/EV, and the final rule that only a fresh positive-value quote
may make a Strong Play/Play actionable.
"""
from __future__ import annotations

import threading
import time

from flask import Blueprint, jsonify, request

import decision_delivery as dd
import decision_intelligence as di
import market_pricing as mp
import nfl_data
import odds_api
import player_intelligence as pi
import projection_data as pd
import projections as pj
import value_engine as ve
from security import json_body, require_roles

_norm_name = odds_api.norm_player_name

props_bp = Blueprint("props", __name__)

EDGE_DISPLAY_CAP = 0.30
_RESP_CACHE: dict = {}
_RESP_TTL = 600
_cache_lock = threading.Lock()


def _cache_get(key):
    with _cache_lock:
        hit = _RESP_CACHE.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
    return None


def _cache_set(key, val, ttl=_RESP_TTL):
    with _cache_lock:
        _RESP_CACHE[key] = (time.time() + ttl, val)


def _cache_clear() -> None:
    with _cache_lock:
        _RESP_CACHE.clear()


def _cap(value):
    if value is None:
        return None
    return round(max(-EDGE_DISPLAY_CAP, min(EDGE_DISPLAY_CAP, float(value))), 4)


def _load_prop_quotes(
    game: dict,
    *,
    include_odds: bool,
    cache_only_odds: bool,
) -> tuple[dict[tuple, list[dict]], dict | None]:
    odds_rows: dict[tuple, list[dict]] = {}
    if not include_odds:
        return odds_rows, None

    if cache_only_odds:
        event = odds_api.find_event_for_game(game, cache_only=True)
    elif odds_api.is_configured():
        event = odds_api.find_event_for_game(game)
    else:
        event = None
    if not event:
        return odds_rows, None

    event_id = str(event["id"])
    event_odds = odds_api.peek_event_props(event_id) if cache_only_odds else odds_api.get_event_props(event_id)
    snapshot = odds_api.event_props_snapshot(event_id)
    for row in odds_api.parse_prop_markets(event_odds, fetched_at=snapshot.get("fetched_at")):
        market = pj.ODDS_KEY_TO_MARKET.get(row["base_key"])
        if market and isinstance(row.get("line"), (int, float)):
            odds_rows.setdefault((_norm_name(row["player"]), market, row["line"]), []).append(row)
    return odds_rows, snapshot


def _build_game_rows(
    game: dict,
    season: int,
    *,
    include_odds: bool = True,
    cache_only_odds: bool = False,
) -> list[dict]:
    """All player-market decisions for one game.

    The historical two-argument call remains valid. Protected verification may
    set ``cache_only_odds=True`` to inspect persisted prices without provider
    calls or credit spend.
    """
    stats_season = pd.stats_season(season)
    logs = pd.player_game_logs(stats_season)
    index = pd.player_index(season, stats_season)
    dvp = pd.defense_vs_position(stats_season)
    odds_rows, odds_snapshot = _load_prop_quotes(
        game,
        include_odds=include_odds,
        cache_only_odds=cache_only_odds,
    )

    home, away = game["home_team"], game["away_team"]
    rows: list[dict] = []
    for player_id, meta in index.items():
        team = meta["team"]
        if team not in (home, away) or meta["games"] < 3:
            continue
        position = meta["position"]
        markets = pj.relevant_markets(position)
        if not markets:
            continue
        history = logs.get(player_id, [])
        if len(history) < 3:
            continue
        opponent = away if team == home else home
        normalized_name = _norm_name(meta["name"])
        for market in markets:
            preview = pi.analyze_projection(
                history,
                market,
                opponent=opponent,
                dvp=dvp,
                position=position,
                roster_verified=bool(meta.get("rosterVerified")),
            )
            if not preview or preview["mean"] < pj.MIN_MEAN[market]:
                continue

            booked = [
                (line, books)
                for (name, offered_market, line), books in odds_rows.items()
                if name == normalized_name and offered_market == market
            ]
            if booked:
                line, books = max(
                    booked,
                    key=lambda item: (
                        len({book.get("book_key") or book.get("book") for book in item[1]}),
                        -abs(float(item[0]) - float(preview["mean"])),
                    ),
                )
                no_odds = False
            elif market == "anytime_td":
                line, books, no_odds = 0.5, [], True
            else:
                line, books, no_odds = int(preview["mean"]) + 0.5, [], True

            intelligence = pi.analyze_projection(
                history,
                market,
                opponent=opponent,
                dvp=dvp,
                position=position,
                line=float(line),
                roster_verified=bool(meta.get("rosterVerified")),
            )
            if not intelligence or intelligence.get("probOver") is None:
                continue

            p_over = float(intelligence["probOver"])
            side = "over" if p_over >= 0.5 else "under"
            p_side = p_over if side == "over" else 1.0 - p_over
            over_pricing = mp.assess_market(books, side="over", model_probability=p_over)
            under_pricing = mp.assess_market(books, side="under", model_probability=1.0 - p_over)
            pricing = over_pricing if side == "over" else under_pricing
            best_over = over_pricing.get("bestPrice")
            best_under = under_pricing.get("bestPrice")
            best_side = pricing.get("bestPrice")
            fresh_price = (
                best_side.get("price")
                if best_side and pricing.get("quoteStatus") == "fresh"
                else None
            )
            edge = _cap(pricing.get("edge")) if pricing.get("quoteStatus") == "fresh" else None
            ev_pct = pricing.get("evPct") if pricing.get("quoteStatus") == "fresh" else None
            kelly = pricing.get("kellyPct") if pricing.get("quoteStatus") == "fresh" else None

            p33_rank_score = pi.ranking_score(intelligence, edge=edge, ev=ev_pct)
            decision = di.build_prop_decision(
                intelligence,
                side=side,
                line=float(line),
                price=fresh_price,
                edge=edge,
                ev=ev_pct,
                simulations=1200,
                seed=di.stable_seed(game["game_id"], player_id, market, line),
            )
            confidence = intelligence["confidence"]
            matchup = intelligence["matchup"]
            actionable = mp.apply_model_actionability(decision["decisionGrade"], pricing)
            decision_risks = list(decision["decisionRisks"])
            if pricing.get("quoteStatus") == "stale":
                decision_risks = [risk for risk in decision_risks if risk != "unpriced_market"]
                decision_risks.append("stale_market_quote")
            if decision["decisionGrade"] in {"Strong Play", "Play"} and pricing.get("quoteStatus") == "stale":
                recommended_action = (
                    f"Model pick: {side.upper()}; the available quote is stale. Refresh market prices before betting."
                )
            elif actionable:
                recommended_action = (
                    f"Actionable {side.upper()}: fresh verified price clears the P3.6 edge and EV gates."
                )
            else:
                recommended_action = decision["recommendedAction"]

            rows.append(
                {
                    "gameId": game["game_id"],
                    "season": season,
                    "week": game["week"],
                    "gameday": (game.get("date") or "")[:10],
                    "player": meta["name"],
                    "playerId": player_id,
                    "team": team,
                    "opponent": opponent,
                    "position": position,
                    "marketKey": market,
                    "marketLabel": pj.MARKET_LABELS[market],
                    "line": line,
                    "modelMean": intelligence["mean"],
                    "seasonMean": intelligence["season_mean"],
                    "l4Mean": intelligence["l4_mean"],
                    "trendPct": intelligence["trendPct"],
                    "oppFactor": intelligence["opp_factor"],
                    "matchupGrade": matchup["grade"],
                    "matchupDataGames": matchup["dataGames"],
                    "matchupDataQuality": matchup["dataQuality"],
                    "games": intelligence["n"],
                    "rawProbOver": intelligence["rawProbOver"],
                    "probOver": p_over,
                    "side": side,
                    "modelProb": round(p_side, 4),
                    "confidenceScore": confidence["score"],
                    "confidenceGrade": confidence["grade"],
                    "confidenceComponents": confidence,
                    "projectionRange": intelligence["interval"],
                    "riskFlags": intelligence["riskFlags"],
                    "signalStrength": intelligence["signalStrength"],
                    "modelRankScore": p33_rank_score,
                    "rankScore": decision["decisionScore"],
                    "decisionGrade": decision["decisionGrade"],
                    "decisionScore": decision["decisionScore"],
                    "consensusProb": decision["consensusProbability"],
                    "simulationProb": decision["simulationProbability"],
                    "simulationAgreement": decision["simulationAgreement"],
                    "simulation": decision["simulation"],
                    "priceStatus": pricing["priceStatus"],
                    "quoteStatus": pricing["quoteStatus"],
                    "actionable": actionable,
                    "recommendedAction": recommended_action,
                    "decisionReasons": decision["decisionReasons"],
                    "decisionRisks": decision_risks[:5],
                    "bestOver": best_over,
                    "bestUnder": best_under,
                    "bestPrice": best_side,
                    "fairProb": over_pricing.get("fairMarketProbability"),
                    "fairMarketProb": pricing.get("fairMarketProbability"),
                    "impliedProb": pricing.get("impliedProbability"),
                    "referenceProb": pricing.get("referenceProbability"),
                    "edge": edge,
                    "evPct": ev_pct,
                    "kellyPct": kelly,
                    "grade": ve.edge_grade(edge),
                    "bookCount": pricing.get("quotedBookCount", 0),
                    "freshBookCount": pricing.get("freshBookCount", 0),
                    "pairedFairBookCount": pricing.get("pairedFairBookCount", 0),
                    "marketPricing": pricing,
                    "oddsSnapshotAgeSeconds": (odds_snapshot or {}).get("age_seconds"),
                    "noOdds": no_odds,
                    "modelSource": "p3.6-live-market-actionability",
                    "decisionModelVersion": decision["modelVersion"],
                    "evidenceSeason": stats_season,
                    "rosterVerified": bool(meta.get("rosterVerified")),
                }
            )
    return dd.sort_decisions(rows)


def _build_week_rows(
    season: int,
    week: int,
    season_type: str,
    *,
    include_odds: bool = True,
    cache_only_odds: bool = False,
) -> tuple[list[dict], int, int]:
    games = nfl_data.get_week_games(season, week, season_type)
    rows: list[dict] = []
    errors = 0
    for game in games:
        try:
            # Preserve the historical two-argument call path for normal product
            # traffic and wrappers/tests.
            if include_odds and not cache_only_odds:
                game_rows = _build_game_rows(game, season)
            else:
                game_rows = _build_game_rows(
                    game,
                    season,
                    include_odds=include_odds,
                    cache_only_odds=cache_only_odds,
                )
            rows.extend(game_rows)
        except Exception:  # noqa: BLE001 - delivery reports partial/degraded state explicitly
            errors += 1
    return dd.sort_decisions(rows), errors, len(games)


@props_bp.route("/api/props/game/<game_id>")
def api_props_game(game_id):
    current_week = nfl_data.current_week()
    season = int(request.args.get("season", current_week["season"]))
    pricing_mode = request.args.get("pricing", "auto").lower()
    include_odds = pricing_mode != "off"
    cache_only = pricing_mode == "cache"
    key = ("game", game_id, season, pricing_mode, "p3.6")
    hit = _cache_get(key)
    if hit and request.args.get("refresh") != "1":
        return jsonify(hit)
    game = next(
        (candidate for candidate in nfl_data.get_schedule(season) if candidate["game_id"] == game_id),
        None,
    )
    if not game:
        return jsonify({"error": "game not found"}), 404
    rows = _build_game_rows(
        game,
        season,
        include_odds=include_odds,
        cache_only_odds=cache_only,
    )
    out = {
        "game": game,
        "stats_season": pd.stats_season(season),
        "model_version": "p3.6-live-market-actionability",
        "pricing": pricing_mode,
        "delivery": dd.build_delivery(rows, limit=8, expected_games=1),
        "rows": rows,
    }
    _cache_set(key, out)
    return jsonify(out)


@props_bp.route("/api/props/board")
def api_props_board():
    current_week = nfl_data.current_week()
    season = int(request.args.get("season", current_week["season"]))
    week = int(request.args.get("week", current_week["week"]))
    season_type = request.args.get(
        "type", current_week["season_type"] if season == current_week["season"] else "REG"
    )
    pricing_mode = request.args.get("pricing", "auto").lower()
    include_odds = pricing_mode != "off"
    cache_only = pricing_mode == "cache"
    key = ("board", season, week, season_type, pricing_mode, "p3.6")
    hit = _cache_get(key)
    if hit and request.args.get("refresh") != "1":
        return jsonify(hit)
    rows, errors, game_count = _build_week_rows(
        season,
        week,
        season_type,
        include_odds=include_odds,
        cache_only_odds=cache_only,
    )
    out = {
        "season": season,
        "week": week,
        "season_type": season_type,
        "games": game_count,
        "rows": rows,
        "decision_summary": di.summarize_decisions(rows),
        "delivery": dd.build_delivery(
            rows,
            limit=12,
            game_errors=errors,
            expected_games=game_count,
        ),
        "stats_season": pd.stats_season(season),
        "odds_configured": odds_api.is_configured(),
        "pricing": pricing_mode,
        "market_snapshot": odds_api.snapshot_status(),
        "model_version": "p3.6-live-market-actionability",
        "ranking": "decision grade, decision score, then fresh positive-value price",
    }
    _cache_set(key, out)
    return jsonify(out)


@props_bp.route("/api/quick-props/week")
def api_quick_props_week():
    """Terminal Quick Props contract with explicit P3.6 pricing mode."""
    current_week = nfl_data.current_week()
    season = int(request.args.get("season", current_week["season"]))
    week = int(request.args.get("week", current_week["week"]))
    season_type = request.args.get(
        "type", current_week["season_type"] if season == current_week["season"] else "REG"
    )
    limit = int(request.args.get("limit", "8"))
    pricing_mode = request.args.get("pricing", "auto").lower()
    if pricing_mode not in {"auto", "cache", "off"}:
        return jsonify({"error": "pricing must be auto, cache, or off", "code": "INVALID_PRICING_MODE"}), 400
    include_odds = pricing_mode != "off"
    cache_only = pricing_mode == "cache"
    cache_key = ("quick", season, week, season_type, limit, pricing_mode, "p3.6")
    hit = _cache_get(cache_key)
    if hit and request.args.get("refresh") != "1":
        return jsonify(hit)
    rows, errors, game_count = _build_week_rows(
        season,
        week,
        season_type,
        include_odds=include_odds,
        cache_only_odds=cache_only,
    )
    delivery = dd.build_delivery(
        rows,
        limit=limit,
        game_errors=errors,
        expected_games=game_count,
    )
    out = {
        "season": season,
        "week": week,
        "season_type": season_type,
        "games": game_count,
        "stats_season": pd.stats_season(season),
        "pricing": pricing_mode,
        "market_snapshot": odds_api.snapshot_status(),
        **delivery,
    }
    _cache_set(cache_key, out, ttl=180)
    return jsonify(out)


@props_bp.route("/api/market-pricing/status")
def api_market_pricing_status():
    """Sanitized current market-cache and actionability policy status."""
    return jsonify(
        {
            "modelVersion": "p3.6-live-market-actionability",
            "provider": odds_api.snapshot_status(),
            "policy": {
                "maximumQuoteAgeSeconds": mp.ACTIONABLE_MAX_AGE_SECONDS,
                "maximumDisplayAgeSeconds": mp.DISPLAY_MAX_AGE_SECONDS,
                "minimumEdge": mp.MIN_EDGE,
                "minimumEv": mp.MIN_EV,
                "rule": "Strong Play/Play + fresh quote + positive edge/EV",
            },
        }
    )


@props_bp.route("/api/market-pricing/refresh", methods=["POST"])
@require_roles("admin", "owner")
def api_market_pricing_refresh():
    """Explicit credit-spending refresh for top model-pick games only."""
    payload = json_body(
        allowed={"season", "week", "type", "maxGames"},
    )
    current_week = nfl_data.current_week()
    season = int(payload.get("season", current_week["season"]))
    week = int(payload.get("week", current_week["week"]))
    season_type = str(payload.get("type") or current_week.get("season_type") or "REG")
    max_games = max(1, min(int(payload.get("maxGames", 2)), 4))
    if not odds_api.is_configured():
        return jsonify({"error": "Odds API runtime is not configured", "code": "ODDS_NOT_CONFIGURED"}), 503

    model_rows, errors, game_count = _build_week_rows(
        season,
        week,
        season_type,
        include_odds=False,
    )
    model_delivery = dd.build_delivery(
        model_rows,
        limit=25,
        game_errors=errors,
        expected_games=game_count,
    )
    targets: list[str] = []
    for row in model_delivery.get("picks") or []:
        game_id = str(row.get("gameId") or "")
        if game_id and game_id not in targets:
            targets.append(game_id)
        if len(targets) >= max_games:
            break
    games = {str(game["game_id"]): game for game in nfl_data.get_week_games(season, week, season_type)}
    refreshed = []
    for game_id in targets:
        game = games.get(game_id)
        if game:
            refreshed.append({"gameId": game_id, **odds_api.refresh_game_props(game)})
    _cache_clear()
    return jsonify(
        {
            "ok": any(item.get("ok") for item in refreshed),
            "season": season,
            "week": week,
            "seasonType": season_type,
            "targetGames": targets,
            "refreshed": refreshed,
            "provider": odds_api.snapshot_status(),
            "note": "This endpoint performs explicit provider refreshes and may consume Odds API credits.",
        }
    )


@props_bp.route("/api/decisions/week")
def api_decisions_week():
    """Canonical pick feed: model Lean-or-better rows, priced or explicitly unpriced."""
    response = api_props_board()
    data = response.get_json()
    grades = {"Strong Play", "Play", "Lean"}
    rows = [row for row in data["rows"] if row.get("decisionGrade") in grades]
    return jsonify({**data, "rows": rows, "decision_filter": "Lean or better"})


@props_bp.route("/api/edges/week")
def api_edges_week():
    """Fresh positive-value priced feed with P3.6 actionability metadata."""
    min_ev = float(request.args.get("minEv", "0.03"))
    response = api_props_board()
    data = response.get_json()
    rows = [
        row
        for row in data["rows"]
        if row.get("quoteStatus") == "fresh"
        and row.get("priceStatus") == "positive_value"
        and row.get("evPct") is not None
        and row["evPct"] >= min_ev
    ]
    return jsonify({**data, "rows": rows, "minEv": min_ev})
