"""
Game routes: weekly slate, single-game detail, P4.0 model decisions, P4.1
sportsbook actionability, P4.2 durable hydrated market board, P4.3 decision-first
delivery, P4.4 immutable game-publication receipts, P4.5 smart market freshness,
P4.6 bankroll-aware portfolio allocation, P4.7 explicit Tracker confirmation,
and market-relative boards.
"""
from __future__ import annotations

import statistics

from flask import Blueprint, jsonify, request

import nfl_data
import odds_api
import p40_game_intelligence
import p41_game_market_pricing
import p42_live_market_hydration
import p43_game_decision_delivery
import p44_game_decision_ledger
import p45_smart_market_refresh
import p46_game_portfolio
import p47_portfolio_tracker
import value_engine as ve
from security import json_body, limiter

games_bp = Blueprint("games", __name__)

EDGE_DISPLAY_CAP = 0.30


def _cap(x: float | None) -> float | None:
    if x is None:
        return None
    return round(max(-EDGE_DISPLAY_CAP, min(EDGE_DISPLAY_CAP, x)), 4)


def _consensus(fairs: list[float]) -> float | None:
    fairs = [f for f in fairs if isinstance(f, (int, float))]
    return round(statistics.median(fairs), 4) if fairs else None


def _two_way_board(rows: list[dict], a_key: str, b_key: str,
                   a_price: str, b_price: str) -> dict:
    books, fairs_a = [], []
    for r in rows:
        pa, pb = r.get(a_price), r.get(b_price)
        fair = ve.devig_two_way(pa, pb)
        fa = fair[0] if fair else None
        if fa is not None:
            fairs_a.append(fa)
        books.append({**r, "fair_" + a_key: round(fa, 4) if fa is not None else None})
    cons_a = _consensus(fairs_a)
    out = {"books": books, "consensus_fair": {a_key: cons_a,
                                              b_key: round(1 - cons_a, 4) if cons_a is not None else None}}
    for side, price_key, prob in ((a_key, a_price, cons_a),
                                  (b_key, b_price, 1 - cons_a if cons_a is not None else None)):
        priced = [r for r in rows if isinstance(r.get(price_key), (int, float))]
        if not priced or prob is None:
            out[f"best_{side}"] = None
            continue
        best = max(priced, key=lambda r: ve.american_to_decimal(r[price_key]) or 0)
        ev = ve.expected_value(prob, best[price_key])
        out[f"best_{side}"] = {"book": best["book"], "price": best[price_key],
                               "point": best.get(f"{side}_point", best.get("point")),
                               "ev": round(ev, 4) if ev is not None else None,
                               "edge": _cap(prob - (ve.american_to_implied(best[price_key]) or 0))}
    return out


def _majority_point(rows: list[dict], key: str):
    pts = [r.get(key) for r in rows if isinstance(r.get(key), (int, float))]
    if not pts:
        return None
    return statistics.mode(pts)


def game_lines(game: dict) -> dict:
    ev = odds_api.find_event_for_game(game)
    if not ev:
        return {"available": False}
    mk = odds_api.parse_game_markets(ev)
    out: dict = {"available": True, "odds_event_id": ev.get("id"),
                 "commence_time": ev.get("commence_time")}
    out["h2h"] = _two_way_board(mk["h2h"], "home", "away",
                                "home_price", "away_price")
    sp_point = _majority_point(mk["spreads"], "home_point")
    sp_rows = [r for r in mk["spreads"] if r.get("home_point") == sp_point]
    out["spreads"] = {"point": sp_point,
                      **_two_way_board(sp_rows, "home", "away",
                                       "home_price", "away_price")}
    tot_point = _majority_point(mk["totals"], "point")
    tot_rows = [r for r in mk["totals"] if r.get("point") == tot_point]
    out["totals"] = {"point": tot_point,
                     **_two_way_board(tot_rows, "over", "under",
                                      "over_price", "under_price")}
    return out


@games_bp.route("/api/games/current")
def api_current():
    cw = nfl_data.current_week()
    cw["stats_season"] = nfl_data.stats_season(cw["season"])
    cw["odds_configured"] = odds_api.is_configured()
    return jsonify(cw)


@games_bp.route("/api/games/week")
def api_week():
    cw = nfl_data.current_week()
    season = int(request.args.get("season", cw["season"]))
    week = int(request.args.get("week", cw["week"]))
    stype = request.args.get("type", cw["season_type"] if season == cw["season"] else "REG")
    live = request.args.get("live") == "1"
    games = nfl_data.get_week_games(season, week, stype, live=live)
    teams = nfl_data.team_summaries(nfl_data.stats_season(season))
    out = []
    for g in games:
        row = dict(g)
        row["home_summary"] = teams.get(g["home_team"])
        row["away_summary"] = teams.get(g["away_team"])
        row["lines"] = game_lines(g) if odds_api.is_configured() else {"available": False}
        out.append(row)
    return jsonify({"season": season, "week": week, "season_type": stype,
                    "games": out})


@games_bp.route("/api/game-decisions/week")
def api_game_decisions_week():
    cw = nfl_data.current_week()
    season = int(request.args.get("season", cw["season"]))
    week = int(request.args.get("week", cw["week"]))
    stype = str(request.args.get("type", cw["season_type"] if season == cw["season"] else "REG")).upper()
    if stype not in {"PRE", "REG", "POST"}:
        return jsonify({"error": "invalid season type"}), 400
    return jsonify(p40_game_intelligence.build_week_report(season, week, stype))


@games_bp.route("/api/game-market-decisions/week")
def api_game_market_decisions_week():
    cw = nfl_data.current_week()
    season = int(request.args.get("season", cw["season"]))
    week = int(request.args.get("week", cw["week"]))
    stype = str(request.args.get("type", cw["season_type"] if season == cw["season"] else "REG")).upper()
    pricing = str(request.args.get("pricing", "auto")).lower()
    if stype not in {"PRE", "REG", "POST"}:
        return jsonify({"error": "invalid season type"}), 400
    if pricing not in {"off", "cache", "auto", "live"}:
        return jsonify({"error": "invalid pricing mode"}), 400
    return jsonify(p41_game_market_pricing.build_week_market_report(
        season, week, stype, pricing_mode=pricing))


@games_bp.route("/api/game-market-board/week")
def api_game_market_board_week():
    cw = nfl_data.current_week()
    season = int(request.args.get("season", cw["season"]))
    week = int(request.args.get("week", cw["week"]))
    stype = str(request.args.get("type", cw["season_type"] if season == cw["season"] else "REG")).upper()
    if stype not in {"PRE", "REG", "POST"}:
        return jsonify({"error": "invalid season type"}), 400
    board = p42_live_market_hydration.build_cached_week_board(season, week, stype)
    delivery = p43_game_decision_delivery.build_delivery_from_board(board)
    publication = p44_game_decision_ledger.record_delivery(delivery.get("picks") or [])
    out = dict(board)
    out["publication"] = {
        "ledger": p44_game_decision_ledger.MODEL_NAME,
        "candidates": publication["candidates"],
        "inserted": publication["inserted"],
        "existing": publication["existing"],
        "failed": publication["failed"],
    }
    return jsonify(out)


@games_bp.route("/api/game-decision-board/week")
def api_game_decision_board_week():
    cw = nfl_data.current_week()
    season = int(request.args.get("season", cw["season"]))
    week = int(request.args.get("week", cw["week"]))
    stype = str(request.args.get("type", cw["season_type"] if season == cw["season"] else "REG")).upper()
    if stype not in {"PRE", "REG", "POST"}:
        return jsonify({"error": "invalid season type"}), 400
    return jsonify(p44_game_decision_ledger.publish_week_delivery(season, week, stype))


@games_bp.route("/api/game-opportunities/week")
def api_game_opportunities_week():
    """P4.5 cache-only continuity board; never upgrades P4.1 actionability."""
    cw = nfl_data.current_week()
    season = int(request.args.get("season", cw["season"]))
    week = int(request.args.get("week", cw["week"]))
    stype = str(request.args.get("type", cw["season_type"] if season == cw["season"] else "REG")).upper()
    if stype not in {"PRE", "REG", "POST"}:
        return jsonify({"error": "invalid season type"}), 400
    return jsonify(p45_smart_market_refresh.build_week_opportunities(season, week, stype))


@games_bp.route("/api/game-portfolio/week")
def api_game_portfolio_week():
    """P4.6 cache-only bankroll portfolio; advisory stakes only."""
    cw = nfl_data.current_week()
    season = int(request.args.get("season", cw["season"]))
    week = int(request.args.get("week", cw["week"]))
    stype = str(request.args.get("type", cw["season_type"] if season == cw["season"] else "REG")).upper()
    if stype not in {"PRE", "REG", "POST"}:
        return jsonify({"error": "invalid season type"}), 400
    return jsonify(p46_game_portfolio.build_week_portfolio(season, week, stype))


@games_bp.route("/api/game-portfolio/tracking/week")
def api_game_portfolio_tracking_week():
    """P4.7 read-only view of which current P4.6 allocations are in Tracker."""
    cw = nfl_data.current_week()
    season = int(request.args.get("season", cw["season"]))
    week = int(request.args.get("week", cw["week"]))
    stype = str(request.args.get("type", cw["season_type"] if season == cw["season"] else "REG")).upper()
    if stype not in {"PRE", "REG", "POST"}:
        return jsonify({"error": "invalid season type"}), 400
    return jsonify(p47_portfolio_tracker.build_week_tracking_status(season, week, stype))


@games_bp.route("/api/game-portfolio/track", methods=["POST"])
@limiter.limit(20, 60, key="user")
def api_game_portfolio_track():
    """Persist only explicitly confirmed current P4.6 allocations to Tracker."""
    payload = json_body(allowed={"season", "week", "type", "confirmed", "selectionKeys"})
    cw = nfl_data.current_week()
    try:
        season = int(payload.get("season", cw["season"]))
        week = int(payload.get("week", cw["week"]))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid season or week"}), 400
    stype = str(payload.get("type", cw["season_type"] if season == cw["season"] else "REG")).upper()
    if stype not in {"PRE", "REG", "POST"}:
        return jsonify({"error": "invalid season type"}), 400
    confirmed = payload.get("confirmed") is True
    selection_keys = payload.get("selectionKeys")
    if selection_keys is not None:
        if not isinstance(selection_keys, list) or len(selection_keys) > 20 or not all(isinstance(key, str) and len(key) <= 200 for key in selection_keys):
            return jsonify({"error": "invalid selectionKeys"}), 400
    result = p47_portfolio_tracker.confirm_week_portfolio(
        season,
        week,
        stype,
        confirmed=confirmed,
        selection_keys=selection_keys,
        persist=True,
    )
    if not result.get("ok"):
        status = 409 if result.get("error") == "explicit_confirmation_required" else 400
        return jsonify(result), status
    return jsonify(result)


@games_bp.route("/api/game-market-refresh/status")
def api_game_market_refresh_status():
    """P4.5 scheduler lease status; performs zero provider requests."""
    season = request.args.get("season")
    return jsonify(p45_smart_market_refresh.refresh_status(
        int(season) if season not in (None, "") else None))


@games_bp.route("/api/game-market-hydration/status")
def api_game_market_hydration_status():
    cw = nfl_data.current_week()
    season = int(request.args.get("season", cw["season"]))
    week = int(request.args.get("week", cw["week"]))
    stype = str(request.args.get("type", cw["season_type"] if season == cw["season"] else "REG")).upper()
    if stype not in {"PRE", "REG", "POST"}:
        return jsonify({"error": "invalid season type"}), 400
    return jsonify(p42_live_market_hydration.cache_status(season, week, stype))


@games_bp.route("/api/game/<game_id>")
def api_game(game_id):
    cw = nfl_data.current_week()
    season = int(request.args.get("season", cw["season"]))
    game = next((g for g in nfl_data.get_schedule(season)
                 if g["game_id"] == game_id), None)
    if not game:
        return jsonify({"error": "game not found", "season": season}), 404
    ss = nfl_data.stats_season(season)
    teams = nfl_data.team_summaries(ss)
    dvp = nfl_data.defense_vs_position(ss)
    return jsonify({
        "game": game,
        "stats_season": ss,
        "home_summary": teams.get(game["home_team"]),
        "away_summary": teams.get(game["away_team"]),
        "home_dvp": dvp.get(game["home_team"]),
        "away_dvp": dvp.get(game["away_team"]),
        "lines": game_lines(game),
    })


@games_bp.route("/api/odds/status")
def api_odds_status():
    return jsonify(odds_api.snapshot_status())
