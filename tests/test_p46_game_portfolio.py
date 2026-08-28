from __future__ import annotations

import p46_game_portfolio as p46


def _item(
    game_id: str,
    market: str = "moneyline",
    *,
    state: str = "ACTIONABLE",
    grade: str = "Play",
    kelly: float | None = 0.20,
    ev: float | None = 0.10,
    edge: float | None = 0.08,
    price: int | None = -110,
) -> dict:
    actionable = state == "ACTIONABLE"
    return {
        "gameId": game_id,
        "market": market,
        "selectedSide": "home",
        "selectedTeam": f"{game_id}-HOME",
        "pickLabel": f"{game_id}-HOME ML",
        "opportunityState": state,
        "actionable": actionable,
        "quoteStatus": "fresh" if state in {"ACTIONABLE", "WATCH"} else "unpriced",
        "priceStatus": "positive_value" if actionable else "unpriced",
        "decisionGrade": grade,
        "confidenceScore": 80.0,
        "modelProbability": 0.65,
        "fairMarketProbability": 0.55 if actionable else None,
        "edge": edge,
        "evPct": ev,
        "kellyPct": kelly,
        "bestBook": "Book A" if actionable else None,
        "bestPrice": price if actionable else None,
    }


def _board(items: list[dict]) -> dict:
    return {
        "available": True,
        "modelVersion": "p45-refresh-v1",
        "season": 2026,
        "seasonType": "REG",
        "week": 1,
        "summary": {
            "visibleOpportunities": sum(1 for row in items if row["opportunityState"] != "PASS"),
            "actionableOpportunities": sum(1 for row in items if row["opportunityState"] == "ACTIONABLE"),
        },
        "allOpportunities": items,
    }


def _settings(**overrides) -> dict:
    out = {
        "bankroll": 1000.0,
        "kelly_fraction": 1.0,
        "max_bet_pct": 0.05,
        "unit_pct": 0.01,
    }
    out.update(overrides)
    return out


def test_only_upstream_actionable_markets_receive_stakes():
    report = p46.build_portfolio_from_opportunities(
        _board([
            _item("g1"),
            _item("g2", state="WATCH"),
            _item("g3", state="MODEL", grade="Lean", kelly=None, ev=None, edge=None, price=None),
        ]),
        settings=_settings(),
    )
    assert len(report["portfolio"]) == 1
    assert report["portfolio"][0]["gameId"] == "g1"
    assert report["portfolio"][0]["recommendedStakeDollars"] > 0
    assert all(row["recommendedStakeDollars"] == 0 for row in report["context"])
    assert report["safety"]["automaticBetPlacement"] is False
    assert report["safety"]["providerIo"] is False


def test_per_bet_game_and_slate_caps_are_respected(monkeypatch):
    monkeypatch.setenv("P46_MAX_GAME_EXPOSURE_PCT", "0.075")
    monkeypatch.setenv("P46_MAX_SLATE_EXPOSURE_PCT", "0.10")
    monkeypatch.setenv("P46_MAX_PORTFOLIO_PICKS", "8")
    items = [
        _item("g1", "moneyline", grade="Strong Play"),
        _item("g1", "spread", grade="Play"),
        _item("g1", "total", grade="Play"),
        _item("g2", "moneyline", grade="Play"),
    ]
    report = p46.build_portfolio_from_opportunities(_board(items), settings=_settings())
    assert report["summary"]["allocatedStakeDollars"] <= 100.0
    assert report["summary"]["perGameExposureDollars"]["g1"] <= 75.0
    assert all(row["recommendedStakePct"] <= 0.05 for row in report["portfolio"])
    assert p46.verify_portfolio(report)["ok"] is True


def test_fractional_kelly_scales_stake_before_caps():
    report = p46.build_portfolio_from_opportunities(
        _board([_item("g1", kelly=0.08)]),
        settings=_settings(kelly_fraction=0.25, max_bet_pct=0.05),
    )
    row = report["portfolio"][0]
    assert row["requestedStakePct"] == 0.02
    assert row["recommendedStakeDollars"] == 20.0
    assert row["recommendedStakeUnits"] == 2.0


def test_zero_kelly_fraction_disables_recommended_staking():
    report = p46.build_portfolio_from_opportunities(
        _board([_item("g1", kelly=0.20)]),
        settings=_settings(kelly_fraction=0.0),
    )
    assert report["portfolio"] == []
    assert len(report["alternates"]) == 1
    assert report["alternates"][0]["recommendedStakeDollars"] == 0.0


def test_no_actionable_markets_returns_zero_stake_context():
    report = p46.build_portfolio_from_opportunities(
        _board([
            _item("g1", state="WATCH"),
            _item("g2", state="MODEL", grade="Lean", kelly=None, ev=None, edge=None, price=None),
        ]),
        settings=_settings(),
    )
    assert report["state"] == "no-actionable-portfolio"
    assert report["portfolio"] == []
    assert report["summary"]["allocatedStakeDollars"] == 0.0
    assert all(row["recommendedStakeDollars"] == 0.0 for row in report["context"])


def test_build_week_portfolio_consumes_p45_cache_only_board(monkeypatch):
    calls = []
    board = _board([_item("g1")])

    def _opportunities(season, week, season_type):
        calls.append((season, week, season_type))
        return board

    monkeypatch.setattr(p46.p45, "build_week_opportunities", _opportunities)
    report = p46.build_week_portfolio(2026, 1, "reg", settings=_settings())
    assert calls == [(2026, 1, "REG")]
    assert report["safety"]["cacheOnly"] is True
    assert report["portfolio"][0]["gameId"] == "g1"
