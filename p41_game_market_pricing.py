"""P4.1 game-market pricing, shopping, and actionability.

P4.0 owns independent game probabilities. P4.1 joins those probabilities to
sportsbook moneyline, spread, and total quotes without allowing the market to
rewrite the model. Freshness, same-book de-vig, best-price shopping, edge, EV,
and Kelly are evaluated independently for each market.

The default/verification paths can use persisted game-odds snapshots with zero
provider spend. Explicit ``live`` mode is the only P4.1 path that force-refreshes
The Odds API. P5.4 may apply an explicitly owner-promoted market-specific
calibration champion to spread/total selected-side probabilities; the selected
side and all P4.1 actionability thresholds remain unchanged.
"""
from __future__ import annotations

import math
import statistics
import time
from collections import Counter
from typing import Any, Iterable

import market_pricing as mp
import nfl_data
import odds_api
import p40_game_intelligence as p40
import value_engine as ve

MODEL_NAME = "p4.1-game-market-actionability"
MODEL_VERSION = "p41-pricing-v1"
TOTAL_SD = 10.5


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _game_snapshot_fetched_at(status: dict[str, Any]) -> float | None:
    age = status.get("game_snapshot_age_seconds")
    if not isinstance(age, (int, float)):
        return None
    return max(0.0, time.time() - float(age))


def _quote_view(row: dict[str, Any]) -> dict[str, Any]:
    observed = mp.quote_timestamp(row)
    provider_updated = mp.provider_update_timestamp(row)
    age = mp.quote_age_seconds(row)
    freshness = mp.quote_freshness(row)
    expires_in = None
    expires_at = None
    if observed is not None:
        expires_in = round(mp.ACTIONABLE_MAX_AGE_SECONDS - float(age or 0.0), 1)
        expires_at = observed.timestamp() + mp.ACTIONABLE_MAX_AGE_SECONDS
    return {
        "book": row.get("book"),
        "bookKey": row.get("book_key"),
        "price": row.get("price"),
        "line": row.get("line"),
        "marketPoint": row.get("market_point"),
        "quoteAt": observed.isoformat() if observed else None,
        "quoteAgeSeconds": age,
        "quoteFreshness": freshness,
        "providerUpdatedAt": provider_updated.isoformat() if provider_updated else None,
        "expiresAtEpoch": round(expires_at, 3) if expires_at is not None else None,
        "expiresInSeconds": expires_in,
    }


def _flatten_event(event: dict[str, Any], fetched_at: float | None) -> dict[str, list[dict[str, Any]]]:
    """Normalize featured game markets into timestamped two-way quote rows."""
    home = event.get("home_team")
    away = event.get("away_team")
    out: dict[str, list[dict[str, Any]]] = {"moneyline": [], "spread": [], "total": []}
    for bookmaker in event.get("bookmakers", []):
        book = bookmaker.get("title") or bookmaker.get("key")
        book_key = bookmaker.get("key") or book
        book_last_update = bookmaker.get("last_update")
        for market in bookmaker.get("markets", []):
            key = market.get("key")
            market_last_update = market.get("last_update")
            outcomes = {row.get("name"): row for row in market.get("outcomes", [])}
            common = {
                "book": book,
                "book_key": book_key,
                "book_last_update": book_last_update,
                "market_last_update": market_last_update,
                "fetched_at": fetched_at,
            }
            if key == "h2h" and home in outcomes and away in outcomes:
                out["moneyline"].extend(
                    [
                        {**common, "side": "home", "price": outcomes[home].get("price"), "line": None, "market_point": None},
                        {**common, "side": "away", "price": outcomes[away].get("price"), "line": None, "market_point": None},
                    ]
                )
            elif key == "spreads" and home in outcomes and away in outcomes:
                home_point = outcomes[home].get("point")
                away_point = outcomes[away].get("point")
                if isinstance(home_point, (int, float)) and isinstance(away_point, (int, float)):
                    canonical = float(home_point)
                    out["spread"].extend(
                        [
                            {**common, "side": "home", "price": outcomes[home].get("price"), "line": float(home_point), "market_point": canonical},
                            {**common, "side": "away", "price": outcomes[away].get("price"), "line": float(away_point), "market_point": canonical},
                        ]
                    )
            elif key == "totals":
                over = outcomes.get("Over")
                under = outcomes.get("Under")
                if over and under and isinstance(over.get("point"), (int, float)):
                    point = float(over["point"])
                    out["total"].extend(
                        [
                            {**common, "side": "over", "price": over.get("price"), "line": point, "market_point": point},
                            {**common, "side": "under", "price": under.get("price"), "line": point, "market_point": point},
                        ]
                    )
    return out


def _canonical_point(rows: list[dict[str, Any]]) -> float | None:
    points = [float(row["market_point"]) for row in rows if isinstance(row.get("market_point"), (int, float))]
    if not points:
        return None
    counts = Counter(points)
    median_point = statistics.median(points)
    return min(counts, key=lambda point: (-counts[point], abs(point - median_point), abs(point)))


def _same_point(rows: list[dict[str, Any]], point: float | None) -> list[dict[str, Any]]:
    if point is None:
        return list(rows)
    return [row for row in rows if row.get("market_point") == point]


def _assess(
    rows: Iterable[dict[str, Any]],
    *,
    side: str,
    opposite: str,
    model_probability: float,
) -> dict[str, Any]:
    items = [dict(row) for row in rows]
    probability = _clamp(float(model_probability), 0.0, 1.0)
    side_rows = [row for row in items if row.get("side") == side and ve.american_to_decimal(row.get("price")) is not None]
    fresh_side = [row for row in side_rows if mp.quote_freshness(row) == "fresh"]
    display_side = [row for row in side_rows if mp.quote_freshness(row) in {"fresh", "stale", "unknown"}]
    best_fresh = max(fresh_side, key=lambda row: ve.american_to_decimal(row["price"]) or 0.0, default=None)
    best_display = best_fresh or max(display_side, key=lambda row: ve.american_to_decimal(row["price"]) or 0.0, default=None)

    by_book: dict[str, dict[str, dict[str, Any]]] = {}
    for row in items:
        if mp.quote_freshness(row) != "fresh":
            continue
        book = str(row.get("book_key") or row.get("book") or "")
        row_side = str(row.get("side") or "")
        if book and row_side in {side, opposite}:
            by_book.setdefault(book, {})[row_side] = row
    fair_values: list[float] = []
    for pair in by_book.values():
        first = pair.get(side)
        second = pair.get(opposite)
        if not first or not second:
            continue
        fair = ve.devig_two_way(first.get("price"), second.get("price"), method="multiplicative")
        if fair:
            fair_values.append(float(fair[0]))

    fair_probability = statistics.median(fair_values) if fair_values else None
    implied_probability = ve.american_to_implied(best_display.get("price")) if best_display else None
    reference_probability = fair_probability if fair_probability is not None else implied_probability
    edge = probability - reference_probability if reference_probability is not None else None
    ev = ve.expected_value(probability, best_display.get("price")) if best_display else None
    kelly = ve.kelly_stake(probability, best_display.get("price"))["stake_pct"] if best_display else None

    if best_display is None:
        quote_status, price_status = "unpriced", "unpriced"
    elif best_fresh is None:
        quote_status, price_status = "stale", "stale"
    else:
        quote_status = "fresh"
        if fair_probability is not None and edge is not None and ev is not None and edge >= mp.MIN_EDGE and ev >= mp.MIN_EV:
            price_status = "positive_value"
        elif ev is not None and ev > 0:
            price_status = "thin_value"
        else:
            price_status = "no_value"

    actionable_value = bool(
        best_fresh is not None
        and fair_probability is not None
        and edge is not None
        and ev is not None
        and edge >= mp.MIN_EDGE
        and ev >= mp.MIN_EV
    )
    return {
        "side": side,
        "quoteStatus": quote_status,
        "priceStatus": price_status,
        "bestPrice": _quote_view(best_display) if best_display else None,
        "quotedBookCount": len({str(row.get("book_key") or row.get("book")) for row in side_rows if row.get("book_key") or row.get("book")}),
        "freshBookCount": len({str(row.get("book_key") or row.get("book")) for row in fresh_side if row.get("book_key") or row.get("book")}),
        "pairedFairBookCount": len(fair_values),
        "fairMarketProbability": round(fair_probability, 4) if fair_probability is not None else None,
        "impliedProbability": round(implied_probability, 4) if implied_probability is not None else None,
        "referenceProbability": round(reference_probability, 4) if reference_probability is not None else None,
        "modelProbability": round(probability, 4),
        "edge": round(edge, 4) if edge is not None else None,
        "evPct": round(ev, 4) if ev is not None else None,
        "kellyPct": round(float(kelly), 4) if kelly is not None else None,
        "actionableValue": actionable_value,
        "thresholds": {
            "maximumQuoteAgeSeconds": mp.ACTIONABLE_MAX_AGE_SECONDS,
            "minimumEdge": mp.MIN_EDGE,
            "minimumEv": mp.MIN_EV,
            "pairedFairBookRequired": True,
        },
    }


def _market_grade(probability: float, confidence: float) -> str:
    selected = max(probability, 1.0 - probability)
    if selected >= 0.68 and confidence >= 75:
        return "Strong Play"
    if selected >= 0.62 and confidence >= 65:
        return "Play"
    if selected >= 0.56 and confidence >= 55:
        return "Lean"
    return "Pass"


def _expected_total(decision: dict[str, Any]) -> float | None:
    evidence = decision.get("evidence") or {}
    home = (evidence.get("home") or {}).get("basic") or {}
    away = (evidence.get("away") or {}).get("basic") or {}
    values = [home.get("ppg"), home.get("papg"), away.get("ppg"), away.get("papg")]
    if not all(isinstance(value, (int, float)) for value in values):
        return None
    home_points = (float(home["ppg"]) + float(away["papg"])) / 2.0
    away_points = (float(away["ppg"]) + float(home["papg"])) / 2.0
    return _clamp(home_points + away_points, 25.0, 70.0)


def _spread_home_probability(model_margin: float, home_point: float) -> float:
    sd = float(p40.policy()["simulationMarginSd"])
    return _clamp(_normal_cdf((model_margin + home_point) / sd), 0.02, 0.98)


def _total_over_probability(model_total: float, total_point: float) -> float:
    return _clamp(1.0 - _normal_cdf((total_point - model_total) / TOTAL_SD), 0.02, 0.98)


def _market_calibration(market: str, selected_probability: float) -> dict[str, Any]:
    """Lazy-load P5.4 to keep the P4.4 -> P4.3 -> P4.2 -> P4.1 chain acyclic."""
    try:
        from p54_game_market_calibration import apply_to_selected_probability

        return apply_to_selected_probability(market, selected_probability)
    except Exception:  # noqa: BLE001 - pricing must fail safely to the raw model
        return {
            "probability": float(selected_probability),
            "rawProbability": float(selected_probability),
            "applied": False,
            "market": market,
            "candidateId": None,
            "modelVersion": "p54-market-calibration-v1",
            "championState": "unavailable",
        }


def price_game_decision(
    decision: dict[str, Any],
    event: dict[str, Any] | None,
    *,
    fetched_at: float | None,
) -> dict[str, Any]:
    """Join one P4.0 game decision to moneyline/spread/total sportsbook quotes."""
    out = dict(decision)
    out["model"] = MODEL_NAME
    out["modelVersion"] = MODEL_VERSION
    out["sourceModel"] = decision.get("modelVersion")
    out["oddsEventId"] = event.get("id") if event else None
    out["oddsCommenceTime"] = event.get("commence_time") if event else None
    out["markets"] = {}
    if not event:
        out["marketStatus"] = "unpriced"
        out["actionable"] = False
        out["actionableMarkets"] = []
        return out

    quotes = _flatten_event(event, fetched_at)
    confidence = float(decision.get("confidenceScore") or 0.0)

    # Moneyline calibration remains owned by P5.0/P4.0.
    home_prob = float(decision.get("homeWinProbability") or 0.5)
    ml_side = "home" if home_prob >= 0.5 else "away"
    ml_prob = home_prob if ml_side == "home" else 1.0 - home_prob
    ml_grade = str(decision.get("decisionGrade") or _market_grade(home_prob, confidence))
    ml_pricing = _assess(quotes["moneyline"], side=ml_side, opposite="away" if ml_side == "home" else "home", model_probability=ml_prob)
    ml_actionable = ml_grade in {"Strong Play", "Play"} and bool(ml_pricing["actionableValue"])
    out["markets"]["moneyline"] = {
        "market": "moneyline",
        "line": None,
        "selectedSide": ml_side,
        "selectedTeam": decision.get("homeTeam") if ml_side == "home" else decision.get("awayTeam"),
        "modelProbability": round(ml_prob, 4),
        "confidenceScore": round(confidence, 2),
        "decisionGrade": ml_grade,
        "pricing": ml_pricing,
        "actionable": ml_actionable,
    }

    # Spread: fit/apply only a spread-specific P5.4 champion after side selection.
    spread_point = _canonical_point(quotes["spread"])
    if spread_point is not None:
        spread_rows = _same_point(quotes["spread"], spread_point)
        home_cover = _spread_home_probability(float(decision.get("modelHomeMargin") or 0.0), spread_point)
        spread_side = "home" if home_cover >= 0.5 else "away"
        raw_spread_prob = home_cover if spread_side == "home" else 1.0 - home_cover
        spread_calibration = _market_calibration("spread", raw_spread_prob)
        spread_prob = _clamp(float(spread_calibration.get("probability") or raw_spread_prob), 0.5, 0.999)
        spread_conf = max(35.0, confidence - 4.0)
        spread_grade = _market_grade(spread_prob, spread_conf)
        spread_pricing = _assess(spread_rows, side=spread_side, opposite="away" if spread_side == "home" else "home", model_probability=spread_prob)
        spread_actionable = spread_grade in {"Strong Play", "Play"} and bool(spread_pricing["actionableValue"])
        selected_line = spread_point if spread_side == "home" else -spread_point
        market_model_version = MODEL_VERSION
        if spread_calibration.get("applied") and spread_calibration.get("candidateId"):
            market_model_version = f"{MODEL_VERSION}+{spread_calibration['candidateId']}"
        out["markets"]["spread"] = {
            "market": "spread",
            "line": round(selected_line, 2),
            "homeMarketPoint": round(spread_point, 2),
            "selectedSide": spread_side,
            "selectedTeam": decision.get("homeTeam") if spread_side == "home" else decision.get("awayTeam"),
            "prePromotionProbability": round(raw_spread_prob, 4),
            "modelProbability": round(spread_prob, 4),
            "confidenceScore": round(spread_conf, 2),
            "decisionGrade": spread_grade,
            "marketCalibration": spread_calibration,
            "marketModelVersion": market_model_version,
            "pricing": spread_pricing,
            "actionable": spread_actionable,
        }

    # Total: fit/apply only a total-specific P5.4 champion after side selection.
    total_point = _canonical_point(quotes["total"])
    expected_total = _expected_total(decision)
    if total_point is not None and expected_total is not None:
        over_prob = _total_over_probability(expected_total, total_point)
        total_side = "over" if over_prob >= 0.5 else "under"
        raw_total_prob = over_prob if total_side == "over" else 1.0 - over_prob
        total_calibration = _market_calibration("total", raw_total_prob)
        total_prob = _clamp(float(total_calibration.get("probability") or raw_total_prob), 0.5, 0.999)
        total_conf = max(35.0, min(79.0, confidence - 10.0))
        total_grade = _market_grade(total_prob, total_conf)
        total_rows = _same_point(quotes["total"], total_point)
        total_pricing = _assess(total_rows, side=total_side, opposite="under" if total_side == "over" else "over", model_probability=total_prob)
        total_actionable = total_grade in {"Strong Play", "Play"} and bool(total_pricing["actionableValue"])
        market_model_version = MODEL_VERSION
        if total_calibration.get("applied") and total_calibration.get("candidateId"):
            market_model_version = f"{MODEL_VERSION}+{total_calibration['candidateId']}"
        out["markets"]["total"] = {
            "market": "total",
            "line": round(total_point, 2),
            "modelExpectedTotal": round(expected_total, 2),
            "selectedSide": total_side,
            "prePromotionProbability": round(raw_total_prob, 4),
            "modelProbability": round(total_prob, 4),
            "confidenceScore": round(total_conf, 2),
            "decisionGrade": total_grade,
            "marketCalibration": total_calibration,
            "marketModelVersion": market_model_version,
            "pricing": total_pricing,
            "actionable": total_actionable,
            "risk": "Total confidence is discounted until pace/play-volume modeling is added.",
        }

    actionable_markets = [key for key, market in out["markets"].items() if market.get("actionable")]
    statuses = [str((market.get("pricing") or {}).get("quoteStatus")) for market in out["markets"].values()]
    out["actionableMarkets"] = actionable_markets
    out["actionable"] = bool(actionable_markets)
    out["marketStatus"] = "fresh" if "fresh" in statuses else ("stale" if "stale" in statuses else "unpriced")
    return out


def _load_market_snapshot(pricing_mode: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    mode = pricing_mode.lower()
    if mode == "off":
        return [], odds_api.snapshot_status()
    if mode == "live":
        events = odds_api.get_game_odds(force=True) if odds_api.is_configured() else []
        return events, odds_api.snapshot_status()
    if mode == "auto":
        events = odds_api.get_game_odds() if odds_api.is_configured() else odds_api.peek_game_odds()
        return events, odds_api.snapshot_status()
    return odds_api.peek_game_odds(), odds_api.snapshot_status()


def build_week_market_report(
    season: int,
    week: int,
    season_type: str = "REG",
    *,
    pricing_mode: str = "auto",
) -> dict[str, Any]:
    """Return the P4.1 weekly game board with protected market actionability."""
    if pricing_mode.lower() not in {"off", "cache", "auto", "live"}:
        raise ValueError("pricing_mode must be off, cache, auto, or live")
    model_report = p40.build_week_report(season, week, season_type)
    schedule_games = nfl_data.get_week_games(season, week, season_type, live=False)
    game_by_id = {str(game.get("game_id")): game for game in schedule_games}
    _, status = _load_market_snapshot(pricing_mode)
    fetched_at = _game_snapshot_fetched_at(status)

    rows: list[dict[str, Any]] = []
    for decision in model_report.get("decisions", []):
        game = game_by_id.get(str(decision.get("gameId")))
        event = None
        if game is not None and pricing_mode.lower() != "off":
            event = odds_api.find_event_for_game(game, cache_only=True)
        rows.append(price_game_decision(decision, event, fetched_at=fetched_at))

    actionable = [row for row in rows if row.get("actionable")]
    priced = [row for row in rows if row.get("marketStatus") in {"fresh", "stale"}]
    market_counts = Counter(
        market_key
        for row in rows
        for market_key, market in (row.get("markets") or {}).items()
        if (market.get("pricing") or {}).get("quoteStatus") != "unpriced"
    )
    actionable_market_counts = Counter(
        market_key
        for row in rows
        for market_key, market in (row.get("markets") or {}).items()
        if market.get("actionable")
    )
    return {
        "available": model_report.get("available", False),
        "model": MODEL_NAME,
        "modelVersion": MODEL_VERSION,
        "sourceModelVersion": model_report.get("modelVersion"),
        "season": season,
        "seasonType": season_type,
        "week": week,
        "pricingMode": pricing_mode.lower(),
        "oddsConfigured": odds_api.is_configured(),
        "gameSnapshotAgeSeconds": status.get("game_snapshot_age_seconds"),
        "gameCount": model_report.get("gameCount", 0),
        "decisionCount": len(rows),
        "pricedGameCount": len(priced),
        "actionableGameCount": len(actionable),
        "marketCoverage": dict(sorted(market_counts.items())),
        "actionableMarkets": dict(sorted(actionable_market_counts.items())),
        "rows": rows,
        "safety": {
            "freshQuoteRequired": True,
            "pairedFairBookRequired": True,
            "strongPlayOrPlayRequired": True,
            "liveRefreshRequiresExplicitMode": True,
            "marketCalibrationCannotFlipSelectedSide": True,
        },
    }


def verify_actionability(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Structural P4.1 fail-closed audit for a weekly market board."""
    market_rows = [market for row in rows for market in (row.get("markets") or {}).values()]
    actionable = [market for market in market_rows if market.get("actionable")]
    invalid = [
        market
        for market in actionable
        if market.get("decisionGrade") not in {"Strong Play", "Play"}
        or (market.get("pricing") or {}).get("quoteStatus") != "fresh"
        or (market.get("pricing") or {}).get("priceStatus") != "positive_value"
        or int((market.get("pricing") or {}).get("pairedFairBookCount") or 0) < 1
    ]
    stale_actionable = [
        market for market in actionable if (market.get("pricing") or {}).get("quoteStatus") != "fresh"
    ]
    gates = {
        "actionable_contract_integrity": not invalid,
        "stale_quotes_fail_closed": not stale_actionable,
        "paired_market_required": all(int((market.get("pricing") or {}).get("pairedFairBookCount") or 0) >= 1 for market in actionable),
    }
    return {
        "markets": len(market_rows),
        "actionableMarkets": len(actionable),
        "gates": gates,
        "ok": all(gates.values()),
    }
