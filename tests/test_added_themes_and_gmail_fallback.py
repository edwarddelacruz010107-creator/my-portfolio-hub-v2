from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_added_themes_are_cms_bound_and_use_external_javascript():
    for theme_id in ("console_blueprint", "terminal_green"):
        template = (ROOT / "themes" / theme_id / "templates" / "index.html").read_text()
        assert "portfolio.projects" in template
        assert "portfolio.contact_form_action" in template
        assert "partials/_portfolio_seo.html" in template
        assert f"themes/{theme_id}/theme.js" in template
        assert "onclick=" not in template


def test_gmail_network_fallback_is_used_for_test_and_delivery():
    route = (ROOT / "app" / "superadmin" / "routes" / "email_settings.py").read_text()
    sender = (ROOT / "app" / "services" / "email" / "superadmin_email_service.py").read_text()
    assert "suggested_port': 465" in route
    assert "smtp.gmail.com" in route and "SMTP_SSL" in route
    assert "smtp.gmail.com" in sender and "_deliver(465, 'ssl')" in sender
