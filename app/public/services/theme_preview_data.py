"""
app/public/services/theme_preview_data.py

Static, in-memory SAMPLE portfolio content used ONLY to render a theme
preview for anonymous marketing-page visitors at
`GET /themes/<theme_id>/preview`.

Why not reuse the tenant 'default' portfolio's real data?
    - Anonymous, unauthenticated route -> must never expose any real
      tenant's Profile/Project/contact data.
    - Decoupled from DB state -- the preview always looks complete and
      polished, even on a brand-new/empty install with no seeded tenant.

This mirrors the SHAPE `app/theme_context.py::build_portfolio_view`
expects from live ORM rows, so the exact same adapter -- and therefore
the exact same rendering path a real tenant's portfolio uses -- is
exercised here. No theme-specific branching, no placeholder screenshots.

v6.4 changes
------------
* Sample data now mirrors the REAL ORM column names (author_name,
  author_title, author_company, author_avatar on Testimonial; skills
  as a comma-separated string on Certificate) instead of a hand-picked
  set of aliases. `theme_context.py` now routes every entity through
  `app/services/theme_serializers.py`, which generates compatibility
  aliases automatically -- hand-rolling them here was redundant and,
  worse, meant this file could silently drift from what the real
  models actually look like without anyone noticing. Mirroring the
  real schema here makes this file double as a lightweight contract
  check: if a theme renders correctly against this sample data, it
  will render correctly against real tenant data with the same shape.
* Added `_sample_certificates()` -- previously omitted entirely, so
  every theme's certificate section rendered empty in the preview
  even when the theme supports it. Preview should be feature-complete
  relative to a real tenant portfolio; this closes that gap.
"""

from __future__ import annotations

from types import SimpleNamespace


def _sample_profile() -> SimpleNamespace:
    return SimpleNamespace(
        name="Jordan Reyes",
        title="Product Designer & Frontend Engineer",
        bio=(
            "I design and build digital products end-to-end -- from "
            "wireframes to production-ready interfaces. Based in Manila, "
            "working with teams worldwide."
        ),
        bio_short="I design and build digital products end-to-end.",
        profile_image="",
        email="hello@example.com",
        location="Manila, PH",
        social_links={
            "github": "https://github.com",
            "linkedin": "https://linkedin.com",
            "twitter": "https://twitter.com",
        },
        resume_url="",
        is_available=True,
        availability_status="Available for freelance",
        hero_tagline="Building interfaces people enjoy using.",
        subtitle="Product Designer & Frontend Engineer",
        meta_description="Sample portfolio preview",
        clients_count=18,
    )


def _sample_projects() -> list[SimpleNamespace]:
    data = [
        ("Nimbus Analytics", "Real-time SaaS analytics dashboard.", "Web App"),
        ("Fieldwork", "Field-service scheduling app for small teams.", "Mobile"),
        ("Aperture", "Design system and component library.", "Design System"),
        ("Lighthouse", "Marketing site + CMS for a media brand.", "Marketing"),
    ]
    return [
        SimpleNamespace(
            title=title,
            description=desc,
            short_description=desc,
            image_url="",
            cover_image="",
            category=cat,
            live_url="#",
            demo_url="#",
            github_url="#",
            repo_url="#",
            tech_stack=["React", "Flask", "PostgreSQL"],
            technologies=["React", "Flask", "PostgreSQL"],
            is_featured=(i == 0),
        )
        for i, (title, desc, cat) in enumerate(data)
    ]


def _sample_skills_by_category() -> dict:
    return {
        "Frontend": [SimpleNamespace(name=n, level=lvl) for n, lvl in
                     [("React", 90), ("TypeScript", 85), ("CSS/Tailwind", 92)]],
        "Backend": [SimpleNamespace(name=n, level=lvl) for n, lvl in
                    [("Python/Flask", 88), ("PostgreSQL", 80)]],
        "Design": [SimpleNamespace(name=n, level=lvl) for n, lvl in
                   [("Figma", 90), ("Design Systems", 85)]],
    }


def _sample_services() -> list[SimpleNamespace]:
    data = [
        ("Product Design", "End-to-end UX/UI for web and mobile.", "bi-palette"),
        ("Frontend Development", "Performant, accessible interfaces.", "bi-code-slash"),
        ("Design Systems", "Scalable component libraries.", "bi-grid-3x3-gap"),
    ]
    return [
        SimpleNamespace(name=n, subtitle="", description=d, icon=icon)
        for n, d, icon in data
    ]


def _sample_testimonials() -> list[SimpleNamespace]:
    """Field names match the real `Testimonial` ORM model exactly
    (app/models/tenant_data.py:458-470). No manual aliasing needed --
    theme_serializers.serialize_testimonial() generates the full
    alias set (name, role, content, quote, stars, image_url, etc.)
    from these canonical fields."""
    data = [
        ("Alex Chen", "VP Product", "Nimbus",
         "Jordan shipped our redesign two weeks ahead of schedule -- and it tested better than the original."),
        ("Priya Nair", "Founder", "Fieldwork",
         "One of the rare designers who can also ship production code."),
    ]
    return [
        SimpleNamespace(
            author_name=name,
            author_title=title,
            author_company=company,
            author_avatar="",
            content=message,
            rating=5,
            is_featured=(i == 0),
            order=i,
        )
        for i, (name, title, company, message) in enumerate(data)
    ]


def _sample_certificates() -> list[SimpleNamespace]:
    """Field names match the real `Certificate` ORM model exactly
    (app/models/tenant_data.py:532-566), including `skills` as the
    comma-separated text column theme_serializers.serialize_certificate
    parses into a list."""
    data = [
        ("AWS Certified Solutions Architect", "Amazon Web Services", "AWS, Cloud Architecture, EC2"),
        ("Professional UX Design Certificate", "Google", "UX Research, Prototyping, Figma"),
    ]
    return [
        SimpleNamespace(
            title=title,
            issuer=issuer,
            description="",
            credential_id="",
            verification_url="#",
            image_path="",
            badge_path="",
            issue_date=None,
            expiration_date=None,
            skills=skills_csv,
            is_featured=(i == 0),
            is_expired=False,
            display_order=i,
        )
        for i, (title, issuer, skills_csv) in enumerate(data)
    ]


def _sample_experiences() -> list[SimpleNamespace]:
    """Preview data for the editable Work Experience Timeline CMS section."""
    data = [
        ("Senior Full Stack Developer", "Nova Digital Studio", "Full-time", "Remote", "2024-01-01", None, True,
         "Building production SaaS dashboards, portfolio systems, and automation tooling.",
         "Designed multi-tenant Flask architecture\nImplemented payments, email, and theme engine\nImproved admin dashboard UX", "Python, Flask, PostgreSQL, Docker"),
        ("Frontend/UI Engineer", "Freelance Projects", "Freelance", "Philippines", "2022-06-01", "2023-12-01", False,
         "Delivered modern animated portfolio, POS, and dashboard interfaces for clients.",
         "Created responsive UI systems\nBuilt reusable component patterns", "JavaScript, HTML, CSS, Figma"),
    ]
    from datetime import date
    def parse(value):
        return date.fromisoformat(value) if value else None
    return [
        SimpleNamespace(
            role=role,
            company=company,
            employment_type=kind,
            location=location,
            start_date=parse(start),
            end_date=parse(end),
            is_current=current,
            description=description,
            achievements=achievements,
            technologies=tech,
            icon="lucide:briefcase-business",
            display_order=i,
            is_visible=True,
        )
        for i, (role, company, kind, location, start, end, current, description, achievements, tech) in enumerate(data)
    ]


def build_sample_context(tenant_slug: str = "preview", contact_url: str = "#") -> dict:
    """
    Returns kwargs ready to pass straight into ThemeEngine.render(), using
    the same `build_portfolio_view` adapter live tenant portfolios use.
    """
    from app.theme_context import build_portfolio_view

    profile = _sample_profile()
    projects = _sample_projects()
    skills_by_category = _sample_skills_by_category()
    services = _sample_services()
    testimonials = _sample_testimonials()
    certificates = _sample_certificates()
    experiences = _sample_experiences()

    stats = {
        "projects_count": len(projects),
        "years_experience": 6,
        "clients_count": profile.clients_count,
    }

    portfolio_view, name_parts, categories = build_portfolio_view(
        profile,
        projects=projects,
        skills_by_category=skills_by_category,
        services=services,
        testimonials=testimonials,
        certificates=certificates,
        experiences=experiences,
        stats=stats,
        tenant_slug=tenant_slug,
        contact_url=contact_url,
    )

    return {
        "profile": profile,
        "portfolio": portfolio_view,
        "name_parts": name_parts,
        "featured_projects": [p for p in projects if p.is_featured],
        "other_projects": [p for p in projects if not p.is_featured],
        "skills": [s for group in skills_by_category.values() for s in group],
        "skills_by_category": skills_by_category,
        "testimonials": testimonials,
        "certificates": certificates,
        "services": services,
        "experiences": experiences,
        "stats": stats,
        "categories": categories,
        "tenant_slug": tenant_slug,
        "contact_url": contact_url,
        "is_root_domain": False,
    }
