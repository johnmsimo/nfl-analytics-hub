"""P4.4 immutable game-decision publication ledger and outcome grading.

P4.3 is the user-facing publication boundary for actionable game markets. P4.4
records the first published version of each actionable moneyline/spread/total
recommendation in a dedicated ledger so game outcomes never contaminate the
P3.x player-prop calibration ledger.

Release-time model and price evidence is immutable. Grading writes only result
columns after a final NFL score exists.
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
import p43_game_decision_delivery as p43
import value_engine as ve

MODEL_NAME = "p4.4-game-decision-ledger"
MODEL_VERSION = "p44-game-ledger-v1"


game_decision_ledger_receipts = sa.Table(
    "game_decision_ledger_receipts",
    db.metadata,
    sa.Column("receipt_id", sa.String(24), primary_key=True),
    sa.Column("release_key", sa.String(320), nullable=False, unique=True),
    sa.Column("season", sa.Integer, index=True),
    sa.Column("week", sa.Integer, index=True),
    sa.Column("season_type", sa.String(8), index=True),
    sa.Column("game_id", sa.String(40), nullable=False, index=True),
    sa.Column("market_key", sa.String(24), nullable=False, index=True),
    sa.Column("selected_side", sa.String(16), nullable=False),
    sa.Column("selected_team", sa.String(12)),
    sa.Column("decision_grade", sa.String(24), nullable=False, index=True),
    sa.Column("release_fingerprint", sa.String(64), nullable=False),
    sa.Column("release_payload", sa.JSON, nullable=False),
    sa.Column("grade", sa.String(16), nullable=False, default="pending", index=True),
    sa.Column("home_score", sa.Integer),
    sa.Column("away_score", sa.Integer),
    sa.Column("result_payload", sa.JSON),
    sa.Column("released_at", sa.DateTime(timezone=True), nullable=False, index=True),
    sa.Column("graded_at", sa.DateTime(timezone=True), index=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)


def _now() -> datetime:
    return datetime.now(UTC)


def _rollback() -> None:
    try:
        db.session.rollback()
    except Exception:  # noqa: BLE001
        pass


def _fingerprint(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _release_key(item: dict[str, Any]) -> str:
    parts = (
        item.get("season"),
        item.get("seasonType"),
        item.get("week"),
        item.get("gameId"),
        item.get("market"),
        item.get("line"),
        item.get("selectedSide"),
        item.get("sourceModelVersion") or MODEL_VERSION,
    )
    return "|".join("" if value is None else str(value) for value in parts)


def build_receipt(item: dict[str, Any]) -> dict[str, Any]:
    """Build a canonical immutable release receipt without writing it."""
    best_price = item.get("bestPrice")
    if not isinstance(best_price, (int, float)):
        best_price = None
    release = {
        "gameId": item.get("gameId"),
        "season": item.get("season"),
        "seasonType": item.get("seasonType"),
        "week": item.get("week"),
        "kickoffAt": item.get("kickoffAt"),
        "homeTeam": item.get("homeTeam"),
        "awayTeam": item.get("awayTeam"),
        "marketKey": item.get("market"),
        "marketLabel": item.get("marketLabel"),
        "pickLabel": item.get("pickLabel"),
        "selectedSide": item.get("selectedSide"),
        "selectedTeam": item.get("selectedTeam"),
        "line": item.get("line"),
        "modelProbability": item.get("modelProbability"),
        "confidenceScore": item.get("confidenceScore"),
        "decisionGrade": item.get("decisionGrade"),
        "fairMarketProbability": item.get("fairMarketProbability"),
        "referenceProbability": item.get("referenceProbability"),
        "edge": item.get("edge"),
        "evPct": item.get("evPct"),
        "kellyPct": item.get("kellyPct"),
        "bestBook": item.get("bestBook"),
        "bestPrice": best_price,
        "quoteAt": item.get("quoteAt"),
        "quoteAgeSeconds": item.get("quoteAgeSeconds"),
        "freshBookCount": item.get("freshBookCount"),
        "pairedFairBookCount": item.get("pairedFairBookCount"),
        "quoteStatus": item.get("quoteStatus"),
        "priceStatus": item.get("priceStatus"),
        "actionable": bool(item.get("actionable")),
        "reasons": list(item.get("reasons") or []),
        "risks": list(item.get("risks") or []),
        "hydratedAt": item.get("hydratedAt"),
        "sourceModelVersion": item.get("sourceModelVersion"),
        "publicationModelVersion": MODEL_VERSION,
        "publicationSource": "p4.3-game-decision-board",
    }
    key = _release_key(item)
    return {
        "receiptId": hashlib.sha256(key.encode("utf-8")).hexdigest()[:20],
        "releaseKey": key,
        "releaseFingerprint": _fingerprint(release),
        "release": release,
    }


def record_delivery(items: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Record first publication for upstream-actionable game recommendations."""
    candidates = [
        dict(item)
        for item in items
        if item.get("actionable") is True
        and item.get("quoteStatus") == "fresh"
        and item.get("market") in {"moneyline", "spread", "total"}
    ]
    inserted = existing = failed = 0
    ids: list[str] = []
    now = _now()
    try:
        for item in candidates:
            receipt = build_receipt(item)
            ids.append(receipt["receiptId"])
            prior = db.session.execute(
                sa.select(game_decision_ledger_receipts.c.receipt_id).where(
                    game_decision_ledger_receipts.c.release_key == receipt["releaseKey"]
                )
            ).scalar_one_or_none()
            if prior is not None:
                existing += 1
                continue
            release = receipt["release"]
            db.session.execute(
                game_decision_ledger_receipts.insert().values(
                    receipt_id=receipt["receiptId"],
                    release_key=receipt["releaseKey"],
                    season=release.get("season"),
                    week=release.get("week"),
                    season_type=str(release.get("seasonType") or "")[:8] or None,
                    game_id=str(release.get("gameId") or ""),
                    market_key=str(release.get("marketKey") or ""),
                    selected_side=str(release.get("selectedSide") or ""),
                    selected_team=(str(release.get("selectedTeam")) if release.get("selectedTeam") else None),
                    decision_grade=str(release.get("decisionGrade") or "Play"),
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
        failed = max(1, len(candidates) - inserted - existing) if candidates else 0
        _rollback()
        inserted = 0
    return {
        "candidates": len(candidates),
        "inserted": inserted,
        "existing": existing,
        "failed": failed,
        "receiptIds": ids,
    }


def publish_week_delivery(
    season: int, week: int, season_type: str = "REG", *, limit: int = 12
) -> dict[str, Any]:
    """Build P4.3 delivery and persist first-publication receipts for its picks."""
    delivery = p43.build_week_delivery(season, week, season_type, limit=limit)
    publication = record_delivery(delivery.get("picks") or [])
    out = dict(delivery)
    out["publication"] = {
        "ledger": MODEL_NAME,
        "candidates": publication["candidates"],
        "inserted": publication["inserted"],
        "existing": publication["existing"],
        "failed": publication["failed"],
    }
    return out


def grade_market_release(
    release: dict[str, Any], *, home_score: int, away_score: int
) -> tuple[str, float] | None:
    """Pure game-market grader used by production grading and regression tests."""
    market = str(release.get("marketKey") or "")
    side = str(release.get("selectedSide") or "").lower()
    home = str(release.get("homeTeam") or "")
    away = str(release.get("awayTeam") or "")
    selected_team = str(release.get("selectedTeam") or "")
    if market == "moneyline":
        if home_score == away_score:
            return ("push", float(home_score))
        winner_side = "home" if home_score > away_score else "away"
        winner_team = home if winner_side == "home" else away
        won = side == winner_side or (selected_team and selected_team == winner_team)
        return ("win" if won else "loss", float(home_score - away_score))

    try:
        line = float(release.get("line"))
    except (TypeError, ValueError):
        return None

    if market == "spread":
        if side not in {"home", "away"}:
            return None
        selected_score = home_score if side == "home" else away_score
        opponent_score = away_score if side == "home" else home_score
        adjusted = float(selected_score) + line - float(opponent_score)
        if abs(adjusted) < 1e-9:
            return ("push", adjusted)
        return ("win" if adjusted > 0 else "loss", adjusted)

    if market == "total":
        if side not in {"over", "under"}:
            return None
        total = float(home_score + away_score)
        if total == line:
            return ("push", total)
        over = total > line
        return ("win" if (side == "over") == over else "loss", total)
    return None


def _result_payload(
    release: dict[str, Any], grade: str, actual: float, home_score: int, away_score: int
) -> dict[str, Any]:
    probability = release.get("modelProbability")
    probability = float(probability) if isinstance(probability, (int, float)) else None
    binary = 1.0 if grade == "win" else (0.0 if grade == "loss" else None)
    brier = (
        round((probability - binary) ** 2, 6)
        if probability is not None and binary is not None
        else None
    )
    decimal = ve.american_to_decimal(release.get("bestPrice"))
    unit_profit = None
    if decimal is not None:
        unit_profit = (
            round(decimal - 1.0, 4)
            if grade == "win"
            else (-1.0 if grade == "loss" else 0.0)
        )
    return {
        "grade": grade,
        "actual": actual,
        "homeScore": int(home_score),
        "awayScore": int(away_score),
        "probability": probability,
        "brier": brier,
        "unitProfit": unit_profit,
    }


def grade_pending() -> dict[str, Any]:
    """Grade pending game receipts when the canonical schedule marks games final."""
    try:
        rows = db.session.execute(
            sa.select(
                game_decision_ledger_receipts.c.receipt_id,
                game_decision_ledger_receipts.c.season,
                game_decision_ledger_receipts.c.game_id,
                game_decision_ledger_receipts.c.release_payload,
            ).where(game_decision_ledger_receipts.c.grade == "pending")
        ).all()
    except (RuntimeError, SQLAlchemyError):
        _rollback()
        return {"available": False, "graded": 0, "pending": 0}
    if not rows:
        return {"available": True, "graded": 0, "pending": 0}

    games: dict[str, dict[str, Any]] = {}
    for season in {int(row.season) for row in rows if row.season is not None}:
        for game in nfl_data.get_schedule(season):
            if game.get("completed"):
                games[str(game.get("game_id"))] = game

    graded = 0
    now = _now()
    try:
        for row in rows:
            game = games.get(str(row.game_id))
            if not game:
                continue
            home_score = game.get("home_score")
            away_score = game.get("away_score")
            if not isinstance(home_score, int) or not isinstance(away_score, int):
                continue
            release = dict(row.release_payload or {})
            result = grade_market_release(
                release, home_score=home_score, away_score=away_score
            )
            if result is None:
                continue
            grade, actual = result
            db.session.execute(
                game_decision_ledger_receipts.update()
                .where(game_decision_ledger_receipts.c.receipt_id == row.receipt_id)
                .values(
                    grade=grade,
                    home_score=home_score,
                    away_score=away_score,
                    result_payload=_result_payload(
                        release, grade, actual, home_score, away_score
                    ),
                    graded_at=now,
                    updated_at=now,
                )
            )
            graded += 1
        db.session.commit()
    except (RuntimeError, SQLAlchemyError):
        _rollback()
        return {"available": False, "graded": 0, "pending": len(rows)}
    return {"available": True, "graded": graded, "pending": len(rows) - graded}


def _public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "receiptId": row.get("receipt_id"),
        "releasedAt": row.get("released_at").isoformat() if row.get("released_at") else None,
        "releaseFingerprint": row.get("release_fingerprint"),
        "grade": row.get("grade"),
        "gradedAt": row.get("graded_at").isoformat() if row.get("graded_at") else None,
        "release": dict(row.get("release_payload") or {}),
        "result": dict(row.get("result_payload") or {}),
    }


def list_receipts(
    *, limit: int = 500, season: int | None = None, week: int | None = None
) -> list[dict[str, Any]]:
    stmt = sa.select(game_decision_ledger_receipts)
    if season is not None:
        stmt = stmt.where(game_decision_ledger_receipts.c.season == int(season))
    if week is not None:
        stmt = stmt.where(game_decision_ledger_receipts.c.week == int(week))
    stmt = stmt.order_by(game_decision_ledger_receipts.c.released_at.desc()).limit(
        max(1, min(int(limit), 2000))
    )
    try:
        rows = db.session.execute(stmt).mappings().all()
    except (RuntimeError, SQLAlchemyError):
        _rollback()
        return []
    return [_public(dict(row)) for row in rows]


def performance_summary() -> dict[str, Any]:
    receipts = list_receipts(limit=2000)
    graded = [row for row in receipts if row.get("grade") in {"win", "loss", "push"}]
    wins = sum(row.get("grade") == "win" for row in graded)
    losses = sum(row.get("grade") == "loss" for row in graded)
    pushes = sum(row.get("grade") == "push" for row in graded)
    unit_profits = [
        float(row["result"]["unitProfit"])
        for row in graded
        if isinstance((row.get("result") or {}).get("unitProfit"), (int, float))
    ]
    briers = [
        float(row["result"]["brier"])
        for row in graded
        if isinstance((row.get("result") or {}).get("brier"), (int, float))
    ]
    per_market: dict[str, dict[str, int]] = {}
    for row in graded:
        market = str((row.get("release") or {}).get("marketKey") or "unknown")
        bucket = per_market.setdefault(
            market, {"n": 0, "wins": 0, "losses": 0, "pushes": 0}
        )
        bucket["n"] += 1
        if row["grade"] == "win":
            bucket["wins"] += 1
        elif row["grade"] == "loss":
            bucket["losses"] += 1
        else:
            bucket["pushes"] += 1
    decided = wins + losses
    return {
        "available": True,
        "model": MODEL_NAME,
        "receipts": len(receipts),
        "pending": len(receipts) - len(graded),
        "graded": len(graded),
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "hitRate": round(wins / decided, 4) if decided else None,
        "brier": round(sum(briers) / len(briers), 6) if briers else None,
        "pricedGraded": len(unit_profits),
        "unitProfit": round(sum(unit_profits), 4) if unit_profits else None,
        "unitRoi": round(sum(unit_profits) / len(unit_profits), 4) if unit_profits else None,
        "perMarket": dict(sorted(per_market.items())),
    }


def ledger_status() -> dict[str, Any]:
    try:
        rows = db.session.execute(
            sa.select(
                game_decision_ledger_receipts.c.grade,
                game_decision_ledger_receipts.c.released_at,
            )
        ).all()
    except (RuntimeError, SQLAlchemyError):
        _rollback()
        return {"available": False, "receipts": 0, "pending": 0, "graded": 0}
    latest = max((row.released_at for row in rows if row.released_at), default=None)
    return {
        "available": True,
        "backend": "database",
        "receipts": len(rows),
        "pending": sum(row.grade == "pending" for row in rows),
        "graded": sum(row.grade in {"win", "loss", "push"} for row in rows),
        "latestReleasedAt": latest.isoformat() if latest else None,
        "isolatedFromPlayerPropLedger": True,
    }
