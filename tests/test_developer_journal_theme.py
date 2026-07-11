"""Regression coverage for the Developer Journal CMS theme."""
from pathlib import Path


def test_developer_journal_is_supported_and_complete():
    from app.theme_engine import SUPPORTED_THEME_IDS

    root = Path(__file__).resolve().parents[1]
    assert "developer_journal" in SUPPORTED_THEME_IDS
    assert (root / "themes/developer_journal/theme.json").is_file()
    assert (root / "themes/developer_journal/templates/index.html").is_file()
    assert (root / "app/static/themes/developer_journal/theme.js").is_file()
    assert (root / "app/static/themes/developer_journal/preview.svg").is_file()


def test_developer_journal_uses_shared_cms_contract():
    root = Path(__file__).resolve().parents[1]
    source = (root / "themes/developer_journal/templates/index.html").read_text(encoding="utf-8")

    assert "partials/_favicons.html" in source
    assert "partials/_portfolio_seo.html" in source
    assert "portfolio.projects" in source
    assert "portfolio.skills" in source
    assert "portfolio.services" in source
    assert "portfolio.experiences" in source
    assert "portfolio.certificates" in source
    assert "portfolio.testimonials" in source
    assert "portfolio.contact_form_action" in source
    assert "portfolio.stats" in source
    assert "e.achievements" in source
    assert "service.subtitle" in source
    assert "c.credential_id" in source
    assert "c.verification_url" in source
    assert "featured.prototype_url" in source
    assert "portfolio.website_url" in source
    assert "url_for('tenant.billing'" in source
    assert "onclick=" not in source
    assert "<script>" not in source


def test_developer_journal_preview_renders(app):
    response = app.test_client().get("/themes/developer_journal/preview")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Developer Journal" in html
    assert "/static/themes/developer_journal/theme.js" in html
    assert "/favicon.ico" in html


def test_developer_journal_is_available_to_normal_tenants():
    from types import SimpleNamespace
    from app.theme_engine import ThemeEngine

    engine = ThemeEngine()
    profile = SimpleNamespace(
        selected_theme="developer_journal",
        is_administrator=False,
        plan="Trial",
        effective_plan=lambda: "trial",
    )
    assert engine.can_use_theme(profile, "developer_journal") is True
    assert engine.resolve_theme(profile) == "developer_journal"


def test_social_serializer_keeps_editable_website_link():
    from app.services.theme_serializers import serialize_social_links

    links = serialize_social_links({"website_url": "https://portfolio.example"})
    assert links["website"] == "https://portfolio.example"
