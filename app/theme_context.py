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

import os

from flask import current_app, url_for

from app.services.theme_serializers import (
    serialize_project,
    serialize_skill,
    serialize_service,
    serialize_testimonial,
    serialize_certificate,
    serialize_experience,
    serialize_social_links,
)


def _initials(name: str) -> str:
    parts = [p for p in (name or '').split() if p]
    if not parts:
        return ''
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _normalize_upload_reference(value: str, subfolder: str) -> tuple[str, str] | None:
    value = (value or '').strip() if isinstance(value, str) else ''
    if not value or value.lower() in {'none', 'null', 'undefined'}:
        return None
    if any(ch in value for ch in ('\x00', '\r', '\n')):
        return None

    normalized = value.replace('\\', '/')
    if normalized.startswith('/static/uploads/'):
        normalized = normalized[len('/static/uploads/'):]
    elif normalized.startswith('static/uploads/'):
        normalized = normalized[len('static/uploads/'):]
    elif normalized.startswith('/uploads/'):
        normalized = normalized[len('/uploads/'):]
    elif normalized.startswith('uploads/'):
        normalized = normalized[len('uploads/'):]
    else:
        if normalized.startswith('/') or '..' in normalized:
            return None
        prefix = f'{subfolder}/'
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
        normalized = f'{subfolder}/{normalized}'

    parts = normalized.split('/', 1)
    if len(parts) != 2:
        return None
    folder, filename = parts[0], parts[1]
    allowed_folders = {'profiles', 'projects', 'avatars', 'billing', 'certificates'}
    if folder not in allowed_folders or not filename or '..' in filename or filename.startswith('/'):
        return None
    return folder, filename


def _local_upload_exists(folder: str, filename: str) -> bool:
    if current_app.config.get('SKIP_UPLOAD_EXISTENCE_CHECK'):
        return True
    try:
        upload_root = current_app.config.get('UPLOAD_FOLDER')
        if not upload_root:
            static_folder = current_app.static_folder or ''
            upload_root = os.path.join(static_folder, 'uploads')
        root = os.path.abspath(upload_root)
        folder_path = os.path.abspath(os.path.join(root, folder))
        target = os.path.abspath(os.path.join(folder_path, filename))
        if not (target == folder_path or target.startswith(folder_path + os.sep)):
            return False
        return os.path.isfile(target)
    except Exception:
        return True


def _upload_url(value: str, subfolder: str) -> str:
    """Return a safe public URL for uploaded media passed to theme templates.

    Supports remote object-storage URLs and local filenames served through the
    app-level /uploads route. The route reads from UPLOAD_FOLDER, so mounted
    persistent disks keep profile/project/certificate photos after redeploy.
    """
    value = (value or '').strip() if isinstance(value, str) else ''
    if not value or value.lower() in {'none', 'null', 'undefined'}:
        return ''
    if any(ch in value for ch in ('\x00', '\r', '\n')):
        return ''
    if value.startswith(('http://', 'https://', 'data:')):
        return value

    normalized = _normalize_upload_reference(value, subfolder)
    if not normalized:
        return ''
    folder, filename = normalized

    public_base = (current_app.config.get('UPLOAD_PUBLIC_BASE_URL') or '').rstrip('/')
    if public_base:
        return f'{public_base}/{folder}/{filename}'

    if not _local_upload_exists(folder, filename):
        return ''
    try:
        return url_for('uploaded_media', subfolder=folder, filename=filename)
    except Exception:
        return f'/uploads/{folder}/{filename}'


def build_portfolio_view(
    profile,
    projects=None,
    skills_by_category=None,
    services=None,
    testimonials=None,
    certificates=None,
    experiences=None,
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
    experiences = experiences or []
    skills_by_category = skills_by_category or {}
    stats = stats or {}

    name = (getattr(profile, 'name', '') or 'Your Name').strip()
    social = serialize_social_links(getattr(profile, 'social_links', None) or {})

    name_parts = name.split(' ', 1)
    if len(name_parts) == 1:
        name_parts.append('')

    project_views = []
    try:
        from app.services.custom_domain_service import (
            tenant_project_public_url,
            tenant_portfolio_public_url,
        )
    except Exception:
        tenant_project_public_url = None
        tenant_portfolio_public_url = None

    for p in projects:
        view = serialize_project(p)
        project_slug = view.get('slug') or getattr(p, 'slug', '')
        if project_slug and tenant_project_public_url:
            view['case_study_url'] = tenant_project_public_url(tenant_slug, project_slug)
            view['url'] = view['case_study_url']
        else:
            view['case_study_url'] = ''
            view['url'] = ''
        view['image_raw'] = view.get('image_url', '')
        view['image_url'] = _upload_url(view.get('image_url', ''), 'projects')
        project_views.append(view)

    skills_grouped = []
    for category, skill_list in skills_by_category.items():
        items = [serialize_skill(s) for s in skill_list]
        skills_grouped.append({
            'category': category,
            'name': category,      # compatibility alias for Futuristic Cyber-style templates
            'skills': items,
            'items': items,        # compatibility alias for Futuristic Cyber-style templates
        })

    stats_list = [
        {'label': 'Projects', 'value': stats.get('projects_count', 0) or 0},
        {'label': 'Years Experience', 'value': stats.get('years_experience', 0) or 0},
        {'label': 'Clients', 'value': stats.get('clients_count', 0) or 0},
    ]

    service_views = [serialize_service(s) for s in services]
    testimonial_views = [serialize_testimonial(t) for t in testimonials]
    for testimonial in testimonial_views:
        testimonial['avatar_raw'] = testimonial.get('avatar_url', '')
        testimonial['avatar_url'] = _upload_url(testimonial.get('avatar_url', ''), 'profiles')

    experience_views = [serialize_experience(e) for e in experiences]

    certificate_views = [serialize_certificate(c) for c in certificates]
    for certificate in certificate_views:
        certificate['image_raw'] = certificate.get('image_path', '')
        certificate['badge_raw'] = certificate.get('badge_path', '')
        certificate['image_url'] = _upload_url(certificate.get('image_path', ''), 'certificates')
        certificate['badge_url'] = _upload_url(certificate.get('badge_path', ''), 'certificates')

    bio = getattr(profile, 'bio', '') or '' if profile else ''
    bio_short = getattr(profile, 'bio_short', '') or '' if profile else ''

    portfolio = {
        'name': name,
        'title': getattr(profile, 'title', '') or '' if profile else '',
        'bio': bio,
        'bio_plain': bio_short or bio,
        'bio_extended': bio,
        'avatar_url': _upload_url(getattr(profile, 'profile_image', '') or '', 'profiles') if profile else '',
        'avatar_raw': getattr(profile, 'profile_image', '') or '' if profile else '',
        'phone': getattr(profile, 'phone', '') or '' if profile else '',
        'subtitle': getattr(profile, 'subtitle', '') or '' if profile else '',
        'hero_tagline': getattr(profile, 'hero_tagline', '') or '' if profile else '',
        'slug': tenant_slug,
        'public_url': tenant_portfolio_public_url(tenant_slug) if 'tenant_portfolio_public_url' in locals() and tenant_portfolio_public_url else '',
        'email': getattr(profile, 'email', '') or '' if profile else '',
        'location': getattr(profile, 'location', '') or '' if profile else '',
        'response_time': None,
        'github_url': social['github'],
        'linkedin_url': social['linkedin'],
        'twitter_url': social['twitter'],
        'facebook_url': social['facebook'],
        'instagram_url': social.get('instagram'),
        'youtube_url': social.get('youtube'),
        'dribbble_url': social.get('dribbble'),
        'behance_url': social.get('behance'),
        'social_links': social,
        'resume_url': getattr(profile, 'resume_url', '') or '' if profile else '',
        'available_for_work': bool(getattr(profile, 'is_available', False)) if profile else False,
        'availability_text': getattr(profile, 'availability_status', '') or '' if profile else '',
        'stats': stats_list,
        'skills': skills_grouped,
        'projects': project_views,
        'experiences': experience_views,
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
        'meta_title': getattr(profile, 'meta_title', '') or '' if profile else '',
        'meta_description': getattr(profile, 'meta_description', '') or '' if profile else '',
        'og_image': _upload_url(getattr(profile, 'og_image', '') or '', 'profiles') if profile else '',
        'tenant_slug': tenant_slug,
        'skills_flat': [skill for group in skills_grouped for skill in group.get('skills', [])],
        'education': [],
        'achievements': [],
        'website_url': social.get('website') or '',
        'color_cycle': ['#22c55e', '#38bdf8', '#a855f7', '#f59e0b'],
        'icon_cycle': ['lucide:code-2', 'lucide:layers', 'lucide:terminal', 'lucide:server'],
        'node_colors': ['green', 'blue', 'purple', 'amber'],
    }

    categories = sorted({p['category'] for p in project_views if p['category']})

    return portfolio, name_parts, categories
