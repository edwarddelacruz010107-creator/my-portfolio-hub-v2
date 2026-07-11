"""Regression tests for the centralized MyPortfolioHub favicon system."""
from __future__ import annotations

from pathlib import Path

import pytest


FAVICON_PATHS = (
    "/favicon.ico",
    "/static/images/favicon/favicon-48x48.png",
    "/static/images/favicon/favicon-96x96.png",
    "/static/images/favicon/apple-touch-icon.png",
    "/static/images/favicon/site.webmanifest",
)


def _assert_favicon_head(html: str) -> None:
    head = html.split("</head>", 1)[0]
    for path in FAVICON_PATHS:
        assert path in head, f"Missing favicon reference: {path}"
    # PNG, Apple, and manifest assets are unique.  /favicon.ico appears
    # exactly twice by design: rel=icon and rel=shortcut icon.
    for path in FAVICON_PATHS[1:]:
        assert head.count(path) == 1, f"Duplicate favicon reference: {path}"
    assert head.count("/favicon.ico") == 2
    assert "/static/image/icon.ico" not in head
    assert "/static/img/brand/favicon.ico" not in head


def test_root_favicon_is_public_image(app):
    client = app.test_client()
    response = client.get("/favicon.ico", follow_redirects=False)

    assert response.status_code == 200
    assert response.content_type in {
        "image/x-icon",
        "image/vnd.microsoft.icon",
    }
    assert not response.is_json
    assert not response.data.lstrip().startswith(b"<")
    assert response.headers.get("Location") is None
    assert "public" in response.headers.get("Cache-Control", "")
    assert "max-age=86400" in response.headers.get("Cache-Control", "")


@pytest.mark.parametrize(
    ("path", "content_type"),
    [
        ("/static/images/favicon/favicon-48x48.png", "image/png"),
        ("/static/images/favicon/favicon-96x96.png", "image/png"),
        ("/static/images/favicon/apple-touch-icon.png", "image/png"),
        ("/static/images/favicon/site.webmanifest", "application/manifest+json"),
    ],
)
def test_static_favicon_assets_are_public(app, path, content_type):
    response = app.test_client().get(path, follow_redirects=False)
    assert response.status_code == 200
    assert response.content_type == content_type
    assert response.headers.get("Location") is None
    assert "max-age=86400" in response.headers.get("Cache-Control", "")


@pytest.mark.parametrize("path", ["/", "/projects", "/themes", "/administrator-portfolio/"])
def test_public_pages_render_canonical_favicon_tags(app, path):
    response = app.test_client().get(path, follow_redirects=True)
    assert response.status_code == 200
    _assert_favicon_head(response.get_data(as_text=True))


def test_robots_allows_favicon_and_static(app):
    response = app.test_client().get("/robots.txt")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Allow: /favicon.ico" in body
    assert "Allow: /static/" in body
    assert "Sitemap:" in body


def test_all_supported_tenant_themes_use_shared_favicon_partial():
    project_root = Path(__file__).resolve().parents[1]
    for theme_id in ("default", "developer_pro", "blockform_brutal", "schematic_spec", "developer_journal", "console_blueprint", "terminal_green"):
        template = project_root / "themes" / theme_id / "templates" / "index.html"
        source = template.read_text(encoding="utf-8")
        assert "{% include 'partials/_favicons.html' %}" in source
        assert "static/image/icon.ico" not in source
        assert "img/brand/favicon" not in source

@pytest.mark.parametrize(
    "theme_id",
    ("default", "developer_pro", "blockform_brutal", "schematic_spec", "developer_journal", "console_blueprint", "terminal_green"),
)
def test_public_theme_previews_render_canonical_favicon_tags(app, theme_id):
    response = app.test_client().get(f"/themes/{theme_id}/preview")
    assert response.status_code == 200
    _assert_favicon_head(response.get_data(as_text=True))


def test_favicon_route_is_host_and_tenant_independent(app):
    response = app.test_client().get(
        "/favicon.ico",
        headers={"Host": "tenant-custom-domain.example"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert response.content_type == "image/vnd.microsoft.icon"


def test_favicon_assets_are_square_and_google_sized():
    from PIL import Image

    project_root = Path(__file__).resolve().parents[1]
    favicon_dir = project_root / "app" / "static" / "images" / "favicon"
    expected = {
        "favicon-48x48.png": (48, 48),
        "favicon-96x96.png": (96, 96),
        "apple-touch-icon.png": (180, 180),
        "icon-192x192.png": (192, 192),
        "icon-512x512.png": (512, 512),
    }
    for filename, dimensions in expected.items():
        with Image.open(favicon_dir / filename) as image:
            assert image.size == dimensions
            assert image.width == image.height
            assert image.format == "PNG"


def test_manifest_uses_root_safe_brand_icon_urls():
    import json

    project_root = Path(__file__).resolve().parents[1]
    manifest_path = project_root / "app" / "static" / "images" / "favicon" / "site.webmanifest"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["name"] == "MyPortfolioHub"
    assert manifest["short_name"] == "MyPortfolioHub"
    assert all(icon["src"].startswith("/static/images/favicon/") for icon in manifest["icons"])
    assert any(icon["sizes"] == "48x48" for icon in manifest["icons"])
    assert any(icon["sizes"] == "96x96" for icon in manifest["icons"])


def test_csp_allows_same_origin_favicon_images():
    from app import csp

    assert "'self'" in csp["img-src"]
    assert "'self'" in csp["default-src"]
