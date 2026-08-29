from __future__ import annotations


def test_model_operations_exposes_p57_market_calibration_console(client):
    response = client.get("/model-operations")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "P5.7 · Spread &amp; Total Calibration Operations Console" in html
    assert "/api/game-market-calibration/control-plane" in html
    assert "/api/game-market-calibration/promote" not in html
    assert "/api/game-market-calibration/rollback" not in html
    # Mutating endpoint paths come only from the signed P5.6 command contract.
    assert "command.endpoint" in html


def test_p57_preserves_existing_moneyline_operations_console(client):
    html = client.get("/model-operations").get_data(as_text=True)
    assert "P5.3 · Game Calibration Operations Console" in html
    assert "/api/game-calibration/control-plane" in html
    assert "P5.7 · v4.3.3" in html


def test_p57_market_mutation_buttons_are_disabled_by_default(client):
    html = client.get("/model-operations").get_data(as_text=True)
    for market in ("spread", "total"):
        assert f'id="market-{market}-promote-btn" type="button" disabled' in html
        assert f'id="market-{market}-rollback-btn" type="button" disabled' in html
    assert "c.promoteReady===true" in html
    assert "c.rollbackReady===true" in html
    assert "role==='owner'" in html


def test_p57_requires_exact_tokens_and_second_human_confirmation(client):
    html = client.get("/model-operations").get_data(as_text=True)
    assert "PROMOTE_GAME_MARKET_CALIBRATION" in html
    assert "ROLLBACK_GAME_MARKET_CALIBRATION" in html
    assert "input.value!==command.confirmation" in html
    assert "if(!confirm(text))return" in html


def test_p57_console_does_not_auto_mutate_on_load_or_refresh(client):
    html = client.get("/model-operations").get_data(as_text=True)
    assert "No market mutation runs on page load or refresh." in html
    assert "await Promise.all([refreshStatus(),loadCalibration(),loadMarketCalibration()]);activate(active);" in html
    assert "runMarketCalibrationMutation(market,'promotion')" in html
    assert "runMarketCalibrationMutation(market,'rollback')" in html
    assert "$('#market-cal-refresh').onclick=loadMarketCalibration;" in html


def test_p57_rollback_requires_p55_same_market_recommendation(client):
    html = client.get("/model-operations").get_data(as_text=True)
    assert "c.rollbackReady!==true" in html
    assert "rollback is not recommended by P5.5" in html
    assert "Enabled only when P5.5 recommends rollback review for spread" in html
    assert "Enabled only when P5.5 recommends rollback review for total" in html


def test_p57_promotion_and_rollback_body_are_market_scoped(client):
    html = client.get("/model-operations").get_data(as_text=True)
    assert "{market,candidateId:candidate,confirmation:command.confirmation}" in html
    assert "{market,confirmation:command.confirmation}" in html
    assert "marketCalibration?.markets?.[market]" in html
    assert "Spread/total governance remains isolated." in html


def test_p57_operator_can_see_market_safety_contract(client):
    html = client.get("/model-operations").get_data(as_text=True)
    assert "Zero provider calls" in html
    assert "Market-isolated governance" in html
    assert "No automatic promotion" in html
    assert "No automatic rollback" in html
    assert "Owner confirmation required" in html
    assert "No selected-side changes" in html
    assert "No wager execution" in html
