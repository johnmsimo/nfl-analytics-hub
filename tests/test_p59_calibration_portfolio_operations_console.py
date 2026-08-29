from __future__ import annotations


def _html(client) -> str:
    response = client.get("/static/p59_calibration_portfolio_operations.html")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def test_p59_exposes_unified_portfolio_console(client):
    html = _html(client)
    assert "P5.9 · Calibration Portfolio Operations Console" in html
    assert "/api/game-calibration/portfolio-control-plane" in html
    assert "moneyline" in html
    assert "spread" in html
    assert "total" in html
    assert "P5.2" in html
    assert "P5.6" in html


def test_p59_console_is_read_only_on_load_and_refresh(client):
    html = _html(client)
    assert "No promotion or rollback runs on page load or refresh." in html
    assert "await loadPortfolio();" in html
    assert "$('#portfolio-refresh').onclick=loadPortfolio;" in html
    assert "runPortfolioMutation(market,'promotion')" in html
    assert "runPortfolioMutation(market,'rollback')" in html
    # No mutating endpoint is hard-coded in the portfolio console.
    assert "/api/game-calibration/promote" not in html
    assert "/api/game-calibration/rollback" not in html
    assert "/api/game-market-calibration/promote" not in html
    assert "/api/game-market-calibration/rollback" not in html
    assert "command.endpoint" in html


def test_p59_all_mutation_buttons_are_disabled_by_default(client):
    html = _html(client)
    assert 'id="${market}-promote-btn" type="button" disabled' in html
    assert 'id="${market}-rollback-btn" type="button" disabled' in html
    assert "owner&&row.promoteReady===true" in html
    assert "owner&&row.rollbackReady===true" in html
    assert "role()==='owner'" in html


def test_p59_requires_delegated_exact_confirmation_and_second_human_confirmation(client):
    html = _html(client)
    assert "input.value!==command.confirmation" in html
    assert "if(!confirm(prompt))return" in html
    assert "!command.endpoint||!command.confirmation" in html
    assert "Delegated command contract is unavailable" in html


def test_p59_moneyline_and_market_mutation_bodies_remain_correctly_scoped(client):
    html = _html(client)
    assert "market==='moneyline'?{candidateId:candidate,confirmation:command.confirmation}:{market,candidateId:candidate,confirmation:command.confirmation}" in html
    assert "market==='moneyline'?{confirmation:command.confirmation}:{market,confirmation:command.confirmation}" in html
    assert "portfolio?.markets?.[market]" in html


def test_p59_rollback_priority_and_market_isolation_are_visible(client):
    html = _html(client)
    assert "Priority:" in html
    assert "rollbackReviewMarkets" in html
    assert "promotionReviewMarkets" in html
    assert "Spread and total delegate to P5.6" in html
    assert "Lower-layer P5.2/P5.6 gates remain authoritative" in html


def test_p59_operator_can_see_portfolio_safety_contract(client):
    html = _html(client)
    assert "Zero provider calls" in html
    assert "No automatic promotion" in html
    assert "No automatic rollback" in html
    assert "Owner confirmation required" in html
    assert "No probability changes by console" in html
    assert "No selected-side changes" in html
    assert "No wager execution" in html


def test_p59_preserves_link_back_to_existing_model_operations(client):
    html = _html(client)
    assert 'href="/model-operations"' in html
    assert "← Model Operations" in html
