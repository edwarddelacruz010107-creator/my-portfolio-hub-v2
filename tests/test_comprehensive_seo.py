"""Regression tests for MyPortfolioHub's centralized public SEO system."""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse


JSON_LD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


def _html(response) -> str:
    assert response.status_code == 200
    return response.get_data(as_text=True)


def _json_ld_graphs(html: str) -> list[dict]:
    payloads: list[dict] = []
    for raw in JSON_LD_RE.findall(html):
        payloads.append(json.loads(raw.strip()))
    return payloads


def _types(payloads: list[dict]) -> set[str]:
    found: set[str] = set()
    for payload in payloads:
        nodes = payload.get("@graph", [payload])
        for node in nodes:
            value = node.get("@type")
            if isinstance(value, str):
                found.add(value)
            elif isinstance(value, list):
                found.update(str(item) for item in value)
    return found


def test_platform_home_has_complete_metadata_and_graph(app):
    response = app.test_client().get("/", base_url="https://myportfoliohub.online")
    html = _html(response)

    assert html.count('<link rel="canonical"') == 1
    assert html.count('<meta property="og:title"') == 1
    assert html.count('<meta property="og:url"') == 1
    assert html.count('<meta name="twitter:card"') == 1
    assert 'content="https://myportfoliohub.online/"' in html

    payloads = _json_ld_graphs(html)
    assert {"Organization", "WebSite", "WebPage"}.issubset(_types(payloads))


def test_profile_page_schema_has_valid_main_entity(app):
    response = app.test_client().get(
        "/administrator-portfolio/",
        base_url="https://myportfoliohub.online",
    )
    html = _html(response)
    payloads = _json_ld_graphs(html)
    nodes = [node for payload in payloads for node in payload.get("@graph", [payload])]

    profile_page = next(node for node in nodes if node.get("@type") == "ProfilePage")
    person = next(node for node in nodes if node.get("@type") == "Person")

    assert person.get("name")
    assert profile_page["mainEntity"]["@id"] == person["@id"]
    assert profile_page["url"].startswith("https://")
    assert "#" not in profile_page["url"]


def test_profile_schema_never_emits_placeholder_image(app):
    html = _html(app.test_client().get(
        "/administrator-portfolio/",
        base_url="https://myportfoliohub.online",
    ))
    for payload in _json_ld_graphs(html):
        for node in payload.get("@graph", [payload]):
            if node.get("@type") == "Person" and node.get("image"):
                image = str(node["image"])
                assert "placeholder" not in image.lower()
                assert urlparse(image).scheme in {"http", "https"}


def test_search_and_filter_pages_are_noindex(app):
    client = app.test_client()
    for path in (
        "/explore?q=developer",
        "/projects?q=flask",
        "/themes?category=developer",
    ):
        html = _html(client.get(path, base_url="https://myportfoliohub.online"))
        assert '<meta name="robots" content="noindex, follow">' in html


def test_internal_surfaces_use_x_robots_without_blocking_administrator_portfolio(app):
    client = app.test_client()
    auth = client.get("/auth/", base_url="https://myportfoliohub.online")
    assert auth.headers.get("X-Robots-Tag") == "noindex, nofollow, noarchive"

    portfolio = client.get(
        "/administrator-portfolio/",
        base_url="https://myportfoliohub.online",
    )
    assert portfolio.status_code == 200
    assert portfolio.headers.get("X-Robots-Tag") is None


def test_theme_previews_are_noindex(app):
    response = app.test_client().get(
        "/themes/default/preview",
        base_url="https://myportfoliohub.online",
    )
    assert response.status_code == 200
    assert response.headers.get("X-Robots-Tag") == "noindex, nofollow, noarchive"
    assert '<meta name="robots" content="noindex, nofollow, noarchive">' in response.get_data(as_text=True)


def test_robots_and_sitemap_expose_public_content_only(app):
    client = app.test_client()
    robots = _html(client.get("/robots.txt", base_url="https://myportfoliohub.online"))
    assert "Allow: /favicon.ico" in robots
    assert "Allow: /static/" in robots
    assert "Disallow: /studio/" in robots
    assert "Disallow: /superadmin/" in robots
    assert "Sitemap: https://myportfoliohub.online/sitemap.xml" in robots

    sitemap_response = client.get("/sitemap.xml", base_url="https://myportfoliohub.online")
    assert sitemap_response.status_code == 200
    assert sitemap_response.content_type.startswith("application/xml")
    sitemap = sitemap_response.get_data(as_text=True)
    assert "https://myportfoliohub.online/" in sitemap
    assert "/projects" in sitemap
    assert "/themes" in sitemap
    assert "/studio/" not in sitemap
    assert "/superadmin/" not in sitemap


def test_active_themes_use_central_portfolio_seo_partial():
    root = Path(__file__).resolve().parents[1]
    for theme_id in ("default", "developer_pro", "blockform_brutal", "schematic_spec", "developer_journal", "console_blueprint", "terminal_green"):
        source = (root / "themes" / theme_id / "templates" / "index.html").read_text(encoding="utf-8")
        assert "{% include 'partials/_portfolio_seo.html' %}" in source
        assert source.count('rel="canonical"') == 0
        assert source.count('property="og:title"') == 0


def test_public_templates_do_not_use_fragile_relative_canonicals():
    root = Path(__file__).resolve().parents[1]
    relevant = [
        root / "app" / "public" / "templates" / "public" / "index.html",
        root / "app" / "public" / "templates" / "public" / "_base.html",
        root / "app" / "templates" / "partials" / "_seo_meta.html",
    ]
    for path in relevant:
        source = path.read_text(encoding="utf-8")
        assert 'href="static/' not in source
        assert "request.url }}" not in source


def test_published_project_has_creativework_and_breadcrumb_schema(app):
    from app import db
    from app.models.tenant_data import Project

    with app.app_context():
        project = Project.query.filter_by(tenant_slug="default", slug="seo-schema-test").first()
        if project is None:
            project = Project(
                tenant_id=1,
                tenant_slug="default",
                title="SEO Schema Test",
                slug="seo-schema-test",
                description_short="A published project used to verify structured data.",
                status="published",
                case_study_enabled=True,
                tags=["Flask", "SEO"],
            )
            db.session.add(project)
            db.session.commit()

    response = app.test_client().get(
        "/administrator-portfolio/project/seo-schema-test",
        base_url="https://myportfoliohub.online",
    )
    html = _html(response)
    payloads = _json_ld_graphs(html)
    nodes = [node for payload in payloads for node in payload.get("@graph", [payload])]
    work = next(node for node in nodes if node.get("@type") == "CreativeWork")
    breadcrumb = next(node for node in nodes if node.get("@type") == "BreadcrumbList")

    assert work["name"] == "SEO Schema Test"
    assert work["url"].endswith("/administrator-portfolio/project/seo-schema-test")
    assert work["author"]["@id"].endswith("#person")
    assert len(breadcrumb["itemListElement"]) >= 2
    assert all("#projects" not in item.get("item", "") for item in breadcrumb["itemListElement"])
    assert html.count('<link rel="canonical"') == 1
