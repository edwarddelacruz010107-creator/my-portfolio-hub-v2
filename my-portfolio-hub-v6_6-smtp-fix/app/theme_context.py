"""
app/theme_context.py — Portfolio -> theme context adapter (v6.3)

The live `Profile`/`Project`/`Skill`/`Service` models use one set of
attribute names (e.g. `profile.profile_image`, `skill.category`); the
swappable theme templates (themes/*/templates/index.html) were written
against a different, theme-facing shape (`portfolio.avatar_url`,
`portfolio.skills` grouped by category, `portfolio.stats` as a list,
etc.) -- the same shape used by the admin preview mock in
app/blueprints/themes.py.

This module is the single place that bridges the two, so every theme
gets the same, complete `portfolio` object regardless of which one is
selected. Built from data the calling route already queried -- no
extra DB hits.
"""

from types import SimpleNamespace


def _initials(name: str) -> str:
    parts = [p for p in (name or '').split() if p]
    if not parts:
        return ''
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def build_portfolio_view(
    profile,
    projects=None,
    skills_by_category=None,
    services=None,
    testimonials=None,
    stats=None,
    tenant_slug='default',
    contact_url='#',
):
    """
    Build the `portfolio` object themes render against, plus the
    top-level extras (`name_parts`, `categories`) some themes use.

    All fields degrade gracefully when `profile` is None (e.g. a brand
    new tenant with no Profile row yet) so a missing/incomplete
    profile never turns into a 500 on the public page.
    """
    projects = projects or []
    services = services or []
    testimonials = testimonials or []
    skills_by_category = skills_by_category or {}
    stats = stats or {}

    name = (getattr(profile, 'name', '') or 'Your Name').strip()
    social = getattr(profile, 'social_links', None) or {}

    name_parts = name.split(' ', 1)
    if len(name_parts) == 1:
        name_parts.append('')

    project_views = [
        SimpleNamespace(
            title=getattr(p, 'title', ''),
            description=getattr(p, 'description', '') or getattr(p, 'short_description', '') or '',
            image_url=getattr(p, 'image_url', '') or getattr(p, 'cover_image', '') or '',
            category=getattr(p, 'category', '') or '',
            demo_url=getattr(p, 'live_url', None) or getattr(p, 'demo_url', None),
            github_url=getattr(p, 'github_url', None) or getattr(p, 'repo_url', None),
            tech_stack=getattr(p, 'tech_stack', None) or getattr(p, 'technologies', None) or [],
        )
        for p in projects
    ]

    skills_grouped = [
        SimpleNamespace(
            category=category or 'Skills',
            skills=[
                SimpleNamespace(name=getattr(s, 'name', ''), level=getattr(s, 'level', 0) or 0)
                for s in skill_list
            ],
        )
        for category, skill_list in skills_by_category.items()
    ]

    stats_list = [
        SimpleNamespace(label='Projects', value=stats.get('projects_count', 0) or 0),
        SimpleNamespace(label='Years Experience', value=stats.get('years_experience', 0) or 0),
        SimpleNamespace(label='Clients', value=stats.get('clients_count', 0) or 0),
    ]

    service_views = [
        SimpleNamespace(
            name=getattr(s, 'name', ''),
            subtitle=getattr(s, 'subtitle', '') or '',
            description=getattr(s, 'description', '') or '',
            icon=getattr(s, 'icon', '') or '',
        )
        for s in services
    ]

    testimonial_views = [
        SimpleNamespace(
            name=getattr(t, 'name', ''),
            role=getattr(t, 'role', '') or '',
            message=getattr(t, 'message', '') or getattr(t, 'content', '') or '',
            avatar_url=getattr(t, 'avatar_url', '') or '',
        )
        for t in testimonials
    ]

    bio = getattr(profile, 'bio', '') or '' if profile else ''
    bio_short = getattr(profile, 'bio_short', '') or '' if profile else ''

    portfolio = SimpleNamespace(
        name=name,
        title=getattr(profile, 'title', '') or '' if profile else '',
        bio=bio,
        bio_plain=bio_short or bio,
        bio_extended=bio,
        avatar_url=getattr(profile, 'profile_image', '') or '' if profile else '',
        slug=tenant_slug,
        email=getattr(profile, 'email', '') or '' if profile else '',
        location=getattr(profile, 'location', '') or '' if profile else '',
        response_time=None,
        github_url=social.get('github'),
        linkedin_url=social.get('linkedin'),
        twitter_url=social.get('twitter'),
        facebook_url=social.get('facebook'),
        resume_url=getattr(profile, 'resume_url', '') or '' if profile else '',
        available_for_work=bool(getattr(profile, 'is_available', False)) if profile else False,
        availability_text=getattr(profile, 'availability_status', '') or '' if profile else '',
        stats=stats_list,
        skills=skills_grouped,
        projects=project_views,
        experiences=[],
        services=service_views,
        testimonials=testimonial_views,
        typing_phrases=[getattr(profile, 'hero_tagline', None) or 'Building digital experiences.'] if profile else ['Building digital experiences.'],
        footer_tagline=getattr(profile, 'subtitle', '') or '' if profile else '',
        about_highlight=None,
        about_subtitle=None,
        skills_subtitle=None,
        projects_subtitle=None,
        experience_subtitle=None,
        contact_subtitle=None,
        contact_form_action=contact_url,
        initials=_initials(name),
        meta_description=getattr(profile, 'meta_description', '') or '' if profile else '',
    )

    categories = sorted({p.category for p in project_views if p.category})

    return portfolio, name_parts, categories
