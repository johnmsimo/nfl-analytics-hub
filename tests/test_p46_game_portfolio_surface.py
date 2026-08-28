from __future__ import annotations

from pathlib import Path


def test_games_surface_uses_p46_portfolio_endpoint():
    root = Path(__file__).resolve().parents[1]
    games = (root / "games.html").read_text(encoding="utf-8")
    assert "P4.6 bankroll-aware" in games
    assert "/api/game-portfolio/week" in games
    assert "P4.6 stake" in games
