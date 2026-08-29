from __future__ import annotations


def test_model_operations_exposes_p53_calibration_console(client):
    response = client.get("/model-operations")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "P5.3 · Game Calibration Operations Console" in html
    assert "/api/game-calibration/control-plane" in html
    assert "/api/game-calibration/promote" not in html
    # Mutating endpoint paths are supplied by the P5.2 command contract rather
    # than hard-coded into a page-load request.
    assert "command.endpoint" in html


def test_p53_mutation_buttons_are_disabled_by_default(client):
    html = client.get("/model-operations").get_data(as_text=True)
    assert 'id="promote-btn" type="button" disabled' in html
    assert 'id="rollback-btn" type="button" disabled' in html
    assert "promoteReady===true" in html
    assert "rollbackReady===true" in html
    assert "role==='owner'" in html


def test_p53_requires_exact_confirmation_and_second_human_confirmation(client):
    html = client.get("/model-operations").get_data(as_text=True)
    assert "PROMOTE_GAME_CALIBRATION" in html
    assert "ROLLBACK_GAME_CALIBRATION" in html
    assert "input.value!==command.confirmation" in html
    assert "if(!confirm(text))return" in html


def test_p53_console_does_not_auto_mutate_on_load(client):
    html = client.get("/model-operations").get_data(as_text=True)
    # Additional read-only governance loaders may be added by later phases.
    # The P5.3 invariant is that its loader remains part of page initialization
    # while promotion/rollback stay bound exclusively to explicit click handlers.
    assert "await Promise.all([refreshStatus(),loadCalibration()" in html
    assert "$('#promote-btn').onclick=()=>runCalibrationMutation('promotion');" in html
    assert "$('#rollback-btn').onclick=()=>runCalibrationMutation('rollback');" in html
    assert "No mutation runs on page load or refresh." in html


def test_p53_rollback_console_requires_p51_recommendation(client):
    html = client.get("/model-operations").get_data(as_text=True)
    assert "c.rollbackReady!==true" in html
    assert "Rollback is not recommended by P5.1" in html
    assert "The console activates rollback only when P5.1 recommends rollback review." in html


def test_p53_safety_contract_is_visible_to_operator(client):
    html = client.get("/model-operations").get_data(as_text=True)
    assert "Zero provider calls" in html
    assert "No automatic promotion" in html
    assert "No automatic rollback" in html
    assert "Owner confirmation required" in html
    assert "No wager execution" in html
