#!/usr/bin/env python3
"""Small post-deploy SEO smoke test for MyPortfolioHub.

Usage:
    python scripts/audit_public_seo.py --base-url https://myportfoliohub.online
    python scripts/audit_public_seo.py --base-url https://myportfoliohub.online \
        --portfolio /administrator-portfolio/ \
        --project /administrator-portfolio/project/example

The script performs read-only requests and never authenticates.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from urllib.parse import urljoin, urlparse

import requests

JSON_LD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


def check(condition: bool, label: str, details: str = "") -> bool:
    prefix = "PASS" if condition else "FAIL"
    suffix = f" — {details}" if details else ""
    print(f"[{prefix}] {label}{suffix}")
    return condition


def fetch(session: requests.Session, base: str, path: str) -> requests.Response:
    return session.get(urljoin(base.rstrip("/") + "/", path.lstrip("/")), timeout=15, allow_redirects=False)


def audit_html(response: requests.Response, label: str, required_types: set[str]) -> bool:
    ok = True
    body = response.text
    ok &= check(response.status_code == 200, f"{label}: HTTP 200", str(response.status_code))
    ok &= check("text/html" in response.headers.get("Content-Type", ""), f"{label}: HTML content type")
    ok &= check(body.count('rel="canonical"') == 1, f"{label}: one canonical tag")
    ok &= check('property="og:title"' in body and 'property="og:url"' in body, f"{label}: Open Graph metadata")
    ok &= check('name="twitter:card"' in body, f"{label}: Twitter Card metadata")

    found_types: set[str] = set()
    valid_json = True
    for raw in JSON_LD_RE.findall(body):
        try:
            payload = json.loads(raw.strip())
        except json.JSONDecodeError:
            valid_json = False
            continue
        for node in payload.get("@graph", [payload]):
            value = node.get("@type")
            if isinstance(value, str):
                found_types.add(value)
            elif isinstance(value, list):
                found_types.update(str(item) for item in value)
    ok &= check(valid_json, f"{label}: JSON-LD parses")
    ok &= check(required_types.issubset(found_types), f"{label}: required schema types", ", ".join(sorted(found_types)))
    return bool(ok)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--portfolio", default="/administrator-portfolio/")
    parser.add_argument("--project", default="")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    if urlparse(base).scheme not in {"http", "https"}:
        parser.error("--base-url must include http:// or https://")

    session = requests.Session()
    session.headers["User-Agent"] = "MyPortfolioHub-SEO-Audit/1.0"
    passed = True

    passed &= audit_html(fetch(session, base, "/"), "Homepage", {"Organization", "WebSite", "WebPage"})
    passed &= audit_html(fetch(session, base, args.portfolio), "Portfolio", {"ProfilePage", "Person"})
    if args.project:
        passed &= audit_html(fetch(session, base, args.project), "Project", {"CreativeWork", "BreadcrumbList"})

    robots = fetch(session, base, "/robots.txt")
    passed &= check(robots.status_code == 200, "robots.txt: HTTP 200")
    passed &= check("Allow: /static/" in robots.text and "Sitemap:" in robots.text, "robots.txt: assets and sitemap allowed")

    sitemap = fetch(session, base, "/sitemap.xml")
    passed &= check(sitemap.status_code == 200, "sitemap.xml: HTTP 200")
    passed &= check("<urlset" in sitemap.text and "<loc>" in sitemap.text, "sitemap.xml: valid URL set shape")

    favicon = fetch(session, base, "/favicon.ico")
    passed &= check(favicon.status_code == 200, "favicon.ico: HTTP 200")
    passed &= check(favicon.headers.get("Content-Type", "").startswith("image/"), "favicon.ico: image MIME type")

    print("\nSEO audit " + ("passed." if passed else "failed."))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
