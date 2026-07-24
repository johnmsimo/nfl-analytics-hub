def test_enterprise_operations_page_is_registered_and_exposes_controls(client):
    response = client.get("/enterprise-operations")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Enterprise Operations" in body
    assert "v4.4.3" in body
    assert "Apply retention" in body
    assert "Create JSON export" in body
    assert "/api/v4.4/directory/organizations/" in body


def test_enterprise_operations_route_is_integrated():
    from app import app

    rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert "/enterprise-operations" in rules
    assert "/api/v4.4/directory/organizations/<organization_id>/workspaces" in rules
    assert "/api/v4.4/directory/organizations/<organization_id>/audit" in rules
