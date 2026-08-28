"""
Props routes: per-game player intelligence, weekly board, and P3.5 Quick Props delivery.

P3.4 owns the decision contract. P3.5 guarantees that product surfaces consume
that contract deterministically: Lean-or-better model picks are delivered first,
Pass rows are never silently promoted, and sportsbook price remains a separate
actionability layer.
"""
from __future__ import annotations

import threading
import time

from flask import Blueprint, jsonify, request

import decision_delivery as dd
import decision_intelligence as di
import nfl_data
import odds_api
import player_intelligence as pi
import projection_data as pd
import projections as pj
import value_engine as ve

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


def _cap(x):
    if x is None:
        return None
    return round(max(-EDGE_DISPLAY_CAP, min(EDGE_DISPLAY_CAP, x)), 4)


def _best_price(rows: list[dict], side: str):
    priced = [r for r in rows if r["side"] == side and isinstance(r.get("price"), (int, float))]
    if not priced:
        return None
    best = max(priced, key=lambda r: ve.american_to_decimal(r["price"]) or 0)
    return {"book": best["book"], "price": best["price"]}


def _build_game_rows(game: dict, season: int, *, include_odds: bool = True) -> list[dict]:
    """All P3.4 player-market decisions for one game.

    ``include_odds=False`` is used by protected read-only verification so the
    model/delivery contract can be checked without consuming provider credits.
    The default two-argument call remains backward compatible.
    """
    ss = pd.stats_season(season)
    logs = pd.player_game_logs(ss)
    idx = pd.player_index(season, ss)
    dvp = pd.defense_vs_position(ss)

    odds_rows: dict[tuple, list[dict]] = {}
    event = None
    if include_odds and odds_api.is_configured():
        event = odds_api.find_event_for_game(game)
    if event:
        for row in odds_api.parse_prop_markets(odds_api.get_event_props(event["id"])):
            market = pj.ODDS_KEY_TO_MARKET.get(row["base_key"])
            if market and isinstance(row.get("line"), (int, float)):
                odds_rows.setdefault((_norm_name(row["player"]), market, row["line"]), []).append(row)

    home, away = game["home_team"], game["away_team"]
    rows: list[dict] = []
    for player_id, meta in idx.items():
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
                line, books = max(booked, key=lambda item: len({book["book"] for book in item[1]}))
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
            best_over, best_under = _best_price(books, "over"), _best_price(books, "under")
            fair = None
            if best_over and best_under:
                fair = ve.fair_prob(best_over["price"], best_under["price"])
            side = "over" if p_over >= 0.5 else "under"
            p_side = p_over if side == "over" else 1 - p_over
            best_side = best_over if side == "over" else best_under
            edge = ev_pct = kelly = implied = None
            if best_side:
                implied = ve.american_to_implied(best_side["price"])
                edge = _cap(p_side - implied)
                expected = ve.expected_value(p_side, best_side["price"])
                ev_pct = round(expected, 4) if expected is not None else None
                kelly = ve.kelly_stake(p_side, best_side["price"])["stake_pct"]

            p33_rank_score = pi.ranking_score(intelligence, edge=edge, ev=ev_pct)
            decision = di.build_prop_decision(
                intelligence,
                side=side,
                line=float(line),
                price=best_side["price"] if best_side else None,
                edge=edge,
                ev=ev_pct,
                simulations=1200,
                seed=di.stable_seed(game["game_id"], player_id, market, line),
            )
            confidence = intelligence["confidence"]
            matchup = intelligence["matchup"]
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
                    "priceStatus": decision["priceStatus"],
                    "actionable": decision["actionable"],
                    "recommendedAction": decision["recommendedAction"],
                    "decisionReasons": decision["decisionReasons"],
                    "decisionRisks": decision["decisionRisks"],
                    "bestOver": best_over,
                    "bestUnder": best_under,
                    "fairProb": round(fair, 4) if fair is not None else None,
                    "impliedProb": round(implied, 4) if implied is not None else None,
                    "edge": edge,
                    "evPct": ev_pct,
                    "kellyPct": kelly,
                    "grade": ve.edge_grade(edge),
                    "bookCount": len({book["book"] for book in books}),
                    "noOdds": no_odds,
                    "modelSource": decision["modelVersion"],
                    "evidenceSeason": ss,
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
) -> tuple[list[dict], int, int]:
    games = nfl_data.get_week_games(season, week, season_type)
    rows: list[dict] = []
    errors = 0
    for game in games:
        try:
            # Preserve the historical two-argument call path for normal product
            # traffic and wrappers/tests. Only the read-only verification path
            # needs to opt out of provider pricing explicitly.
            if include_odds:
                game_rows = _build_game_rows(game, season)
            else:
                game_rows = _build_game_rows(game, season, include_odds=False)
            rows.extend(game_rows)
        except Exception:  # noqa: BLE001 - delivery reports partial/degraded state explicitly
            errors += 1
    return dd.sort_decisions(rows), errors, len(games)


@props_bp.route("/api/props/game/<game_id>")
def api_props_game(game_id):
    cw = nfl_data.current_week()
    season = int(request.args.get("season", cw["season"]))
    key = ("game", game_id, season, "p3.5")
    hit = _cache_get(key)
    if hit and request.args.get("refresh") != "1":
        return jsonify(hit)
    game = next(
        (candidate for candidate in nfl_data.get_schedule(season) if candidate["game_id"] == game_id),
        None,
    )
    if not game:
        return jsonify({"error": "game not found"}), 404
    rows = _build_game_rows(game, season)
    out = {
        "game": game,
        "stats_season": pd.stats_season(season),
        "model_version": "p3.5-decision-delivery",
        "delivery": dd.build_delivery(rows, limit=8, expected_games=1),
        "rows": rows,
    }
    _cache_set(key, out)
    return jsonify(out)


@props_bp.route("/api/props/board")
def api_props_board():
    cw = nfl_data.current_week()
    season = int(request.args.get("season", cw["season"]))
    week = int(request.args.get("week", cw["week"]))
    season_type = request.args.get(
        "type", cw["season_type"] if season == cw["season"] else "REG"
    )
    key = ("board", season, week, season_type, "p3.5")
    hit = _cache_get(key)
    if hit and request.args.get("refresh") != "1":
        return jsonify(hit)
    rows, errors, game_count = _build_week_rows(season, week, season_type)
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
        "model_version": "p3.5-decision-delivery",
        "ranking": "decision grade, decision score, then available price value",
    }
    _cache_set(key, out)
    return jsonify(out)


@props_bp.route("/api/quick-props/week")
def api_quick_props_week():
    """Terminal Quick Props contract for Dashboard/My Hub consumers."""
    cw = nfl_data.current_week()
    season = int(request.args.get("season", cw["season"]))
    week = int(request.args.get("week", cw["week"]))
    season_type = request.args.get(
        "type", cw["season_type"] if season == cw["season"] else "REG"
    )
    limit = int(request.args.get("limit", "8"))
    include_odds = request.args.get("pricing", "auto").lower() != "off"
    cache_key = ("quick", season, week, season_type, limit, include_odds, "p3.5")
    hit = _cache_get(cache_key)
    if hit and request.args.get("refresh") != "1":
        return jsonify(hit)
    rows, errors, game_count = _build_week_rows(
        season,
        week,
        season_type,
        include_odds=include_odds,
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
        "pricing": "enabled" if include_odds else "disabled",
        **delivery,
    }
    _cache_set(cache_key, out, ttl=180)
    return jsonify(out)


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
    """Quant feed: positive-EV priced rows with P3.5 delivery metadata."""
    min_ev = float(request.args.get("minEv", "0.03"))
    response = api_props_board()
    data = response.get_json()
    rows = [
        row for row in data["rows"] if row.get("evPct") is not None and row["evPct"] >= min_ev
    ]
    return jsonify({**data, "rows": rows, "minEv": min_ev})
