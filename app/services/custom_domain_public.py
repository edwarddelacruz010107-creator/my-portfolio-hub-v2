"""Public portfolio rendering helpers for verified custom-domain hosts."""
from __future__ import annotations

from flask import abort, current_app, g, jsonify, render_template, request, url_for
from sqlalchemy import or_

from app import db
from app.models.tenant_data import Profile, Project, Skill, Testimonial, Service, Certificate, WorkExperience
from app.services.billing import subscription_access_status, is_in_grace_period
from app.utils import log_activity


def _load_public_context(tenant_slug: str):
    profile = Profile.query.filter_by(tenant_slug=tenant_slug).first()
    if not profile:
        abort(404)
    g.tenant_slug = tenant_slug
    g.tenant_profile = profile
    return profile


def render_custom_domain_portfolio(domain_record):
    """Render the tenant portfolio at '/' on a verified custom domain."""
    return _render_hosted_portfolio(
        tenant=domain_record.tenant_slug,
        host_kind='custom_domain',
        public_host=domain_record.normalized_domain,
    )


def render_subdomain_portfolio(tenant_record):
    """Render the tenant portfolio at '/' on its configured platform subdomain."""
    return _render_hosted_portfolio(
        tenant=tenant_record.slug,
        host_kind='tenant_subdomain',
        public_host=request.host.split(':', 1)[0],
    )


def _render_hosted_portfolio(*, tenant: str, host_kind: str, public_host: str):
    profile = _load_public_context(tenant)

    if profile and profile.is_expired():
        profile.enforce_expiry(commit=True)
        return render_template(
            'tenant/suspended.html',
            profile=profile,
            tenant_slug=tenant,
            license_status=profile.license_status(),
            subscription_status=subscription_access_status(profile),
            trial_days_left=profile.trial_days_remaining(),
            in_grace=is_in_grace_period(profile),
        ), 402

    all_projects = Project.published_for_tenant(tenant).all()
    featured_projects = [p for p in all_projects if p.is_featured]
    other_projects = [p for p in all_projects if not p.is_featured]

    skills = (
        Skill.query
        .filter(
            Skill.tenant_slug == tenant,
            or_(Skill.is_visible == True, Skill.is_visible.is_(None)),
        )
        .order_by(Skill.category.asc(), Skill.order.asc())
        .all()
    )
    testimonials = (
        Testimonial.query
        .filter_by(is_visible=True, tenant_slug=tenant)
        .order_by(Testimonial.order.asc())
        .all()
    )
    certificates = (
        Certificate.query
        .filter_by(is_visible=True, tenant_slug=tenant)
        .order_by(Certificate.display_order.asc(), Certificate.id.asc())
        .all()
    )
    services = (
        Service.query
        .filter_by(is_visible=True, tenant_slug=tenant)
        .order_by(Service.display_order.asc())
        .all()
    )
    experiences = (
        WorkExperience.query
        .filter_by(is_visible=True, tenant_slug=tenant)
        .order_by(WorkExperience.display_order.asc(), WorkExperience.start_date.desc(), WorkExperience.id.desc())
        .all()
    )

    skills_by_category = {}
    for skill in skills:
        skills_by_category.setdefault(skill.category, []).append(skill)

    categories = sorted({p.category for p in featured_projects + other_projects if p.category})
    stats = {
        'projects_count': Project.query.filter_by(status='published', tenant_slug=tenant).count(),
        'years_experience': profile.get_years_experience() if profile else 0,
        'clients_count': profile.clients_count if profile else 0,
    }

    from app.theme_engine import get_theme_engine
    from app.theme_context import build_portfolio_view

    portfolio_view, name_parts, categories_themed = build_portfolio_view(
        profile,
        projects=featured_projects + other_projects,
        skills_by_category=skills_by_category,
        services=services,
        testimonials=testimonials,
        certificates=certificates,
        experiences=experiences,
        stats=stats,
        tenant_slug=tenant,
        contact_url=url_for('public.contact'),
    )

    return get_theme_engine().render(
        profile,
        'index.html',
        profile=profile,
        portfolio=portfolio_view,
        name_parts=name_parts,
        featured_projects=featured_projects,
        other_projects=other_projects,
        skills=skills,
        skills_by_category=skills_by_category,
        testimonials=testimonials,
        certificates=certificates,
        services=services,
        experiences=experiences,
        stats=stats,
        categories=categories,
        tenant_slug=tenant,
        contact_url=url_for('public.contact'),
        is_root_domain=False,
        is_custom_domain=host_kind == 'custom_domain',
        is_subdomain=host_kind == 'tenant_subdomain',
        custom_domain=public_host if host_kind == 'custom_domain' else None,
        trial_days_left=profile.trial_days_remaining() if profile else 0,
        license_status=profile.license_status() if profile else 'unlicensed',
    )


def render_custom_domain_project(domain_record, slug: str):
    """Render /project/<slug> on a verified custom domain."""
    return _render_hosted_project(
        tenant=domain_record.tenant_slug,
        slug=slug,
        host_kind='custom_domain',
        public_host=domain_record.normalized_domain,
    )


def render_subdomain_project(tenant_record, slug: str):
    """Render /project/<slug> on a tenant platform subdomain."""
    return _render_hosted_project(
        tenant=tenant_record.slug,
        slug=slug,
        host_kind='tenant_subdomain',
        public_host=request.host.split(':', 1)[0],
    )


def _render_hosted_project(*, tenant: str, slug: str, host_kind: str, public_host: str):
    profile = _load_public_context(tenant)

    if profile and profile.is_expired():
        profile.enforce_expiry(commit=True)
        return render_template(
            'tenant/suspended.html',
            profile=profile,
            tenant_slug=tenant,
            license_status=profile.license_status(),
            trial_days_left=profile.trial_days_remaining(),
        ), 402

    project = (
        Project.query
        .filter_by(slug=slug, status='published', tenant_slug=tenant)
        .filter(Project.case_study_enabled.is_(True))
        .first_or_404()
    )
    project.increment_views()
    db.session.commit()
    try:
        from app.services.notification_service import publish_project_view_milestone
        publish_project_view_milestone(project)
    except Exception:
        current_app.logger.exception('Project view milestone notification failed: project_id=%s', project.id)

    related = (
        Project.query
        .filter(
            Project.status == 'published',
            Project.id != project.id,
            Project.category == project.category,
            Project.tenant_slug == tenant,
        )
        .order_by(Project.order.asc())
        .limit(3)
        .all()
    )

    return render_template(
        'main/project.html',
        project=project,
        profile=profile,
        related=related,
        tenant_slug=tenant,
        is_custom_domain=host_kind == 'custom_domain',
        is_subdomain=host_kind == 'tenant_subdomain',
        custom_domain=public_host if host_kind == 'custom_domain' else None,
    )


def handle_custom_domain_contact(domain_record):
    """Handle /contact POST for a verified custom domain."""
    return _handle_hosted_contact(domain_record.tenant_slug, source='custom_domain')


def handle_subdomain_contact(tenant_record):
    """Handle /contact POST for a tenant platform subdomain."""
    return _handle_hosted_contact(tenant_record.slug, source='tenant_subdomain')


def _handle_hosted_contact(tenant_slug: str, *, source: str):
    profile = _load_public_context(tenant_slug)

    if request.form.get('website', ''):
        return jsonify(status='success', message='Your message has been sent.')

    raw = request.form
    name = raw.get('name', '').strip()
    email = raw.get('email', '').strip()
    subject = raw.get('subject', '').strip()
    message = raw.get('message', '').strip()
    sub_id = raw.get('submission_id', '').strip()[:80]

    from app.request_security import get_client_ip
    ip = get_client_ip()

    from app.services.communication.contact_service import process_contact_submission
    result = process_contact_submission(
        tenant_slug=tenant_slug,
        name=name,
        email=email,
        subject=subject,
        message=message,
        ip_address=ip,
        user_agent=(request.headers.get('User-Agent') or '')[:300],
        submission_id=sub_id or None,
        source=source,
    )

    if not result.success:
        return jsonify(status='error', message=result.delivery_error or 'Submission failed.'), 400

    log_activity('create', 'inquiry', name, f'{source} contact from {email} to tenant {tenant_slug!r}')
    tenant_display = profile.name if profile and getattr(profile, 'name', None) else tenant_slug.replace('-', ' ').title()
    return jsonify(status='success', message=f"Message sent to {tenant_display}. I'll get back to you soon!")
