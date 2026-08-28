"""P3.6 live player-prop pricing and actionability rules.

The model decision and the sportsbook market are deliberately separate. P3.6
may make a P3.4/P3.5 model pick actionable only when a real quote is fresh and
its current price offers positive value. Stale prices remain visible for
context but can never authorize an actionable wager.
"""
from __future__ import annotations

import math
import os
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any, Iterable

import value_engine as ve


def _positive_float_env(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(value, 0.0)


ACTIONABLE_MAX_AGE_SECONDS = _positive_float_env("NFL_MARKET_ACTIONABLE_MAX_AGE_SEC", 900.0)
DISPLAY_MAX_AGE_SECONDS = _positive_float_env("NFL_MARKET_DISPLAY_MAX_AGE_SEC", 21600.0)
MIN_EDGE = _positive_float_env("NFL_MARKET_MIN_EDGE", 0.015)
MIN_EV = _positive_float_env("NFL_MARKET_MIN_EV", 0.02)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _as_utc(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value), UTC)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def provider_update_timestamp(row: dict[str, Any]) -> datetime | None:
    """Return the provider's most specific last-change timestamp for audit context."""
    for key in ("market_last_update", "book_last_update", "quote_at"):
        parsed = _as_utc(row.get(key))
        if parsed is not None:
            return parsed
    return None


def quote_timestamp(row: dict[str, Any]) -> datetime | None:
    """Return when this quote was actually observed by our provider snapshot.

    Actionability freshness is about how recently we verified the currently
    returned quote, not how recently the sportsbook changed the number. A line
    can remain unchanged for an hour and still be a current quote if it was
    fetched seconds ago. Provider update timestamps remain available separately
    through :func:`provider_update_timestamp` for audit/context.

    Rows created by older integrations may not carry ``fetched_at``; those fall
    back to their provider timestamp and therefore still fail closed as they age.
    """
    for key in ("fetched_at", "quote_at", "market_last_update", "book_last_update"):
        parsed = _as_utc(row.get(key))
        if parsed is not None:
            return parsed
    return None


def quote_age_seconds(row: dict[str, Any], now: datetime | None = None) -> float | None:
    at = quote_timestamp(row)
    if at is None:
        return None
    current = (now or datetime.now(UTC)).astimezone(UTC)
    return round(max(0.0, (current - at).total_seconds()), 1)


def quote_freshness(row: dict[str, Any], now: datetime | None = None) -> str:
    age = quote_age_seconds(row, now)
    if age is None:
        return "unknown"
    if age <= ACTIONABLE_MAX_AGE_SECONDS:
        return "fresh"
    if age <= DISPLAY_MAX_AGE_SECONDS:
        return "stale"
    return "expired"


def _quote_view(row: dict[str, Any], now: datetime) -> dict[str, Any]:
    observed_at = quote_timestamp(row)
    provider_updated_at = provider_update_timestamp(row)
    age = quote_age_seconds(row, now)
    freshness = quote_freshness(row, now)
    expires_at = None
    expires_in = None
    if observed_at is not None:
        expires_at = (observed_at + timedelta(seconds=ACTIONABLE_MAX_AGE_SECONDS)).isoformat()
        expires_in = round(ACTIONABLE_MAX_AGE_SECONDS - (age or 0.0), 1)
    provider_update_age = None
    if provider_updated_at is not None:
        provider_update_age = round(max(0.0, (now - provider_updated_at).total_seconds()), 1)
    return {
        "book": row.get("book"),
        "bookKey": row.get("book_key"),
        "price": row.get("price"),
        "line": row.get("line"),
        "quoteAt": observed_at.isoformat() if observed_at is not None else None,
        "quoteAgeSeconds": age,
        "quoteFreshness": freshness,
        "providerUpdatedAt": provider_updated_at.isoformat() if provider_updated_at else None,
        "providerUpdateAgeSeconds": provider_update_age,
        "expiresAt": expires_at,
        "expiresInSeconds": expires_in,
    }


def _best(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    priced = [row for row in rows if ve.american_to_decimal(row.get("price")) is not None]
    if not priced:
        return None
    return max(priced, key=lambda row: ve.american_to_decimal(row.get("price")) or 0.0)


def _fair_probabilities(rows: list[dict[str, Any]], side: str, now: datetime) -> list[float]:
    """Same-book, same-line de-vig probabilities using only fresh observations."""
    by_book: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        if quote_freshness(row, now) != "fresh":
            continue
        book = str(row.get("book_key") or row.get("book") or "")
        row_side = str(row.get("side") or "").lower()
        if not book or row_side not in {"over", "under"}:
            continue
        by_book.setdefault(book, {})[row_side] = row

    values: list[float] = []
    for pair in by_book.values():
        over = pair.get("over")
        under = pair.get("under")
        if not over or not under:
            continue
        probs = ve.devig_two_way(over.get("price"), under.get("price"), method="multiplicative")
        if probs is None:
            continue
        values.append(float(probs[0] if side == "over" else probs[1]))
    return values


def assess_market(
    rows: Iterable[dict[str, Any]],
    *,
    side: str,
    model_probability: float,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return P3.6 price intelligence for one offered player-prop line.

    The returned ``actionableValue`` expresses price quality only. The caller
    must additionally require a model decision grade of Strong Play/Play before
    setting the product-level ``actionable`` flag.
    """
    current = (now or datetime.now(UTC)).astimezone(UTC)
    chosen_side = "under" if str(side).lower() == "under" else "over"
    probability = max(0.0, min(1.0, float(model_probability)))
    market_rows = [dict(row) for row in rows]
    side_rows = [
        row
        for row in market_rows
        if str(row.get("side") or "").lower() == chosen_side
        and ve.american_to_decimal(row.get("price")) is not None
    ]
    fresh_rows = [row for row in side_rows if quote_freshness(row, current) == "fresh"]
    display_rows = [
        row
        for row in side_rows
        if quote_freshness(row, current) in {"fresh", "stale", "unknown"}
    ]
    best_fresh = _best(fresh_rows)
    best_display = best_fresh or _best(display_rows)
    best = _quote_view(best_display, current) if best_display is not None else None

    fair_values = _fair_probabilities(market_rows, chosen_side, current)
    fair_probability = median(fair_values) if fair_values else None
    implied_probability = (
        ve.american_to_implied(best_display.get("price")) if best_display is not None else None
    )
    reference_probability = fair_probability if fair_probability is not None else implied_probability
    edge = probability - reference_probability if reference_probability is not None else None
    ev = (
        ve.expected_value(probability, best_display.get("price"))
        if best_display is not None
        else None
    )
    kelly = (
        ve.kelly_stake(probability, best_display.get("price"))["stake_pct"]
        if best_display is not None
        else None
    )

    fresh_books = {
        str(row.get("book_key") or row.get("book"))
        for row in fresh_rows
        if row.get("book_key") or row.get("book")
    }
    quoted_books = {
        str(row.get("book_key") or row.get("book"))
        for row in side_rows
        if row.get("book_key") or row.get("book")
    }

    if best_display is None:
        quote_status = "unpriced"
        price_status = "unpriced"
    elif best_fresh is None:
        quote_status = "stale"
        price_status = "stale"
    else:
        quote_status = "fresh"
        if ev is not None and edge is not None and ev >= MIN_EV and edge >= MIN_EDGE:
            price_status = "positive_value"
        elif ev is not None and ev > 0:
            price_status = "thin_value"
        else:
            price_status = "no_value"

    actionable_value = bool(
        best_fresh is not None
        and edge is not None
        and ev is not None
        and edge >= MIN_EDGE
        and ev >= MIN_EV
    )
    return {
        "side": chosen_side,
        "quoteStatus": quote_status,
        "priceStatus": price_status,
        "bestPrice": best,
        "quotedBookCount": len(quoted_books),
        "freshBookCount": len(fresh_books),
        "pairedFairBookCount": len(fair_values),
        "fairMarketProbability": round(fair_probability, 4) if fair_probability is not None else None,
        "impliedProbability": round(implied_probability, 4) if implied_probability is not None else None,
        "referenceProbability": round(reference_probability, 4) if reference_probability is not None else None,
        "edge": round(edge, 4) if edge is not None else None,
        "evPct": round(ev, 4) if ev is not None else None,
        "kellyPct": round(float(kelly), 4) if kelly is not None else None,
        "actionableValue": actionable_value,
        "thresholds": {
            "maximumQuoteAgeSeconds": ACTIONABLE_MAX_AGE_SECONDS,
            "maximumDisplayAgeSeconds": DISPLAY_MAX_AGE_SECONDS,
            "minimumEdge": MIN_EDGE,
            "minimumEv": MIN_EV,
        },
    }


def apply_model_actionability(decision_grade: str, pricing: dict[str, Any]) -> bool:
    """Only Strong Play/Play + fresh positive-value pricing becomes actionable."""
    return str(decision_grade) in {"Strong Play", "Play"} and bool(pricing.get("actionableValue"))


def verify_price_contract(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Structural gates for P3.6 cache-only production verification."""
    items = list(rows)
    priced = [row for row in items if row.get("priceStatus") not in {None, "unpriced"}]
    fresh = [row for row in items if row.get("quoteStatus") == "fresh"]
    actionable = [row for row in items if row.get("actionable")]
    stale_actionable = [row for row in actionable if row.get("quoteStatus") != "fresh"]
    invalid_actionable = [
        row
        for row in actionable
        if row.get("decisionGrade") not in {"Strong Play", "Play"}
        or row.get("priceStatus") != "positive_value"
    ]
    timestamped = [
        row
        for row in priced
        if (row.get("bestPrice") or {}).get("quoteAt") is not None
        and (row.get("bestPrice") or {}).get("quoteAgeSeconds") is not None
    ]
    gates = {
        "timestamp_integrity": len(timestamped) == len(priced),
        "stale_quotes_fail_closed": not stale_actionable,
        "model_price_actionability_integrity": not invalid_actionable,
        "bounded_actionability": len(actionable) <= len(fresh),
    }
    return {
        "rows": len(items),
        "pricedRows": len(priced),
        "freshRows": len(fresh),
        "actionableRows": len(actionable),
        "gates": gates,
        "ok": all(gates.values()),
    }
