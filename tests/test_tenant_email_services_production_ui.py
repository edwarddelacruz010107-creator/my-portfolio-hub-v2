from pathlib import Path


def test_email_services_template_is_csp_safe():
    root = Path(__file__).resolve().parents[1]
    template = (root / "app/templates/admin/email_services.html").read_text()
    assert "onclick=" not in template
    assert "onchange=" not in template
    assert "tenant-email-services.js" in template
    assert "data-test-provider" in template
    assert "providerPriorityForm" in template


def test_email_services_script_exists_and_has_required_actions():
    root = Path(__file__).resolve().parents[1]
    script = (root / "app/static/js/tenant-email-services.js").read_text()
    for marker in ("testProvider", "toggleProvider", "savePriority", "data-priority-move"):
        assert marker in script


def test_email_services_routes_resolve_active_tenant():
    root = Path(__file__).resolve().parents[1]
    routes = (root / "app/admin/routes/notifications_email.py").read_text()
    assert "def _active_email_tenant_id" in routes
    assert "provider_order" in routes
    assert "Provider test crashed tenant=%s provider=%s" in routes
