from __future__ import annotations

from pathlib import Path

import decision_delivery as dd

ROOT = Path(__file__).resolve().parents[1]


def _row(
    grade: str,
    score: float,
    *,
    player: str = "Player",
    edge: float | None = None,
    price_status: str = "unpriced",
    actionable: bool = False,
):
    return {
        "decisionGrade": grade,
        "decisionScore": score,
        "player": player,
        "marketKey": "rec_yds",
        "edge": edge,
        "priceStatus": price_status,
        "actionable": actionable,
    }


def test_delivery_never_promotes_pass_rows_into_picks():
    payload = dd.build_delivery(
        [_row("Pass", 0.71, player="A"), _row("Pass", 0.66, player="B")],
        limit=4,
    )

    assert payload["state"] == "watchlist"
    assert payload["terminal"] is True
    assert payload["picks"] == []
    assert len(payload["watchlist"]) == 2
    assert all(row["decisionGrade"] == "Pass" for row in payload["watchlist"])


def test_lean_or_better_is_delivered_before_price_value():
    rows = [
        _row("Lean", 0.53, player="Lean", edge=None),
        _row("Pass", 0.80, player="Pass", edge=0.15, price_status="positive_value"),
        _row("Play", 0.61, player="Play", edge=None),
        _row("Strong Play", 0.70, player="Strong", edge=0.03, price_status="positive_value"),
    ]
    payload = dd.build_delivery(rows, limit=8)

    assert payload["state"] == "ready"
    assert [row["player"] for row in payload["picks"]] == ["Strong", "Play", "Lean"]
    assert payload["summary"]["passes"] == 1
    assert dd.verify_delivery_contract(payload)["gates"]["decision_ordering"] is True


def test_grade_first_ordering_is_valid_even_when_later_grade_has_higher_score():
    payload = dd.build_delivery(
        [
            _row("Play", 0.74, player="HigherScorePlay"),
            _row("Strong Play", 0.63, player="StrongFirst"),
            _row("Lean", 0.80, player="HighScoreLean"),
        ],
        limit=8,
    )

    assert [row["player"] for row in payload["picks"]] == [
        "StrongFirst",
        "HigherScorePlay",
        "HighScoreLean",
    ]
    assert dd.verify_delivery_contract(payload)["ok"] is True


def test_partial_state_is_explicit_when_some_games_fail():
    payload = dd.build_delivery([_row("Play", 0.61)], limit=8, game_errors=1, expected_games=16)

    assert payload["state"] == "partial"
    assert payload["terminal"] is True
    assert payload["summary"]["gameErrors"] == 1
    assert payload["summary"]["expectedGames"] == 16


def test_actionability_requires_positive_value_price_status():
    invalid = dd.build_delivery(
        [_row("Play", 0.62, price_status="unpriced", actionable=True)],
        limit=8,
    )
    valid = dd.build_delivery(
        [_row("Play", 0.62, price_status="positive_value", actionable=True)],
        limit=8,
    )

    assert dd.verify_delivery_contract(invalid)["gates"]["price_actionability_integrity"] is False
    assert dd.verify_delivery_contract(valid)["ok"] is True


def test_quick_props_and_my_hub_are_wired_to_p35_contract():
    props = (ROOT / "routes" / "props.py").read_text(encoding="utf-8")
    dashboard = (ROOT / "routes" / "dashboard_api.py").read_text(encoding="utf-8")
    page = (ROOT / "dashboard.html").read_text(encoding="utf-8")

    assert '/api/quick-props/week' in props
    assert 'include_odds=False' in (ROOT / "p35_verification.py").read_text(encoding="utf-8")
    assert '/api/my-hub' in dashboard
    assert 'dd.sort_decisions' in dashboard
    assert 'quick_props' in dashboard
    assert 'WATCHLIST ONLY' in page
    assert 'setTimeout(()=>controller.abort(),15000)' in page
    assert '/api/my-hub?' in page


def test_p35_workflow_is_read_only_and_avoids_provider_calls():
    workflow = (
        ROOT / ".github" / "workflows" / "p35-decision-delivery-verification.yml"
    ).read_text(encoding="utf-8")

    assert "RUN_DECISION_DELIVERY_VERIFY" in workflow
    assert "environment: production" in workflow
    assert "/app/scripts/p35_decision_delivery_verification.py" in workflow
    assert "/app/scripts/p2_exit_verification.py" in workflow
    assert "ODDS_API" not in workflow
    assert "sync_commercial" not in workflow
