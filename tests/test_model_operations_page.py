from pathlib import Path


def test_model_operations_page_contains_all_promised_panels():
    source = Path("model_operations.html").read_text(encoding="utf-8")
    for text in (
        "Model Operations",
        "Registry",
        "Pending approvals",
        "Unhealthy models",
        "Audit events",
        "v4.3.3",
    ):
        assert text in source


def test_model_operations_page_calls_persistent_operations_endpoints():
    source = Path("model_operations.html").read_text(encoding="utf-8")
    for endpoint in (
        "/api/v4.3/operations/registry/versions",
        "/api/v4.3/operations/approvals",
        "/api/v4.3/operations/health/observations",
        "/api/v4.3/operations/status",
        "/api/v4.3/operations/audit",
    ):
        assert endpoint in source


def test_shared_navigation_links_model_operations_workspace():
    app_source = Path("app.py").read_text(encoding="utf-8")
    nav_source = Path("static/app.js").read_text(encoding="utf-8")
    assert '@app.route("/model-operations")' in app_source
    assert "model_operations.html" in app_source
    assert "['models','Model Ops','/model-operations']" in nav_source
