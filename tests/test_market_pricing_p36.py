from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import market_pricing as mp
import odds_api

ROOT = Path(__file__).resolve().parents[1]


def _row(book: str, side: str, price: int, at: datetime, *, line: float = 100.5) -> dict:
    return {
        "book": book,
        "book_key": book.lower(),
        "side": side,
        "price": price,
        "line": line,
        "market_last_update": at.isoformat(),
        "fetched_at": at.timestamp(),
    }


def test_fresh_multibook_market_selects_best_price_and_devigs_consensus(monkeypatch):
    monkeypatch.setattr(mp, "ACTIONABLE_MAX_AGE_SECONDS", 900.0)
    now = datetime(2026, 8, 28, 2, 0, tzinfo=UTC)
    at = now - timedelta(seconds=60)
    rows = [
        _row("Book A", "over", -110, at),
        _row("Book A", "under", -110, at),
        _row("Book B", "over", 105, at),
        _row("Book B", "under", -125, at),
    ]

    result = mp.assess_market(rows, side="over", model_probability=0.60, now=now)

    assert result["quoteStatus"] == "fresh"
    assert result["bestPrice"]["book"] == "Book B"
    assert result["bestPrice"]["price"] == 105
    assert result["freshBookCount"] == 2
    assert result["pairedFairBookCount"] == 2
    assert result["fairMarketProbability"] is not None
    assert result["edge"] > 0.05
    assert result["evPct"] > 0.10
    assert result["priceStatus"] == "positive_value"
    assert result["actionableValue"] is True


def test_stale_price_is_visible_but_never_actionable(monkeypatch):
    monkeypatch.setattr(mp, "ACTIONABLE_MAX_AGE_SECONDS", 900.0)
    now = datetime(2026, 8, 28, 2, 0, tzinfo=UTC)
    at = now - timedelta(seconds=1200)
    rows = [
        _row("Book A", "over", 120, at),
        _row("Book A", "under", -140, at),
    ]

    result = mp.assess_market(rows, side="over", model_probability=0.70, now=now)

    assert result["bestPrice"]["price"] == 120
    assert result["quoteStatus"] == "stale"
    assert result["priceStatus"] == "stale"
    assert result["actionableValue"] is False
    assert mp.apply_model_actionability("Strong Play", result) is False


def test_lean_never_becomes_actionable_even_with_positive_fresh_price(monkeypatch):
    monkeypatch.setattr(mp, "ACTIONABLE_MAX_AGE_SECONDS", 900.0)
    now = datetime(2026, 8, 28, 2, 0, tzinfo=UTC)
    at = now - timedelta(seconds=30)
    rows = [
        _row("Book A", "over", 110, at),
        _row("Book A", "under", -130, at),
    ]
    pricing = mp.assess_market(rows, side="over", model_probability=0.65, now=now)

    assert pricing["actionableValue"] is True
    assert mp.apply_model_actionability("Lean", pricing) is False
    assert mp.apply_model_actionability("Play", pricing) is True


def test_missing_timestamp_fails_closed():
    rows = [
        {"book": "Book A", "book_key": "a", "side": "over", "price": 110, "line": 50.5},
        {"book": "Book A", "book_key": "a", "side": "under", "price": -130, "line": 50.5},
    ]
    result = mp.assess_market(rows, side="over", model_probability=0.65)

    assert result["quoteStatus"] == "stale"
    assert result["actionableValue"] is False
    assert result["bestPrice"]["quoteAt"] is None


def test_odds_parser_preserves_provider_and_snapshot_timestamps():
    payload = {
        "bookmakers": [
            {
                "key": "book-a",
                "title": "Book A",
                "last_update": "2026-08-28T01:58:00Z",
                "markets": [
                    {
                        "key": "player_pass_yds",
                        "last_update": "2026-08-28T01:59:00Z",
                        "outcomes": [
                            {"name": "Over", "description": "Player One", "point": 250.5, "price": -110},
                            {"name": "Under", "description": "Player One", "point": 250.5, "price": -110},
                        ],
                    }
                ],
            }
        ]
    }
    rows = odds_api.parse_prop_markets(payload, fetched_at=1770000000.0)

    assert len(rows) == 2
    assert rows[0]["book_key"] == "book-a"
    assert rows[0]["book_last_update"] == "2026-08-28T01:58:00Z"
    assert rows[0]["market_last_update"] == "2026-08-28T01:59:00Z"
    assert rows[0]["fetched_at"] == 1770000000.0


def test_price_contract_rejects_stale_actionable_rows():
    payload = [
        {
            "decisionGrade": "Strong Play",
            "priceStatus": "positive_value",
            "quoteStatus": "stale",
            "actionable": True,
            "bestPrice": {"quoteAt": "2026-08-28T01:59:00+00:00", "quoteAgeSeconds": 1000},
        }
    ]
    result = mp.verify_price_contract(payload)

    assert result["gates"]["stale_quotes_fail_closed"] is False
    assert result["ok"] is False


def test_props_route_uses_p36_pricing_and_cache_only_verification_path():
    source = (ROOT / "routes" / "props.py").read_text(encoding="utf-8")

    assert "import market_pricing as mp" in source
    assert "mp.assess_market(" in source
    assert "cache_only_odds" in source
    assert '"/api/market-pricing/refresh"' in source
    assert '"/api/market-pricing/status"' in source
    assert '"p3.6-live-market-actionability"' in source


def test_p36_workflow_makes_credit_spend_explicit_and_keeps_cache_only_option():
    workflow = (
        ROOT / ".github" / "workflows" / "p36-market-pricing-verification.yml"
    ).read_text(encoding="utf-8")

    assert "RUN_CACHE_ONLY_VERIFY" in workflow
    assert "RUN_ONE_EVENT_PRICE_REFRESH_VERIFY" in workflow
    assert "P36_REFRESH_MODE=$MODE" in workflow
    assert "/app/scripts/p36_market_pricing_verification.py" in workflow
    assert "/app/scripts/p2_exit_verification.py" in workflow
