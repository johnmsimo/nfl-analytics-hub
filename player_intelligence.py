"""Evidence-aware player projection and matchup intelligence for P3.3.

This layer sits above the transparent distribution model in ``projections``.
It does not invent a second statistical model.  Instead it measures the
quality of the evidence feeding that model, shrinks probabilities toward 50%
when evidence is thin or unstable, exposes uncertainty bands, and produces a
stable ranking signal for Quick Props even when sportsbook prices are sparse.
"""
from __future__ import annotations

import math
import os
from statistics import NormalDist, median, pstdev
from typing import Any

import nfl_data
import projection_data as projection_data
import projections as projections

_MARKET_COL = {
    "pass_yds": "passing_yards",
    "pass_tds": "passing_tds",
    "rush_yds": "rushing_yards",
    "receptions": "receptions",
    "rec_yds": "receiving_yards",
    "anytime_td": "_scrimmage_tds",
}
_DVP_STAT = {
    "pass_yds": "passing_yards",
    "pass_tds": "passing_tds",
    "rush_yds": "rushing_yards",
    "receptions": "receptions",
    "rec_yds": "receiving_yards",
}
_SKILL = frozenset({"QB", "RB", "FB", "WR", "TE"})
_NORMAL = NormalDist()


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _values(rows: list[dict[str, Any]], market: str) -> list[float]:
    col = _MARKET_COL[market]
    if col == "_scrimmage_tds":
        return [float(row.get("rushing_tds") or 0) + float(row.get("receiving_tds") or 0) for row in rows]
    return [float(row.get(col) or 0) for row in rows]


def _poisson_quantile(lam: float, q: float) -> float:
    if lam <= 0:
        return 0.0
    q = _clamp(q)
    term = math.exp(-lam)
    cdf = term
    k = 0
    while cdf < q and k < 200:
        k += 1
        term *= lam / k
        cdf += term
    return float(k)


def _distribution_quantile(projection: dict[str, Any], q: float) -> float:
    dist = projection["dist"]
    if dist == "normal":
        return max(0.0, float(projection["mean"]) + float(projection["sd"]) * _NORMAL.inv_cdf(q))
    if dist == "lognormal":
        return max(
            0.0,
            math.exp(float(projection["mu"]) + float(projection["sigma"]) * _NORMAL.inv_cdf(q))
            - 8.0,
        )
    return _poisson_quantile(float(projection["mean"]), q)


def _matchup_context(market: str, position: str, opponent: str | None, dvp: dict | None) -> dict[str, Any]:
    factor = projections.opponent_factor(market, position, opponent or "", dvp or {}) if opponent else 1.0
    team_cell = (dvp or {}).get(opponent or "") or {}
    group = position if position in {"QB", "RB", "WR", "TE"} else ("RB" if position == "FB" else "WR")
    group_cell = team_cell.get(group) or {}
    games = int(team_cell.get("games") or 0)
    if market == "anytime_td":
        ratios = [group_cell.get("rushing_tds_ratio"), group_cell.get("receiving_tds_ratio")]
        usable = [float(value) for value in ratios if isinstance(value, (int, float)) and value > 0]
        raw_ratio = sum(usable) / len(usable) if usable else 1.0
    else:
        stat = _DVP_STAT.get(market)
        value = group_cell.get(f"{stat}_ratio") if stat else None
        raw_ratio = float(value) if isinstance(value, (int, float)) and value > 0 else 1.0
    quality = _clamp(games / 12.0) if opponent and group_cell else 0.0
    if factor >= 1.08:
        grade = "favorable"
    elif factor <= 0.92:
        grade = "tough"
    else:
        grade = "neutral"
    return {
        "opponent": opponent,
        "rawRatio": round(raw_ratio, 3),
        "factor": round(float(factor), 3),
        "grade": grade,
        "dataGames": games,
        "dataQuality": round(quality, 3),
    }


def analyze_projection(
    rows: list[dict[str, Any]],
    market: str,
    *,
    opponent: str | None = None,
    dvp: dict | None = None,
    position: str = "WR",
    line: float | None = None,
    roster_verified: bool = True,
) -> dict[str, Any] | None:
    """Return projection, matchup, uncertainty, and evidence-confidence metadata."""
    projection = projections.project_stat(rows, market, opponent=opponent, dvp=dvp, position=position)
    if not projection:
        return None

    vals = _values(rows, market)
    n = len(vals)
    season_mean = float(projection["season_mean"])
    l4_mean = float(projection["l4_mean"])
    spread = pstdev(vals) if len(vals) > 1 else 0.0
    scale = max(abs(season_mean), float(projections.MIN_MEAN[market]), 1.0)
    coefficient = spread / scale
    stability = 1.0 / (1.0 + coefficient)
    trend_gap = abs(l4_mean - season_mean) / scale
    trend_agreement = math.exp(-trend_gap)
    sample_quality = _clamp(n / 10.0)
    matchup = _matchup_context(market, position, opponent, dvp)
    matchup_quality = float(matchup["dataQuality"]) if opponent else 0.5
    roster_quality = 1.0 if roster_verified else 0.0

    confidence = _clamp(
        0.35 * sample_quality
        + 0.25 * stability
        + 0.20 * trend_agreement
        + 0.10 * matchup_quality
        + 0.10 * roster_quality
    )
    if confidence >= 0.80:
        confidence_grade = "high"
    elif confidence >= 0.65:
        confidence_grade = "medium"
    elif confidence >= 0.50:
        confidence_grade = "guarded"
    else:
        confidence_grade = "low"

    raw_probability = projections.prob_over(projection, line) if line is not None else None
    calibrated_probability = None
    if raw_probability is not None:
        # Evidence-aware shrinkage prevents a thin/volatile sample from looking
        # more certain than the evidence warrants.  Strong evidence preserves
        # nearly all of the transparent distribution model's signal.
        preservation = 0.55 + 0.45 * confidence
        calibrated_probability = 0.5 + (float(raw_probability) - 0.5) * preservation
        calibrated_probability = _clamp(calibrated_probability)

    interval = {
        "p10": round(_distribution_quantile(projection, 0.10), 2),
        "p50": round(_distribution_quantile(projection, 0.50), 2),
        "p90": round(_distribution_quantile(projection, 0.90), 2),
    }
    trend_pct = (l4_mean - season_mean) / scale
    risks: list[str] = []
    if n < 5:
        risks.append("thin_sample")
    if stability < 0.55:
        risks.append("high_volatility")
    if trend_agreement < 0.70:
        risks.append("recent_form_conflict")
    if opponent and matchup_quality < 0.50:
        risks.append("thin_matchup_history")
    if not roster_verified:
        risks.append("roster_unverified")

    signal_strength = (
        abs(float(calibrated_probability) - 0.5) * 2.0 * confidence
        if calibrated_probability is not None
        else 0.0
    )
    return {
        **projection,
        "rawProbOver": round(float(raw_probability), 4) if raw_probability is not None else None,
        "probOver": round(float(calibrated_probability), 4) if calibrated_probability is not None else None,
        "interval": interval,
        "matchup": matchup,
        "trendPct": round(trend_pct, 4),
        "confidence": {
            "score": round(confidence, 4),
            "grade": confidence_grade,
            "sampleQuality": round(sample_quality, 4),
            "stability": round(stability, 4),
            "trendAgreement": round(trend_agreement, 4),
            "matchupQuality": round(matchup_quality, 4),
            "rosterQuality": round(roster_quality, 4),
        },
        "riskFlags": risks,
        "signalStrength": round(signal_strength, 4),
        "modelVersion": "p3.3-evidence-calibrated",
    }


def ranking_score(intelligence: dict[str, Any], *, edge: float | None = None, ev: float | None = None) -> float:
    """Stable model-first ranking used by Quick Props and the weekly board."""
    confidence = float((intelligence.get("confidence") or {}).get("score") or 0.0)
    signal = float(intelligence.get("signalStrength") or 0.0)
    score = 0.58 * confidence + 0.42 * signal
    if edge is not None:
        score += min(abs(float(edge)), 0.20) * 1.25
    if ev is not None and ev > 0:
        score += min(float(ev), 0.20) * 0.75
    return round(_clamp(score, 0.0, 1.0), 4)


def _reference_line(prior: list[dict[str, Any]], market: str) -> float:
    if market == "anytime_td":
        return 0.5
    vals = _values(prior, market)
    center = median(vals) if vals else 0.0
    return math.floor(center) + 0.5


def backtest_market(logs: dict[str, list[dict[str, Any]]], market: str, min_prior_games: int = 4) -> dict[str, Any]:
    """Leave-forward history-only calibration check with no future leakage."""
    predictions: list[tuple[float, int]] = []
    for rows in logs.values():
        for index in range(min_prior_games, len(rows)):
            prior = rows[:index]
            actual_row = rows[index]
            position = str(actual_row.get("position") or "")
            if market not in projections.relevant_markets(position):
                continue
            base = projections.project_stat(prior, market, position=position)
            if not base or float(base["mean"]) < float(projections.MIN_MEAN[market]):
                continue
            line = _reference_line(prior, market)
            intel = analyze_projection(prior, market, position=position, line=line, roster_verified=True)
            if not intel or intel.get("probOver") is None:
                continue
            actual_value = _values([actual_row], market)[0]
            predictions.append((float(intel["probOver"]), int(actual_value > line)))

    if not predictions:
        return {"market": market, "n": 0}
    n = len(predictions)
    brier = sum((prob - actual) ** 2 for prob, actual in predictions) / n
    buckets: dict[int, list[tuple[float, int]]] = {}
    for prob, actual in predictions:
        bucket = min(int(prob * 5), 4)
        buckets.setdefault(bucket, []).append((prob, actual))
    ece = 0.0
    reliability: dict[str, Any] = {}
    for bucket, values in sorted(buckets.items()):
        pred = sum(item[0] for item in values) / len(values)
        observed = sum(item[1] for item in values) / len(values)
        ece += (len(values) / n) * abs(pred - observed)
        reliability[f"{bucket * 20}-{bucket * 20 + 20}%"] = {
            "n": len(values),
            "pred": round(pred, 3),
            "actual": round(observed, 3),
        }
    return {
        "market": market,
        "n": n,
        "brier": round(brier, 4),
        "ece": round(ece, 4),
        "meanPred": round(sum(prob for prob, _ in predictions) / n, 4),
        "actualRate": round(sum(actual for _, actual in predictions) / n, 4),
        "reliability": reliability,
    }


def backtest_suite(logs: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    markets = [backtest_market(logs, market) for market in projections.MARKETS]
    usable = [row for row in markets if int(row.get("n") or 0) > 0]
    total = sum(int(row["n"]) for row in usable)
    weighted_brier = (
        sum(float(row["brier"]) * int(row["n"]) for row in usable) / total if total else None
    )
    weighted_ece = (
        sum(float(row["ece"]) * int(row["n"]) for row in usable) / total if total else None
    )
    return {
        "markets": usable,
        "marketCount": len(usable),
        "samples": total,
        "weightedBrier": round(weighted_brier, 4) if weighted_brier is not None else None,
        "weightedEce": round(weighted_ece, 4) if weighted_ece is not None else None,
    }


def readiness_snapshot(target_season: int = 2026) -> dict[str, Any]:
    """Aggregate-only P3.3 production gate; never calls sportsbook providers."""
    evidence_season = projection_data.stats_season(target_season)
    logs = projection_data.player_game_logs(evidence_season)
    index = projection_data.player_index(target_season, evidence_season)
    dvp = projection_data.defense_vs_position(evidence_season)
    schedule = nfl_data.get_schedule(target_season)
    next_opponent: dict[str, str] = {}
    for game in schedule:
        if game.get("completed"):
            continue
        home, away = game.get("home_team"), game.get("away_team")
        if home and away:
            next_opponent.setdefault(str(home), str(away))
            next_opponent.setdefault(str(away), str(home))

    eligible = 0
    projection_rows = 0
    confidence_rows = 0
    matchup_rows = 0
    probability_rows = 0
    for player_id, meta in index.items():
        position = str(meta.get("position") or "").upper()
        if position not in _SKILL:
            continue
        history = logs.get(player_id, [])
        opponent = next_opponent.get(str(meta.get("team") or ""))
        if len(history) < 3 or not opponent:
            continue
        eligible += 1
        for market in projections.relevant_markets(position):
            base = projections.project_stat(history, market, opponent=opponent, dvp=dvp, position=position)
            if not base or float(base["mean"]) < float(projections.MIN_MEAN[market]):
                continue
            line = _reference_line(history, market)
            intel = analyze_projection(
                history,
                market,
                opponent=opponent,
                dvp=dvp,
                position=position,
                line=line,
                roster_verified=bool(meta.get("rosterVerified")),
            )
            if not intel:
                continue
            projection_rows += 1
            if (intel.get("confidence") or {}).get("score") is not None:
                confidence_rows += 1
            if int((intel.get("matchup") or {}).get("dataGames") or 0) > 0:
                matchup_rows += 1
            probability = intel.get("probOver")
            if isinstance(probability, (int, float)) and 0.0 <= probability <= 1.0:
                probability_rows += 1

    confidence_coverage = confidence_rows / projection_rows if projection_rows else 0.0
    matchup_coverage = matchup_rows / projection_rows if projection_rows else 0.0
    probability_coverage = probability_rows / projection_rows if projection_rows else 0.0
    validation = backtest_suite(logs)
    min_rows = max(int(os.environ.get("P33_MIN_PROJECTION_ROWS", "500")), 1)
    min_players = max(int(os.environ.get("P33_MIN_ELIGIBLE_PLAYERS", "250")), 1)
    min_matchup = float(os.environ.get("P33_MIN_MATCHUP_COVERAGE", "0.80"))
    min_backtest = max(int(os.environ.get("P33_MIN_BACKTEST_SAMPLES", "1000")), 1)
    max_brier = float(os.environ.get("P33_MAX_BACKTEST_BRIER", "0.30"))
    max_ece = float(os.environ.get("P33_MAX_BACKTEST_ECE", "0.15"))
    gates = {
        "eligible_player_pool": eligible >= min_players,
        "projection_volume": projection_rows >= min_rows,
        "confidence_coverage": confidence_coverage == 1.0,
        "probability_bounds": probability_coverage == 1.0,
        "matchup_coverage": matchup_coverage >= min_matchup,
        "backtest_sample": int(validation.get("samples") or 0) >= min_backtest,
        "backtest_brier": validation.get("weightedBrier") is not None
        and float(validation["weightedBrier"]) <= max_brier,
        "backtest_calibration": validation.get("weightedEce") is not None
        and float(validation["weightedEce"]) <= max_ece,
    }
    return {
        "phase": "P3.3",
        "mode": "read-only",
        "targetSeason": target_season,
        "evidenceSeason": evidence_season,
        "eligiblePlayers": eligible,
        "projectionRows": projection_rows,
        "confidenceCoverage": round(confidence_coverage, 4),
        "matchupCoverage": round(matchup_coverage, 4),
        "probabilityCoverage": round(probability_coverage, 4),
        "validation": validation,
        "thresholds": {
            "minimumEligiblePlayers": min_players,
            "minimumProjectionRows": min_rows,
            "minimumMatchupCoverage": min_matchup,
            "minimumBacktestSamples": min_backtest,
            "maximumBacktestBrier": max_brier,
            "maximumBacktestEce": max_ece,
        },
        "gates": gates,
        "ok": all(gates.values()),
    }
