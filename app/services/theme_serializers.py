"""
app/services/theme_serializers.py — Central theme serializer architecture
===========================================================================

Single choke point between ORM models and theme templates. Themes must
NEVER receive ORM objects or SimpleNamespace instances -- only plain
dict/list/str/int/float/bool (JSON-safe) data.

Why this module exists (root cause, not a style preference):
    app/theme_context.py previously built SimpleNamespace views with
    hand-rolled `getattr(obj, 'guessed_field', '')` chains. Several of
    those guesses did not match the actual ORM column names (see
    Testimonial: `.name`/`.role`/`.avatar_url` do not exist on the
    model -- the real columns are `author_name`/`author_title`/
    `author_avatar`). getattr's silent default masked this: no
    exception, no log, just blank fields shipped to production.

Design rules enforced here:
    1. Every serializer takes an ORM object OR a dict (source-agnostic).
    2. Every field is pulled through an explicit alias table, not a
       single guessed name -- if a theme or a future model migration
       renames a field, you add ONE alias entry here, not another
       getattr guess scattered across theme_context.py.
    3. Every serializer returns a plain dict. No SimpleNamespace.
    4. Missing/None values fall back to typed defaults (never None
       leaking into a template that does string formatting on it).
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping, Optional


# ─────────────────────────────────────────────────────────────────
# Core field-resolution primitive
# ─────────────────────────────────────────────────────────────────

def _get(obj: Any, *names: str, default: Any = None) -> Any:
    """
    Resolve the first present, non-None value across `names` from
    either an ORM object (attribute access) or a dict (key access).

    This is the ONE place field-name guessing happens. Every alias
    list lives inline at the call site so it's auditable in a single
    read of the serializer function -- not buried in a model diff.
    """
    for name in names:
        if isinstance(obj, Mapping):
            if name in obj and obj[name] is not None:
                return obj[name]
        else:
            val = getattr(obj, name, None)
            if val is not None:
                return val
    return default


def _str(obj: Any, *names: str, default: str = '') -> str:
    val = _get(obj, *names, default=default)
    return str(val).strip() if val is not None else default


def _int(obj: Any, *names: str, default: int = 0) -> int:
    val = _get(obj, *names, default=default)
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _bool(obj: Any, *names: str, default: bool = False) -> bool:
    val = _get(obj, *names, default=default)
    return bool(val)


def _list(obj: Any, *names: str) -> list:
    """CSV-string or JSON-string columns (e.g. Certificate.skills,
    Project.tags stored as comma-separated text in some legacy rows)
    are normalized to a real list here -- once, centrally."""
    val = _get(obj, *names, default=None)
    if val is None:
        return []
    if isinstance(val, (list, tuple)):
        return list(val)
    if isinstance(val, str):
        if val.strip().startswith('['):
            try:
                parsed = json.loads(val)
                return parsed if isinstance(parsed, list) else []
            except json.JSONDecodeError:
                pass
        # Timeline achievements are stored one item per line, while tags use CSV.
        if '\n' in val or '\r' in val:
            return [s.strip() for s in val.splitlines() if s.strip()]
        return [s.strip() for s in val.split(',') if s.strip()]
    return []


def _date_iso(obj: Any, *names: str) -> Optional[str]:
    val = _get(obj, *names, default=None)
    if val is None:
        return None
    return val.isoformat() if hasattr(val, 'isoformat') else str(val)


# ─────────────────────────────────────────────────────────────────
# Entity serializers
# ─────────────────────────────────────────────────────────────────

def serialize_testimonial(t: Any) -> dict:
    """
    Canonical schema. Alias table covers every field name variant
    seen across the current model AND the wider vocabulary themes
    have historically assumed (client_name, designation, quote,
    stars, image_url, etc.) so a theme built against any of those
    names still gets correct data via the compat block below.

    FIX: `author_name` / `author_title` / `author_avatar` are the
    real ORM columns (app/models/tenant_data.py:461-464) -- these
    were previously unmapped, causing every testimonial to render
    with a blank name, role, and avatar in production.
    """
    name = _str(t, 'author_name', 'name', 'client_name')
    role = _str(t, 'author_title', 'role', 'designation')
    company = _str(t, 'author_company', 'company')
    message = _str(t, 'content', 'message', 'review', 'testimonial', 'quote')
    avatar = _str(t, 'author_avatar', 'avatar_url', 'image_url')
    rating = _int(t, 'rating', 'stars', default=5)

    canonical = {
        'name': name,
        'role': role,
        'company': company,
        'role_display': f'{role}, {company}' if role and company else (role or company),
        'message': message,
        'avatar_url': avatar,
        'rating': max(0, min(5, rating)),
        'is_featured': _bool(t, 'is_featured'),
        'order': _int(t, 'order', 'display_order'),
    }
    # Compatibility aliases -- themes written against older/alternate
    # field names keep working without a template rewrite.
    canonical.update({
        'author_name': name,
        'client_name': name,
        'designation': role,
        'content': message,
        'quote': message,
        'stars': canonical['rating'],
        'image_url': avatar,
    })
    return canonical


def serialize_skill(s: Any) -> dict:
    return {
        'name': _str(s, 'name'),
        'level': _int(s, 'proficiency', 'level', default=80),
        'category': _str(s, 'category', default='General'),
        'icon': _str(s, 'icon'),
        'color': _str(s, 'color'),
        'order': _int(s, 'order', 'display_order'),
        'is_visible': _bool(s, 'is_visible', default=True),
    }


def serialize_project(p: Any) -> dict:
    return {
        'slug': _str(p, 'slug'),
        'title': _str(p, 'title'),
        'description': _str(p, 'description'),
        'description_short': _str(p, 'description_short', 'short_description'),
        'image_url': _str(p, 'image', 'image_url', 'cover_image'),
        'image_alt': _str(p, 'image_alt'),
        'before_image': _str(p, 'before_image'),
        'before_image_alt': _str(p, 'before_image_alt'),
        'after_image': _str(p, 'after_image'),
        'after_image_alt': _str(p, 'after_image_alt'),
        'category': _str(p, 'category', default='Web App'),
        'demo_url': _str(p, 'live_url', 'demo_url') or None,
        'github_url': _str(p, 'github_url', 'repo_url') or None,
        'prototype_url': _str(p, 'prototype_url') or None,
        'problem_statement': _str(p, 'problem_statement'),
        'solution_overview': _str(p, 'solution_overview'),
        'outcome_summary': _str(p, 'outcome_summary'),
        'client_quote': _str(p, 'client_quote'),
        'client_name': _str(p, 'client_name'),
        'client_role': _str(p, 'client_role'),
        'meta_title': _str(p, 'meta_title'),
        'meta_description': _str(p, 'meta_description'),
        'case_study_enabled': _bool(p, 'case_study_enabled', default=True),
        'tech_stack': _list(p, 'tags', 'tech_stack', 'technologies'),
        'framework': _str(p, 'framework'),
        'language': _str(p, 'language'),
        'status': _str(p, 'status', default='published'),
        'is_featured': _bool(p, 'is_featured'),
        'view_count': _int(p, 'view_count'),
        'like_count': _int(p, 'like_count'),
        'date_completed': _date_iso(p, 'date_completed'),
        'order': _int(p, 'order'),
    }


def serialize_service(s: Any) -> dict:
    return {
        'name': _str(s, 'title', 'name'),
        'subtitle': _str(s, 'subtitle'),
        'description': _str(s, 'description'),
        'icon': _str(s, 'icon', default='lucide:briefcase'),
        'features': _list(s, 'features'),
        'is_visible': _bool(s, 'is_visible', default=True),
        'order': _int(s, 'display_order', 'order'),
    }


def serialize_certificate(c: Any) -> dict:
    return {
        'title': _str(c, 'title'),
        'issuer': _str(c, 'issuer'),
        'description': _str(c, 'description'),
        'credential_id': _str(c, 'credential_id'),
        'verification_url': _str(c, 'verification_url'),
        'image_path': _str(c, 'image_path'),
        'badge_path': _str(c, 'badge_path'),
        'issue_date': _date_iso(c, 'issue_date'),
        'expiration_date': _date_iso(c, 'expiration_date'),
        'skills': _list(c, 'skills'),
        'is_featured': _bool(c, 'is_featured'),
        'is_expired': _bool(c, 'is_expired'),
        'order': _int(c, 'display_order', 'order'),
    }


def serialize_social_links(social: Any) -> dict:
    """`Profile.social_links` is a JSON column -> plain dict already,
    but normalize key naming so themes don't need to know both
    'x'/'twitter' or 'github_url'/'github' variants."""
    social = social or {}
    get = social.get if isinstance(social, Mapping) else (lambda *_: None)
    return {
        'github': get('github') or get('github_url'),
        'linkedin': get('linkedin') or get('linkedin_url'),
        'twitter': get('twitter') or get('x') or get('twitter_url'),
        'facebook': get('facebook') or get('facebook_url'),
        'instagram': get('instagram'),
        'youtube': get('youtube'),
        'dribbble': get('dribbble'),
        'behance': get('behance'),
        'website': get('website') or get('website_url'),
    }


def serialize_experience(e: Any) -> dict:
    """Normalize WorkExperience timeline rows for all portfolio themes."""
    role = _str(e, 'role', 'title', 'position')
    company = _str(e, 'company', 'organization')
    is_current = _bool(e, 'is_current')
    start_date = _date_iso(e, 'start_date')
    end_date = _date_iso(e, 'end_date')
    date_range = _str(e, 'date_range')
    year = _str(e, 'year')
    if not year and start_date:
        year = start_date[:4]
    return {
        'role': role,
        'title': role,
        'position': role,
        'company': company,
        'organization': company,
        'type': _str(e, 'employment_type', 'type', default='Work'),
        'employment_type': _str(e, 'employment_type', 'type', default='Work'),
        'location': _str(e, 'location'),
        'start_date': start_date,
        'end_date': end_date,
        'is_current': is_current,
        'date_range': date_range or ' – '.join([v for v in [start_date or '', 'Present' if is_current else (end_date or '')] if v]),
        'year': year,
        'description': _str(e, 'description'),
        'achievements': _list(e, 'achievements_list', 'achievements'),
        'technologies': _list(e, 'technologies_list', 'technologies', 'tech_stack'),
        'tech_stack': _list(e, 'technologies_list', 'technologies', 'tech_stack'),
        'icon': _str(e, 'icon', default='lucide:briefcase-business'),
        'order': _int(e, 'display_order', 'order'),
        'is_visible': _bool(e, 'is_visible', default=True),
    }


def serialize_tenant_branding(profile: Any, stats: Optional[dict] = None) -> dict:
    """Profile fields that are branding/identity, not portfolio content --
    kept separate from serialize_portfolio so admin-panel and public-site
    consumers can pull just branding without dragging in projects/skills."""
    stats = stats or {}
    name = _str(profile, 'name', default='Portfolio owner')
    return {
        'name': name,
        'initials': _initials(name),
        'title': _str(profile, 'title'),
        'subtitle': _str(profile, 'subtitle'),
        'meta_title': _str(profile, 'meta_title'),
        'meta_description': _str(profile, 'meta_description'),
        'og_image': _str(profile, 'og_image'),
        'profile_image_alt': _str(profile, 'profile_image_alt'),
        'seo_keywords': _str(profile, 'seo_keywords'),
        'seo_indexable': _bool(profile, 'seo_indexable', default=True),
        'avatar_url': _str(profile, 'profile_image', 'avatar_url'),
        'tenant_slug': _str(profile, 'tenant_slug', default='default'),
        'selected_theme': _str(profile, 'selected_theme', default='default'),
        'plan': _str(profile, 'plan', default='Basic'),
        'stats': {
            'projects_count': _int(stats, 'projects_count') if isinstance(stats, Mapping) else 0,
            'years_experience': _int(profile, 'years_experience'),
            'clients_count': _int(profile, 'clients_count'),
        },
    }


def _initials(name: str) -> str:
    parts = [p for p in (name or '').split() if p]
    if not parts:
        return ''
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


# ─────────────────────────────────────────────────────────────────
# Bulk / context-level helpers
# ─────────────────────────────────────────────────────────────────

def serialize_portfolio(
    profile: Any,
    projects: Optional[Iterable] = None,
    skills: Optional[Iterable] = None,
    services: Optional[Iterable] = None,
    testimonials: Optional[Iterable] = None,
    certificates: Optional[Iterable] = None,
    experiences: Optional[Iterable] = None,
    stats: Optional[dict] = None,
    tenant_slug: str = 'default',
    contact_url: str = '#',
) -> dict:
    """
    Single entry point replacing app.theme_context.build_portfolio_view.
    Returns ONE JSON-safe dict tree -- safe to pass straight to
    `render_template`, `tojson`, or an API response with no further
    transformation.
    """
    profile = profile or {}
    name = _str(profile, 'name', default='Portfolio owner')
    social = serialize_social_links(_get(profile, 'social_links', default={}))
    branding = serialize_tenant_branding(profile, stats)

    name_parts = name.split(' ', 1)
    if len(name_parts) == 1:
        name_parts.append('')

    project_list = [serialize_project(p) for p in (projects or [])]
    skill_list = [serialize_skill(s) for s in (skills or [])]
    service_list = [serialize_service(s) for s in (services or [])]
    testimonial_list = [serialize_testimonial(t) for t in (testimonials or [])]
    certificate_list = [serialize_certificate(c) for c in (certificates or [])]
    experience_list = [serialize_experience(e) for e in (experiences or [])]

    # Group skills by category -- themes historically render skills
    # grouped, not flat.
    grouped: dict = {}
    for sk in skill_list:
        grouped.setdefault(sk['category'], []).append(sk)
    skills_grouped = [
        {'category': cat, 'skills': items} for cat, items in grouped.items()
    ]

    portfolio = {
        **branding,
        'bio': _str(profile, 'bio'),
        'bio_plain': _str(profile, 'bio_short') or _str(profile, 'bio'),
        'bio_extended': _str(profile, 'bio'),
        'email': _str(profile, 'email'),
        'phone': _str(profile, 'phone'),
        'location': _str(profile, 'location'),
        'resume_url': _str(profile, 'resume_url'),
        'available_for_work': _bool(profile, 'is_available'),
        'availability_text': _str(profile, 'availability_status'),
        'github_url': social['github'],
        'linkedin_url': social['linkedin'],
        'twitter_url': social['twitter'],
        'facebook_url': social['facebook'],
        'social_links': social,
        'skills': skills_grouped,
        'skills_flat': skill_list,
        'projects': project_list,
        'services': service_list,
        'testimonials': testimonial_list,
        'certificates': certificate_list,
        'experiences': experience_list,
        'typing_phrases': [_str(profile, 'hero_tagline') or 'Building digital experiences.'],
        'footer_tagline': _str(profile, 'subtitle'),
        'contact_form_action': contact_url,
        'slug': tenant_slug,
    }

    categories = sorted({p['category'] for p in project_list if p['category']})

    return {
        'portfolio': portfolio,
        'name_parts': name_parts,
        'categories': categories,
    }


def normalize_theme_context(context: dict) -> dict:
    """
    Final safety net before render_template(). Walks the top-level
    context dict and guarantees every value is JSON-safe (dict/list/
    str/int/float/bool/None). Anything else (stray ORM object,
    SimpleNamespace slipped in from a route that bypassed the
    serializers above) is coerced to a dict via vars()/__dict__ or
    dropped with a warning -- it never reaches Jinja un-normalized.
    """
    safe = {}
    for key, value in context.items():
        safe[key] = _normalize_value(value)
    return safe


def _normalize_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {k: _normalize_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_normalize_value(v) for v in value]
    if hasattr(value, '__dict__'):
        # SimpleNamespace / stray ORM object that skipped a serializer.
        return {k: _normalize_value(v) for k, v in vars(value).items()
                 if not k.startswith('_')}
    return str(value)  # last resort: never let a raw object reach Jinja


def safe_tojson(value: Any) -> str:
    """Jinja filter-safe JSON dump. Use in templates as
    `{{ portfolio | safe_tojson }}` for client-side hydration --
    never `{{ portfolio | tojson }}` directly on a pre-serializer
    object, since SimpleNamespace/ORM instances aren't JSON
    serializable and raise mid-render."""
    return json.dumps(_normalize_value(value), default=str)
