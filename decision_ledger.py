"""P3.7 immutable publication receipts and automatic outcome grading.

Quick Props is a product publication surface. Once a Lean-or-better decision is
shown there, P3.7 records the first published version as an immutable receipt so
later model/price movement cannot rewrite history. Outcome grading lives in
separate result columns/payloads and therefore never mutates the release-time
model evidence.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Iterable

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

from database import db
import nfl_data
import value_engine as ve


decision_ledger_receipts = sa.Table(
    "decision_ledger_receipts",
    db.metadata,
    sa.Column("receipt_id", sa.String(24), primary_key=True),
    sa.Column("release_key", sa.String(320), nullable=False, unique=True),
    sa.Column("event_date", sa.String(10), index=True),
    sa.Column("season", sa.Integer, index=True),
    sa.Column("week", sa.Integer, index=True),
    sa.Column("season_type", sa.String(8), index=True),
    sa.Column("game_id", sa.String(40), nullable=False, index=True),
    sa.Column("player_id", sa.String(80), index=True),
    sa.Column("market_key", sa.String(80), nullable=False, index=True),
    sa.Column("decision_grade", sa.String(24), nullable=False, index=True),
    sa.Column("actionable", sa.Boolean, nullable=False, default=False, index=True),
    sa.Column("release_fingerprint", sa.String(64), nullable=False),
    sa.Column("release_payload", sa.JSON, nullable=False),
    sa.Column("grade", sa.String(16), nullable=False, default="pending", index=True),
    sa.Column("actual", sa.Float),
    sa.Column("result_payload", sa.JSON),
    sa.Column("released_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("graded_at", sa.DateTime(timezone=True), index=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)

_PICK_GRADES = frozenset({"Strong Play", "Play", "Lean"})
_PROP_STAT = {
    "pass_yds": "passing_yards",
    "pass_tds": "passing_tds",
    "rush_yds": "rushing_yards",
    "receptions": "receptions",
    "rec_yds": "receiving_yards",
}

_RELEASE_FIELDS = (
    "gameId",
    "season",
    "week",
    "gameday",
    "player",
    "playerId",
    "team",
    "opponent",
    "position",
    "marketKey",
    "marketLabel",
    "line",
    "side",
    "modelMean",
    "modelProb",
    "consensusProb",
    "simulationProb",
    "simulationAgreement",
    "confidenceScore",
    "confidenceGrade",
    "matchupGrade",
    "decisionGrade",
    "decisionScore",
    "decisionReasons",
    "decisionRisks",
    "price",
    "book",
    "bestPrice",
    "priceStatus",
    "quoteStatus",
    "fairProb",
    "fairMarketProb",
    "impliedProb",
    "referenceProb",
    "edge",
    "evPct",
    "kellyPct",
    "freshBookCount",
    "pairedFairBookCount",
    "marketPricing",
    "oddsSnapshotAgeSeconds",
    "actionable",
    "modelSource",
    "decisionModelVersion",
    "evidenceSeason",
    "rosterVerified",
)


def _now() -> datetime:
    return datetime.now(UTC)


def _rollback_quietly() -> None:
    try:
        db.session.rollback()
    except Exception:  # noqa: BLE001
        pass


def _json_fingerprint(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _release_key(row: dict[str, Any], context: dict[str, Any]) -> str:
    parts = (
        context.get("season") or row.get("season"),
        context.get("week") or row.get("week"),
        context.get("season_type") or context.get("seasonType"),
        row.get("gameId"),
        row.get("playerId") or row.get("player"),
        row.get("marketKey"),
        row.get("line"),
        row.get("side"),
        row.get("decisionModelVersion") or row.get("modelSource"),
    )
    return "|".join("" if value is None else str(value) for value in parts)


def build_receipt(row: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the immutable release receipt without writing it."""
    context = dict(context or {})
    release = {key: row.get(key) for key in _RELEASE_FIELDS if key in row}
    # P3.6 rows expose the selected sportsbook in bestPrice rather than always
    # duplicating it into top-level price/book. Normalize those fields once at
    # publication so the receipt is easy to audit later.
    best = row.get("bestPrice") if isinstance(row.get("bestPrice"), dict) else {}
    if release.get("price") is None and isinstance(best.get("price"), (int, float)):
        release["price"] = best.get("price")
    if not release.get("book") and best.get("book"):
        release["book"] = best.get("book")
    release["publicationSource"] = str(context.get("source") or "quick_props")
    release["seasonType"] = str(
        context.get("season_type") or context.get("seasonType") or ""
    )
    release["publicationModelVersion"] = str(
        context.get("modelVersion")
        or row.get("modelSource")
        or row.get("decisionModelVersion")
        or "p3.7-ledger"
    )
    release_key = _release_key(row, context)
    fingerprint = _json_fingerprint(release)
    receipt_id = hashlib.sha256(release_key.encode("utf-8")).hexdigest()[:20]
    return {
        "receiptId": receipt_id,
        "releaseKey": release_key,
        "releaseFingerprint": fingerprint,
        "release": release,
    }


def record_delivery(
    rows: Iterable[dict[str, Any]],
    *,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist first-publication receipts; existing receipts are never rewritten."""
    context = dict(context or {})
    candidates = [row for row in rows if row.get("decisionGrade") in _PICK_GRADES]
    inserted = existing = failed = 0
    receipt_ids: list[str] = []
    now = _now()
    try:
        for row in candidates:
            receipt = build_receipt(row, context)
            receipt_id = receipt["receiptId"]
            receipt_ids.append(receipt_id)
            prior = db.session.execute(
                sa.select(decision_ledger_receipts.c.receipt_id).where(
                    decision_ledger_receipts.c.release_key == receipt["releaseKey"]
                )
            ).scalar_one_or_none()
            if prior is not None:
                existing += 1
                continue
            release = receipt["release"]
            db.session.execute(
                decision_ledger_receipts.insert().values(
                    receipt_id=receipt_id,
                    release_key=receipt["releaseKey"],
                    event_date=str(release.get("gameday") or "")[:10] or None,
                    season=_int_or_none(release.get("season") or context.get("season")),
                    week=_int_or_none(release.get("week") or context.get("week")),
                    season_type=str(release.get("seasonType") or "")[:8] or None,
                    game_id=str(release.get("gameId") or ""),
                    player_id=(str(release.get("playerId")) if release.get("playerId") is not None else None),
                    market_key=str(release.get("marketKey") or ""),
                    decision_grade=str(release.get("decisionGrade") or "Lean"),
                    actionable=bool(release.get("actionable")),
                    release_fingerprint=receipt["releaseFingerprint"],
                    release_payload=release,
                    grade="pending",
                    released_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
            inserted += 1
        db.session.commit()
    except (RuntimeError, SQLAlchemyError):
        failed = max(1, len(candidates) - inserted - existing)
        _rollback_quietly()
        inserted = 0
    return {
        "candidates": len(candidates),
        "inserted": inserted,
        "existing": existing,
        "failed": failed,
        "receiptIds": receipt_ids,
    }


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _grade_release(release: dict[str, Any], stat_rows: dict[tuple[str, str], dict[str, Any]]) -> tuple[str, float] | None:
    market = str(release.get("marketKey") or "")
    if market not in {*_PROP_STAT, "anytime_td"}:
        return None
    key = (str(release.get("gameId") or ""), str(release.get("playerId") or ""))
    row = stat_rows.get(key)
    if row is None:
        return None
    if market == "anytime_td":
        actual = float(row.get("rushing_tds") or 0) + float(row.get("receiving_tds") or 0)
    else:
        actual = float(row.get(_PROP_STAT[market]) or 0)
    try:
        line = float(release.get("line"))
    except (TypeError, ValueError):
        return None
    if actual == line:
        return ("push", actual)
    side = str(release.get("side") or "over")
    over = actual > line
    return ("win" if (side == "over") == over else "loss", actual)


def grade_pending() -> dict[str, Any]:
    """Grade published player-prop receipts whose games are final."""
    try:
        rows = db.session.execute(
            sa.select(
                decision_ledger_receipts.c.receipt_id,
                decision_ledger_receipts.c.season,
                decision_ledger_receipts.c.game_id,
                decision_ledger_receipts.c.release_payload,
            ).where(decision_ledger_receipts.c.grade == "pending")
        ).all()
    except (RuntimeError, SQLAlchemyError):
        _rollback_quietly()
        return {"graded": 0, "pending": 0, "available": False}
    if not rows:
        return {"graded": 0, "pending": 0, "available": True}

    seasons = {int(row.season) for row in rows if row.season is not None}
    final_games: set[str] = set()
    stat_rows: dict[tuple[str, str], dict[str, Any]] = {}
    for season in seasons:
        for game in nfl_data.get_schedule(season):
            if game.get("completed"):
                final_games.add(str(game.get("game_id")))
        for stat in nfl_data.get_player_week_stats(season):
            stat_rows[(str(stat.get("game_id")), str(stat.get("player_id")))] = stat

    graded = 0
    now = _now()
    try:
        for row in rows:
            if str(row.game_id) not in final_games:
                continue
            release = dict(row.release_payload or {})
            result = _grade_release(release, stat_rows)
            if result is None:
                continue
            grade, actual = result
            probability = _probability_for_release(release)
            binary = 1.0 if grade == "win" else (0.0 if grade == "loss" else None)
            brier = (
                round((probability - binary) ** 2, 6)
                if probability is not None and binary is not None
                else None
            )
            price = release.get("price")
            decimal = ve.american_to_decimal(price)
            unit_profit = None
            if decimal is not None:
                unit_profit = round(decimal - 1.0, 4) if grade == "win" else (-1.0 if grade == "loss" else 0.0)
            result_payload = {
                "grade": grade,
                "actual": actual,
                "probability": probability,
                "brier": brier,
                "unitProfit": unit_profit,
            }
            db.session.execute(
                decision_ledger_receipts.update()
                .where(decision_ledger_receipts.c.receipt_id == row.receipt_id)
                .values(
                    grade=grade,
                    actual=actual,
                    result_payload=result_payload,
                    graded_at=now,
                    updated_at=now,
                )
            )
            graded += 1
        db.session.commit()
    except (RuntimeError, SQLAlchemyError):
        _rollback_quietly()
        return {"graded": 0, "pending": len(rows), "available": False}
    return {"graded": graded, "pending": len(rows) - graded, "available": True}


def _probability_for_release(release: dict[str, Any]) -> float | None:
    for key in ("consensusProb", "modelProb"):
        value = release.get(key)
        if isinstance(value, (int, float)):
            return max(0.0, min(1.0, float(value)))
    return None


def list_receipts(*, limit: int = 500, season: int | None = None, week: int | None = None) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 2000))
    stmt = sa.select(decision_ledger_receipts)
    if season is not None:
        stmt = stmt.where(decision_ledger_receipts.c.season == int(season))
    if week is not None:
        stmt = stmt.where(decision_ledger_receipts.c.week == int(week))
    stmt = stmt.order_by(decision_ledger_receipts.c.released_at.desc()).limit(safe_limit)
    try:
        rows = db.session.execute(stmt).mappings().all()
    except (RuntimeError, SQLAlchemyError):
        _rollback_quietly()
        return []
    return [_public_receipt(dict(row)) for row in rows]


def _public_receipt(row: dict[str, Any]) -> dict[str, Any]:
    release = dict(row.get("release_payload") or {})
    result = dict(row.get("result_payload") or {})
    return {
        "receiptId": row.get("receipt_id"),
        "releasedAt": row.get("released_at").isoformat() if row.get("released_at") else None,
        "releaseFingerprint": row.get("release_fingerprint"),
        "grade": row.get("grade"),
        "gradedAt": row.get("graded_at").isoformat() if row.get("graded_at") else None,
        "actual": row.get("actual"),
        "release": release,
        "result": result,
    }


def _ece(samples: list[tuple[float, float]], bins: int = 10) -> float | None:
    if not samples:
        return None
    total = len(samples)
    error = 0.0
    for idx in range(bins):
        low, high = idx / bins, (idx + 1) / bins
        bucket = [sample for sample in samples if low <= sample[0] < high or (idx == bins - 1 and sample[0] == 1.0)]
        if not bucket:
            continue
        avg_p = sum(item[0] for item in bucket) / len(bucket)
        avg_y = sum(item[1] for item in bucket) / len(bucket)
        error += len(bucket) / total * abs(avg_p - avg_y)
    return round(error, 6)


def performance_summary() -> dict[str, Any]:
    try:
        rows = db.session.execute(sa.select(decision_ledger_receipts)).mappings().all()
    except (RuntimeError, SQLAlchemyError):
        _rollback_quietly()
        return {"available": False, "receipts": 0}
    receipts = [dict(row) for row in rows]
    graded = [row for row in receipts if row.get("grade") in {"win", "loss", "push"}]
    wins = sum(row.get("grade") == "win" for row in graded)
    losses = sum(row.get("grade") == "loss" for row in graded)
    pushes = sum(row.get("grade") == "push" for row in graded)
    calibration: list[tuple[float, float]] = []
    briers: list[float] = []
    unit_profits: list[float] = []
    per_grade: dict[str, dict[str, Any]] = {}
    per_market: dict[str, dict[str, Any]] = {}
    for row in graded:
        release = dict(row.get("release_payload") or {})
        result = dict(row.get("result_payload") or {})
        probability = result.get("probability")
        if row.get("grade") in {"win", "loss"} and isinstance(probability, (int, float)):
            outcome = 1.0 if row.get("grade") == "win" else 0.0
            calibration.append((float(probability), outcome))
            if isinstance(result.get("brier"), (int, float)):
                briers.append(float(result["brier"]))
        if isinstance(result.get("unitProfit"), (int, float)):
            unit_profits.append(float(result["unitProfit"]))
        for key, bucket_key in ((str(release.get("decisionGrade") or "?"), "grade"), (str(release.get("marketKey") or "?"), "market")):
            target = per_grade if bucket_key == "grade" else per_market
            bucket = target.setdefault(key, {"n": 0, "wins": 0, "losses": 0, "pushes": 0})
            bucket["n"] += 1
            if row.get("grade") == "win":
                bucket["wins"] += 1
            elif row.get("grade") == "loss":
                bucket["losses"] += 1
            elif row.get("grade") == "push":
                bucket["pushes"] += 1
    decided = wins + losses
    return {
        "available": True,
        "receipts": len(receipts),
        "pending": len(receipts) - len(graded),
        "graded": len(graded),
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "hitRate": round(wins / decided, 4) if decided else None,
        "brier": round(sum(briers) / len(briers), 6) if briers else None,
        "ece": _ece(calibration),
        "calibrationSamples": len(calibration),
        "pricedGraded": len(unit_profits),
        "unitProfit": round(sum(unit_profits), 4) if unit_profits else None,
        "unitRoi": round(sum(unit_profits) / len(unit_profits), 4) if unit_profits else None,
        "actionableReceipts": sum(bool(row.get("actionable")) for row in receipts),
        "perDecisionGrade": per_grade,
        "perMarket": per_market,
        "model": "p3.7-publication-ledger",
    }


def ledger_status() -> dict[str, Any]:
    try:
        rows = db.session.execute(
            sa.select(
                decision_ledger_receipts.c.grade,
                decision_ledger_receipts.c.actionable,
                decision_ledger_receipts.c.released_at,
            )
        ).all()
    except (RuntimeError, SQLAlchemyError):
        _rollback_quietly()
        return {
            "backend": "database",
            "available": False,
            "receipts": 0,
            "pending": 0,
            "graded": 0,
            "actionable": 0,
            "latestReleasedAt": None,
        }
    latest = max((row.released_at for row in rows if row.released_at is not None), default=None)
    return {
        "backend": "database",
        "available": True,
        "receipts": len(rows),
        "pending": sum(row.grade == "pending" for row in rows),
        "graded": sum(row.grade in {"win", "loss", "push"} for row in rows),
        "actionable": sum(bool(row.actionable) for row in rows),
        "latestReleasedAt": latest.isoformat() if latest is not None else None,
    }
