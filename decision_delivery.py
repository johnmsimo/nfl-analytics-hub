"""P3.5 deterministic decision delivery for Quick Props and dashboard surfaces.

P3.4 decides the model grade. P3.5 is deliberately a delivery layer: it never
upgrades a Pass into a pick, never treats an unpriced model pick as a wager,
and always returns a terminal product state instead of an ambiguous ranking
placeholder. P3.6 extends the price tie-breaker so stale quotes are treated like
unpriced rows and can never gain ranking priority merely because an old number
exists.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

_PICK_GRADES = ("Strong Play", "Play", "Lean")
_GRADE_ORDER = {"Strong Play": 0, "Play": 1, "Lean": 2, "Pass": 3}


def _score(row: dict[str, Any]) -> float:
    value = row.get("decisionScore")
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _edge(row: dict[str, Any]) -> float:
    value = row.get("edge")
    try:
        return float(value)
    except (TypeError, ValueError):
        return -1.0


def _usable_price(row: dict[str, Any]) -> bool:
    price_status = str(row.get("priceStatus") or "unpriced")
    quote_status = row.get("quoteStatus")
    if price_status in {"unpriced", "stale"}:
        return False
    # Pre-P3.6 callers do not carry quoteStatus; preserve their historical
    # ordering while P3.6 rows must explicitly be fresh.
    return quote_status in {None, "fresh"}


def _sort_key(row: dict[str, Any]) -> tuple:
    return (
        _GRADE_ORDER.get(str(row.get("decisionGrade") or "Pass"), 9),
        -_score(row),
        not _usable_price(row),
        -_edge(row),
        str(row.get("player") or ""),
        str(row.get("marketKey") or ""),
    )


def sort_decisions(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return canonical order: decision quality first, usable price second."""
    return sorted(rows, key=_sort_key)


def build_delivery(
    rows: Iterable[dict[str, Any]],
    *,
    limit: int = 8,
    game_errors: int = 0,
    expected_games: int | None = None,
) -> dict[str, Any]:
    """Build one terminal delivery contract without inventing model picks."""
    ordered = sort_decisions(rows)
    picks = [row for row in ordered if row.get("decisionGrade") in _PICK_GRADES]
    passes = [row for row in ordered if row.get("decisionGrade") == "Pass"]
    safe_limit = max(1, min(int(limit), 25))
    delivered = picks[:safe_limit]
    watchlist = passes[:safe_limit] if not delivered else []

    if delivered and game_errors:
        state = "partial"
        message = "Model picks are ready, but one or more games could not be evaluated."
    elif delivered:
        state = "ready"
        message = "Model picks are ready."
    elif ordered and game_errors:
        state = "partial"
        message = "No Lean-or-better picks cleared the model gate; best remaining rows are watchlist only and some games degraded."
    elif ordered:
        state = "watchlist"
        message = "No Lean-or-better picks cleared the model gate. Showing the strongest Pass rows as watchlist only."
    elif game_errors:
        state = "degraded"
        message = "Decision delivery failed for one or more games and produced no usable rows."
    else:
        state = "empty"
        message = "No projectable player-market rows are available for this selection."

    grades = Counter(str(row.get("decisionGrade") or "Pass") for row in ordered)
    priced = sum(row.get("priceStatus") != "unpriced" for row in delivered)
    fresh_priced = sum(_usable_price(row) for row in delivered)
    actionable = sum(bool(row.get("actionable")) for row in delivered)
    return {
        "state": state,
        "terminal": True,
        "message": message,
        "picks": delivered,
        "watchlist": watchlist,
        "summary": {
            "rows": len(ordered),
            "strongPlays": grades.get("Strong Play", 0),
            "plays": grades.get("Play", 0),
            "leans": grades.get("Lean", 0),
            "passes": grades.get("Pass", 0),
            "leanOrBetter": len(picks),
            "delivered": len(delivered),
            "pricedDelivered": priced,
            "freshPricedDelivered": fresh_priced,
            "actionableDelivered": actionable,
            "gameErrors": int(game_errors),
            "expectedGames": expected_games,
        },
        "ranking": "decision grade, decision score, then fresh available price value",
        "modelVersion": "p3.5-decision-delivery",
    }


def verify_delivery_contract(payload: dict[str, Any]) -> dict[str, Any]:
    """Return structural gates used by the protected P3.5/P3.6 checks."""
    state = str(payload.get("state") or "")
    picks = list(payload.get("picks") or [])
    watchlist = list(payload.get("watchlist") or [])
    terminal = payload.get("terminal") is True
    picks_are_non_pass = all(row.get("decisionGrade") in _PICK_GRADES for row in picks)
    watchlist_are_pass = all(row.get("decisionGrade") == "Pass" for row in watchlist)
    ordered = picks or watchlist
    canonical_order = ordered == sort_decisions(ordered)
    price_integrity = all(
        not row.get("actionable")
        or (
            row.get("priceStatus") == "positive_value"
            and row.get("quoteStatus") in {None, "fresh"}
        )
        for row in picks
    )
    valid_state = state in {"ready", "partial", "watchlist", "empty", "degraded"}
    gates = {
        "terminal_state": terminal and valid_state,
        "pick_grade_integrity": picks_are_non_pass,
        "watchlist_integrity": watchlist_are_pass,
        "decision_ordering": canonical_order,
        "price_actionability_integrity": price_integrity,
    }
    return {"gates": gates, "ok": all(gates.values())}
