"""Central SEO helpers for MyPortfolioHub public and tenant pages.

The helpers deliberately return plain dictionaries so templates can serialize
JSON-LD with Jinja's ``tojson`` filter.  They never trust query-string URLs as
canonical identifiers, never emit placeholder images in ProfilePage markup,
and keep platform and tenant entities separate.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

from flask import current_app, request, url_for


def _value(source: Any, name: str, default: Any = None) -> Any:
    if source is None:
        return default
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def _text(value: Any, limit: int | None = None) -> str:
    if value is None:
        return ""
    text = " ".join(str(value).split())
    return text[:limit].rstrip() if limit and len(text) > limit else text


def _iso(value: Any) -> str | None:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return None


def _valid_http_url(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    parsed = urlparse(text)
    return text if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def absolute_url(value: Any, folder: str | None = None) -> str:
    """Return an absolute crawlable URL for a local or remote media value."""
    text = _text(value)
    if not text:
        return ""
    remote = _valid_http_url(text)
    if remote:
        return remote
    if folder:
        try:
            from app.services.media.upload_storage import build_upload_url
            text = build_upload_url(text, folder) or text
        except Exception:
            pass
    remote = _valid_http_url(text)
    if remote:
        return remote
    return urljoin(request.url_root, text.lstrip("/"))


def current_canonical_url() -> str:
    """Self-referential canonical URL without query strings or fragments."""
    return request.base_url


def _social_urls(source: Any) -> list[str]:
    raw = _value(source, "social_links", {}) or {}
    values: Iterable[Any]
    if isinstance(raw, dict):
        values = raw.values()
    elif isinstance(raw, (list, tuple, set)):
        values = raw
    else:
        values = []
    return [url for url in (_valid_http_url(item) for item in values) if url]


def _platform_origin() -> str:
    configured = _text(current_app.config.get("APP_BASE_URL")).rstrip("/")
    if configured:
        configured_host = urlparse(configured).hostname
        request_host = request.host.split(":", 1)[0].lower()
        if configured_host and configured_host.lower() == request_host:
            return configured
    return request.url_root.rstrip("/")


def platform_seo(
    *,
    title: str,
    description: str,
    canonical: str | None = None,
    image: str | None = None,
    robots: str = "index, follow, max-image-preview:large",
    page_name: str | None = None,
) -> dict[str, Any]:
    """Build platform metadata and an Organization/WebSite/WebPage graph."""
    canonical_url = _valid_http_url(canonical) or current_canonical_url()
    origin = _platform_origin()
    title = _text(title, 200) or "MyPortfolioHub"
    description = _text(description, 300)
    image_url = absolute_url(image) if image else url_for(
        "static", filename="images/favicon/icon-512x512.png", _external=True
    )
    org_id = f"{origin}/#organization"
    website_id = f"{origin}/#website"
    page_id = f"{canonical_url}#webpage"
    company_name = _text(current_app.config.get("COMPANY_NAME")) or "MyPortfolioHub"
    support_email = _text(current_app.config.get("SUPPORT_EMAIL"))
    company_location = _text(current_app.config.get("COMPANY_LOCATION"))
    social_raw = _text(current_app.config.get("COMPANY_SOCIAL_URLS"))
    same_as = [u for u in (_valid_http_url(x.strip()) for x in social_raw.split(",")) if u]

    organization: dict[str, Any] = {
        "@type": "Organization",
        "@id": org_id,
        "name": company_name,
        "url": f"{origin}/",
        "logo": {
            "@type": "ImageObject",
            "url": url_for("static", filename="images/favicon/icon-512x512.png", _external=True),
            "contentUrl": url_for("static", filename="images/favicon/icon-512x512.png", _external=True),
            "width": 512,
            "height": 512,
        },
    }
    if support_email:
        organization["email"] = support_email
        organization["contactPoint"] = {
            "@type": "ContactPoint",
            "contactType": "customer support",
            "email": support_email,
            "availableLanguage": ["English"],
        }
    if company_location:
        organization["address"] = {
            "@type": "PostalAddress",
            "addressCountry": company_location,
        }
    if same_as:
        organization["sameAs"] = same_as

    graph: list[dict[str, Any]] = [
        organization,
        {
            "@type": "WebSite",
            "@id": website_id,
            "url": f"{origin}/",
            "name": company_name,
            "alternateName": "Portfolio Hub",
            "publisher": {"@id": org_id},
            "inLanguage": "en",
        },
        {
            "@type": "WebPage",
            "@id": page_id,
            "url": canonical_url,
            "name": title,
            "description": description,
            "isPartOf": {"@id": website_id},
            "about": {"@id": org_id},
            "primaryImageOfPage": {"@type": "ImageObject", "url": image_url},
            "inLanguage": "en",
        },
    ]

    if canonical_url.rstrip("/") != origin.rstrip("/"):
        graph.append({
            "@type": "BreadcrumbList",
            "@id": f"{canonical_url}#breadcrumb",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{origin}/"},
                {"@type": "ListItem", "position": 2, "name": page_name or title, "item": canonical_url},
            ],
        })

    # Google requires a real review or aggregate rating for SoftwareApplication
    # rich-result eligibility. Only emit this entity when the operator supplies
    # a genuine, visible review through environment configuration.
    review_body = _text(current_app.config.get("SOFTWARE_REVIEW_BODY"), 500)
    review_author = _text(current_app.config.get("SOFTWARE_REVIEW_AUTHOR"), 100)
    try:
        review_rating = float(current_app.config.get("SOFTWARE_REVIEW_RATING") or 0)
    except (TypeError, ValueError):
        review_rating = 0
    if review_body and review_author and 1 <= review_rating <= 5:
        graph.append({
            "@type": "WebApplication",
            "@id": f"{origin}/#webapp",
            "name": company_name,
            "url": f"{origin}/",
            "description": description,
            "applicationCategory": "BusinessApplication",
            "operatingSystem": "Web",
            "publisher": {"@id": org_id},
            "offers": {"@type": "Offer", "price": 0, "priceCurrency": "USD"},
            "review": {
                "@type": "Review",
                "author": {"@type": "Person", "name": review_author},
                "reviewBody": review_body,
                "reviewRating": {"@type": "Rating", "ratingValue": review_rating, "bestRating": 5, "worstRating": 1},
            },
        })

    return {
        "title": title,
        "description": description,
        "canonical": canonical_url,
        "robots": robots,
        "og_type": "website",
        "image": image_url,
        "image_alt": f"{company_name} platform preview",
        "graph": {"@context": "https://schema.org", "@graph": graph},
    }


def portfolio_seo(
    *,
    profile: Any = None,
    portfolio: Any = None,
    tenant_slug: str | None = None,
    projects: Iterable[Any] | None = None,
    preview_mode: bool = False,
) -> dict[str, Any]:
    """Build metadata and valid ProfilePage/Person structured data."""
    source = portfolio or profile
    slug = _text(tenant_slug or _value(source, "tenant_slug") or _value(source, "slug") or "default")
    try:
        from app.services.custom_domain_service import tenant_portfolio_public_url, tenant_project_public_url
        canonical_url = current_canonical_url() if preview_mode else tenant_portfolio_public_url(slug, external=True)
    except Exception:
        canonical_url = current_canonical_url()
        tenant_project_public_url = None

    name = _text(_value(source, "name")) or "Portfolio Owner"
    role = _text(_value(source, "title")) or "Professional Portfolio"
    description = _text(
        _value(source, "meta_description")
        or _value(source, "bio_plain")
        or _value(source, "bio_short")
        or _value(source, "bio")
        or f"{name}'s professional portfolio.",
        300,
    )
    title = _text(_value(source, "meta_title"), 200) or f"{name} — {role}"
    image = absolute_url(
        _value(source, "og_image") or _value(source, "avatar_url") or _value(source, "profile_image"),
        "profiles",
    )
    indexable = bool(_value(source, "seo_indexable", True)) and not preview_mode
    robots = "index, follow, max-snippet:-1, max-image-preview:large, max-video-preview:-1" if indexable else "noindex, nofollow, noarchive"
    person_id = f"{canonical_url}#person"
    page_id = f"{canonical_url}#profilepage"
    social_urls = _social_urls(source)
    updated = _iso(_value(profile, "updated_at"))
    created = _iso(_value(profile, "created_at"))

    person: dict[str, Any] = {
        "@type": "Person",
        "@id": person_id,
        "name": name,
        "alternateName": slug,
        "identifier": f"tenant:{slug}",
        "jobTitle": role,
        "description": description,
        "url": canonical_url,
    }
    if image:
        person["image"] = image
    if social_urls:
        person["sameAs"] = social_urls
    location = _text(_value(source, "location"))
    if location:
        person["address"] = {"@type": "PostalAddress", "addressLocality": location}

    project_rows = list(projects or _value(source, "projects", []) or [])[:10]
    project_parts: list[dict[str, Any]] = []
    for project in project_rows:
        project_slug = _text(_value(project, "slug"))
        if not project_slug or tenant_project_public_url is None:
            continue
        project_url = tenant_project_public_url(slug, project_slug, external=True)
        project_item: dict[str, Any] = {
            "@type": "CreativeWork",
            "@id": f"{project_url}#creativework",
            "name": _text(_value(project, "title")) or "Untitled Project",
            "url": project_url,
            "author": {"@id": person_id},
        }
        project_desc = _text(_value(project, "description_short") or _value(project, "description"), 300)
        if project_desc:
            project_item["description"] = project_desc
        project_image = absolute_url(_value(project, "image_url") or _value(project, "image"), "projects")
        if project_image:
            project_item["image"] = project_image
        project_parts.append(project_item)

    page: dict[str, Any] = {
        "@type": "ProfilePage",
        "@id": page_id,
        "url": canonical_url,
        "name": title,
        "description": description,
        "mainEntity": {"@id": person_id},
        "inLanguage": "en",
    }
    if created:
        page["dateCreated"] = created
    if updated:
        page["dateModified"] = updated
    if project_parts:
        page["hasPart"] = [{"@id": item["@id"]} for item in project_parts]

    graph: list[dict[str, Any]] = [page, person, *project_parts]
    if request.path not in {"/", ""} and not preview_mode:
        graph.append({
            "@type": "BreadcrumbList",
            "@id": f"{canonical_url}#breadcrumb",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "MyPortfolioHub", "item": url_for("root", _external=True)},
                {"@type": "ListItem", "position": 2, "name": name, "item": canonical_url},
            ],
        })

    return {
        "title": title,
        "description": description,
        "canonical": canonical_url,
        "robots": robots,
        "og_type": "profile",
        "image": image,
        "image_alt": _text(_value(source, "profile_image_alt")) or f"Portrait and portfolio of {name}",
        "graph": {"@context": "https://schema.org", "@graph": graph},
    }


def project_seo(*, profile: Any, project: Any, tenant_slug: str | None = None) -> dict[str, Any]:
    """Build canonical metadata plus CreativeWork and BreadcrumbList JSON-LD."""
    slug = _text(tenant_slug or _value(project, "tenant_slug") or _value(profile, "tenant_slug") or "default")
    from app.services.custom_domain_service import tenant_portfolio_public_url, tenant_project_public_url

    project_slug = _text(_value(project, "slug"))
    canonical_url = tenant_project_public_url(slug, project_slug, external=True)
    portfolio_url = tenant_portfolio_public_url(slug, external=True)
    owner_name = _text(_value(profile, "name")) or "Creator"
    owner_role = _text(_value(profile, "title"))
    project_title = _text(_value(project, "meta_title")) or _text(_value(project, "title")) or "Untitled Project"
    description = _text(
        _value(project, "meta_description")
        or _value(project, "description_short")
        or _value(project, "outcome_summary")
        or _value(project, "description")
        or "View this project case study.",
        300,
    )
    image = absolute_url(_value(project, "image_url") or _value(project, "image"), "projects")
    published = _text(_value(project, "status", "published")).lower() == "published"
    profile_indexable = bool(_value(profile, "seo_indexable", True))
    robots = "index, follow, max-snippet:-1, max-image-preview:large" if (published and profile_indexable) else "noindex, nofollow, noarchive"
    person_id = f"{portfolio_url}#person"
    work_id = f"{canonical_url}#creativework"
    page_id = f"{canonical_url}#webpage"

    person: dict[str, Any] = {"@type": "Person", "@id": person_id, "name": owner_name, "url": portfolio_url}
    if owner_role:
        person["jobTitle"] = owner_role

    work: dict[str, Any] = {
        "@type": "CreativeWork",
        "@id": work_id,
        "name": project_title,
        "description": description,
        "url": canonical_url,
        "mainEntityOfPage": {"@id": page_id},
        "author": {"@id": person_id},
        "creator": {"@id": person_id},
        "isPartOf": {"@id": f"{portfolio_url}#profilepage"},
    }
    if image:
        work["image"] = image
    created = _iso(_value(project, "created_at") or _value(project, "date_completed"))
    modified = _iso(_value(project, "updated_at"))
    if created:
        work["dateCreated"] = created
        work["datePublished"] = created
    if modified:
        work["dateModified"] = modified
    category = _text(_value(project, "category"))
    if category:
        work["genre"] = category
    tags = _value(project, "tags") or _value(project, "technologies") or []
    if isinstance(tags, str):
        tags = [part.strip() for part in tags.split(",") if part.strip()]
    if tags:
        work["keywords"] = list(tags)

    breadcrumb = {
        "@type": "BreadcrumbList",
        "@id": f"{canonical_url}#breadcrumb",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": owner_name, "item": portfolio_url},
            {"@type": "ListItem", "position": 2, "name": project_title, "item": canonical_url},
        ],
    }
    page = {
        "@type": "WebPage",
        "@id": page_id,
        "url": canonical_url,
        "name": project_title,
        "description": description,
        "mainEntity": {"@id": work_id},
        "breadcrumb": {"@id": breadcrumb["@id"]},
        "inLanguage": "en",
    }
    return {
        "title": project_title,
        "description": description,
        "canonical": canonical_url,
        "robots": robots,
        "og_type": "article",
        "image": image,
        "image_alt": _text(_value(project, "image_alt")) or f"Preview of {project_title}",
        "graph": {"@context": "https://schema.org", "@graph": [page, work, person, breadcrumb]},
    }
