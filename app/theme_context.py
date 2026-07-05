"""
app/theme_context.py — Portfolio -> theme context adapter (v6.4)

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

v6.4 changes
------------
Per-entity normalization delegated to app/services/theme_serializers.py
instead of hand-rolled getattr chains. This is a bug fix, not a style
change -- the old chains had two confirmed field-name mismatches that
silently dropped data in production:

  * Testimonial: read `.name` / `.role` / `.avatar_url`, none of which
    exist on the model. Real columns are `author_name` / `author_title`
    (+ `author_company`) / `author_avatar`. Every testimonial rendered
    with a blank name, role, and avatar. `rating` was never read at all.
  * Certificate: read `.skills_list`, which doesn't exist. Real column
    is `.skills` (comma-separated text). Certificate skill badges
    rendered empty.

`portfolio` is now a plain dict instead of SimpleNamespace (JSON-safe,
`tojson`-safe). This is template-transparent: Jinja's `.` operator
falls back from getattr to getitem, and all 3 installed themes
(default, developer_pro, futuristic_cyber) use dot-access exclusively
-- verified before making this change.
"""

from app.services.theme_serializers import (
    serialize_project,
    serialize_skill,
    serialize_service,
    serialize_testimonial,
    serialize_certificate,
    serialize_social_links,
)


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
    certificates=None,
    stats=None,
    tenant_slug='default',
    contact_url='#',
):
    """
    Build the `portfolio` dict themes render against, plus the
    top-level extras (`name_parts`, `categories`) some themes use.

    All fields degrade gracefully when `profile` is None (e.g. a brand
    new tenant with no Profile row yet) so a missing/incomplete
    profile never turns into a 500 on the public page.

    Signature is unchanged from v6.3 -- do not rename kwargs here
    without updating all 4 call sites (app/tenant/__init__.py,
    app/admin/routes/profile_appearance.py,
    app/public/services/theme_preview_data.py, app/__init__.py).
    """
    projects = projects or []
    services = services or []
    testimonials = testimonials or []
    certificates = certificates or []
    skills_by_category = skills_by_category or {}
    stats = stats or {}

    name = (getattr(profile, 'name', '') or 'Your Name').strip()
    social = serialize_social_links(getattr(profile, 'social_links', None) or {})

    name_parts = name.split(' ', 1)
    if len(name_parts) == 1:
        name_parts.append('')

    project_views = [serialize_project(p) for p in projects]

    skills_grouped = [
        {
            'category': category,
            'skills': [serialize_skill(s) for s in skill_list],
        }
        for category, skill_list in skills_by_category.items()
    ]

    stats_list = [
        {'label': 'Projects', 'value': stats.get('projects_count', 0) or 0},
        {'label': 'Years Experience', 'value': stats.get('years_experience', 0) or 0},
        {'label': 'Clients', 'value': stats.get('clients_count', 0) or 0},
    ]

    service_views = [serialize_service(s) for s in services]
    testimonial_views = [serialize_testimonial(t) for t in testimonials]
    certificate_views = [serialize_certificate(c) for c in certificates]

    bio = getattr(profile, 'bio', '') or '' if profile else ''
    bio_short = getattr(profile, 'bio_short', '') or '' if profile else ''

    portfolio = {
        'name': name,
        'title': getattr(profile, 'title', '') or '' if profile else '',
        'bio': bio,
        'bio_plain': bio_short or bio,
        'bio_extended': bio,
        'avatar_url': getattr(profile, 'profile_image', '') or '' if profile else '',
        'slug': tenant_slug,
        'email': getattr(profile, 'email', '') or '' if profile else '',
        'location': getattr(profile, 'location', '') or '' if profile else '',
        'response_time': None,
        'github_url': social['github'],
        'linkedin_url': social['linkedin'],
        'twitter_url': social['twitter'],
        'facebook_url': social['facebook'],
        'resume_url': getattr(profile, 'resume_url', '') or '' if profile else '',
        'available_for_work': bool(getattr(profile, 'is_available', False)) if profile else False,
        'availability_text': getattr(profile, 'availability_status', '') or '' if profile else '',
        'stats': stats_list,
        'skills': skills_grouped,
        'projects': project_views,
        'experiences': [],  # no Experience model yet -- see theme_serializers.serialize_experience
        'services': service_views,
        'testimonials': testimonial_views,
        'certificates': certificate_views,
        'typing_phrases': [getattr(profile, 'hero_tagline', None) or 'Building digital experiences.'] if profile else ['Building digital experiences.'],
        'footer_tagline': getattr(profile, 'subtitle', '') or '' if profile else '',
        'about_highlight': None,
        'about_subtitle': None,
        'skills_subtitle': None,
        'projects_subtitle': None,
        'experience_subtitle': None,
        'contact_subtitle': None,
        'contact_form_action': contact_url,
        'initials': _initials(name),
        'meta_description': getattr(profile, 'meta_description', '') or '' if profile else '',
    }

    categories = sorted({p['category'] for p in project_views if p['category']})

    return portfolio, name_parts, categories
