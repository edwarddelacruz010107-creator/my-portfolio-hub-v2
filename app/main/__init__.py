"""
app/main/__init__.py — Legacy / root routes (v3.0)

IMPORTANT: All /<tenant_slug>/* routes have been moved to app/tenant/__init__.py.

This blueprint now only handles:
  GET  /  → redirected by app root route (not here)

The old tenant_portfolio, tenant_contact, tenant_project_detail,
tenant_admin_login, tenant_admin_root routes are REMOVED — they
are now handled by the tenant blueprint at /<tenant_slug>/*.

The old /project/<slug> and /contact routes are kept for legacy
backward compatibility (no tenant context = first/default profile).

SEO endpoints:
  GET /sitemap.xml  → canonical public platform, tenant, and case-study URLs
  GET /robots.txt   → crawler rules plus the current-host sitemap location
"""
import uuid
import logging
from datetime import timezone
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from flask import Blueprint, current_app, flash, render_template, request, jsonify, abort, url_for, redirect, Response, make_response
from flask_login import current_user, login_required
from sqlalchemy import or_
from werkzeug.utils import secure_filename

from app import csrf, db, limiter
from app.repositories import profile_repository, tenant_repository
from app.forms import PlanSelectionForm, PaymentUploadForm
from app.models.portfolio import (
    Profile, Project,
    Inquiry, Subscription, PaymentInstruction, PaymentSubmission,
    normalize_plan_name,
)
from app.security import FileUploadPolicy, log_security_event
from app.utils import BILLING_PLANS, is_paymongo_enabled, log_activity
from app.services.billing.currency import get_currency_settings

logger = logging.getLogger(__name__)
main   = Blueprint('main', __name__)


# ── SEO: robots.txt ───────────────────────────────────────────────────────────

@main.route('/robots.txt')
def robots_txt():
    """Serve crawler rules for the platform host or a verified custom domain."""
    base_url = request.host_url.rstrip('/')
    content = f"""User-agent: *
Allow: /
Allow: /favicon.ico
Allow: /static/
Allow: /uploads/

# Private application surfaces are excluded from crawling. Authorization is
# still enforced by the application; robots.txt is not a security boundary.
Disallow: /admin/
Disallow: /studio/
Disallow: /superadmin/
Disallow: /auth/
Disallow: /billing/
Disallow: /api/
Disallow: /heartbeat/
Disallow: /webhooks/
Disallow: /impersonate/

Sitemap: {base_url}/sitemap.xml
"""
    resp = make_response(content.strip() + '\n', 200)
    resp.headers['Content-Type'] = 'text/plain; charset=utf-8'
    resp.headers['Cache-Control'] = 'public, max-age=86400'
    return resp


# ── SEO: sitemap.xml ──────────────────────────────────────────────────────────

@main.route('/sitemap.xml')
def sitemap_xml():
    """Return canonical public URLs, scoped to a custom domain when present."""
    from datetime import datetime
    from app.models.portfolio import Tenant, Profile, Project
    from app.services.custom_domain_service import (
        resolve_verified_custom_domain,
        tenant_portfolio_public_url,
        tenant_project_public_url,
    )

    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    rows: list[dict] = []
    seen: set[str] = set()

    def add(loc: str, *, lastmod: str = today, changefreq: str = 'weekly', priority: str = '0.7') -> None:
        loc = (loc or '').strip()
        if not loc or loc in seen:
            return
        seen.add(loc)
        rows.append({
            'loc': loc,
            'lastmod': lastmod,
            'changefreq': changefreq,
            'priority': priority,
        })

    try:
        domain_record = resolve_verified_custom_domain(request.host)
        if domain_record is not None:
            tenants = [Tenant.query.filter_by(slug=domain_record.tenant_slug, status='active').first()]
        else:
            tenants = tenant_repository.list_by(status='active')
            add(url_for('root', _external=True), changefreq='daily', priority='1.0')
            for endpoint, frequency, priority in (
                ('public.explore', 'daily', '0.8'),
                ('public.projects', 'daily', '0.9'),
                ('public.themes', 'weekly', '0.8'),
                ('public.pricing', 'weekly', '0.8'),
                ('public.about_company', 'monthly', '0.7'),
                ('public.privacy', 'yearly', '0.3'),
                ('public.terms', 'yearly', '0.3'),
            ):
                add(url_for(endpoint, _external=True), changefreq=frequency, priority=priority)

        for tenant in [t for t in tenants if t is not None]:
            profile = profile_repository.get_by_tenant_id(tenant.id)
            if not profile or not bool(getattr(profile, 'seo_indexable', True)):
                continue
            profile_lastmod = (
                profile.updated_at.strftime('%Y-%m-%d')
                if getattr(profile, 'updated_at', None)
                else today
            )
            add(
                tenant_portfolio_public_url(tenant.slug, external=True),
                lastmod=profile_lastmod,
                changefreq='weekly',
                priority='1.0' if domain_record is not None else '0.9',
            )

            projects = (
                Project.query
                .filter_by(tenant_slug=tenant.slug, status='published')
                .filter(Project.case_study_enabled.is_(True))
                .order_by(Project.updated_at.desc(), Project.id.desc())
                .all()
            )
            for project in projects:
                if not project.slug:
                    continue
                project_lastmod = (
                    project.updated_at.strftime('%Y-%m-%d')
                    if getattr(project, 'updated_at', None)
                    else profile_lastmod
                )
                add(
                    tenant_project_public_url(tenant.slug, project.slug, external=True),
                    lastmod=project_lastmod,
                    changefreq='monthly',
                    priority='0.8',
                )
    except Exception as exc:
        logger.warning('sitemap_xml: DB query failed — returning available URLs: %s', exc)

    xml_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for row in rows:
        xml_lines.extend([
            '  <url>',
            f'    <loc>{xml_escape(row["loc"])}</loc>',
            f'    <lastmod>{xml_escape(row["lastmod"])}</lastmod>',
            f'    <changefreq>{row["changefreq"]}</changefreq>',
            f'    <priority>{row["priority"]}</priority>',
            '  </url>',
        ])
    xml_lines.append('</urlset>')

    resp = make_response('\n'.join(xml_lines), 200)
    resp.headers['Content-Type'] = 'application/xml; charset=utf-8'
    resp.headers['Cache-Control'] = 'public, max-age=21600'
    return resp


@main.route('/contact', methods=['GET', 'POST'])
@limiter.limit(lambda: current_app.config.get('RATELIMIT_CONTACT_FORM', "5 per minute; 20 per hour"))
def contact():
    """
    Default portfolio contact endpoint (v5.8).
    Delegates to contact_service — same pipeline as all tenant contacts.
    Submissions are routed to the default tenant administrator's email.
    """
    if request.method == 'GET':
        return redirect(url_for('root'))

    # Honeypot
    if request.form.get('website', ''):
        return jsonify(status='success', message='Your message has been sent.')

    raw = request.form
    name       = raw.get('name', '').strip()
    email      = raw.get('email', '').strip()
    subject    = raw.get('subject', '').strip()
    message    = raw.get('message', '').strip()
    sub_id     = raw.get('submission_id', '').strip()[:80]

    from app.request_security import get_client_ip
    ip = get_client_ip()

    from app.services.contact_service import process_contact_submission
    result = process_contact_submission(
        tenant_slug='default',
        name=name,
        email=email,
        subject=subject,
        message=message,
        phone=raw.get('phone', '').strip(),
        company=raw.get('company', '').strip(),
        source='legacy_contact',
        ip_address=(ip or '')[:45],
        user_agent=(request.headers.get('User-Agent') or '')[:300],
        submission_id=sub_id or None,
    )

    if not result.success:
        return jsonify(status='error', message=result.delivery_error or 'Submission failed.'), 400

    return jsonify(
        status='success',
        message="Your message has been sent. I'll get back to you soon!",
    )


@main.route('/project/<slug>')
def project_detail(slug: str):
    """
    Legacy /project/<slug> route — resolves without tenant context.
    Looks up the project and redirects to the tenant-scoped URL.
    """
    project = (
        Project.query
        .filter_by(slug=slug, status='published')
        .filter(Project.case_study_enabled.is_(True))
        .first_or_404()
    )
    # Redirect to the best canonical public URL. If the tenant has a verified
    # primary custom domain, prefer /project/<slug> on that domain; otherwise
    # keep the existing tenant-scoped platform URL.
    from app.services.custom_domain_service import tenant_project_public_url
    return redirect(
        tenant_project_public_url(project.tenant_slug or 'default', slug),
        code=301,
    )


def _redirect_default_billing():
    return redirect(url_for('root'))


def _load_default_billing_profile():
    profile = profile_repository.get_by_tenant_slug('default')
    if not profile:
        abort(404)
    return profile


def _default_billing_access_allowed() -> bool:
    """
    SEC-FIX (Finding #1): the routes below operate on the hardcoded
    'default' tenant's subscription record and can trigger live PayMongo
    checkout sessions. They were previously reachable with no
    authentication at all.

    Mirrors app.tenant._tenant_billing_access_allowed() and
    app.admin._billing_access_check() for consistency: only the
    'default' tenant's own admin, or a superadmin, may view/mutate it.
    """
    if not current_user.is_authenticated:
        return False
    if current_user.is_superadmin:
        return True
    return bool(current_user.is_admin and current_user.tenant_slug == 'default')


def _default_billing_instructions(profile):
    if profile is None or profile.tenant is None:
        return []
    return (
        PaymentInstruction.query
        .filter(
            or_(
                PaymentInstruction.tenant_id == profile.tenant_id,
                PaymentInstruction.tenant_id.is_(None),
            ),
            PaymentInstruction.is_active == True,
        )
        .order_by(PaymentInstruction.created_at.asc())
        .all()
    )


@main.route('/billing')
@login_required
def billing():
    return redirect(url_for('main.billing_plans'))


@main.route('/billing/plans', methods=['GET', 'POST'])
@login_required
def billing_plans():
    if not _default_billing_access_allowed():
        abort(403)
    profile = _load_default_billing_profile()
    subscription = profile.current_subscription()
    form = PlanSelectionForm(plan=normalize_plan_name(subscription.plan if subscription else profile.plan or 'Basic'))

    # Check for payment success/failure from PayMongo
    status = request.args.get('status')
    if status == 'success':
        flash('Payment received! Your subscription is now active.', 'success')
    elif status == 'failed':
        flash('Payment failed. Please try again or choose a different payment method.', 'danger')
    elif status == 'cancelled':
        flash('Payment was cancelled. You can try again anytime.', 'warning')

    if request.method == 'POST':
        selected_plan = request.form.get('plan')
        billing_cycle = request.form.get('billing_cycle', 'monthly')
        payment_method = request.form.get('payment_method', 'card')
        action = request.form.get('action', 'save_local')

        # Normalize plan name
        selected_plan = normalize_plan_name(selected_plan or profile.plan or 'Basic')

        # Update or create subscription record
        if not subscription:
            subscription = Subscription(
                tenant=profile.tenant,
                plan=selected_plan,
                billing_cycle=billing_cycle,
                status='pending',
            )
            db.session.add(subscription)
        else:
            subscription.plan = selected_plan
            subscription.billing_cycle = billing_cycle
            if subscription.status != 'active':
                subscription.status = 'pending'
        
        db.session.commit()

        # If PayMongo is enabled, initiate payment
        if is_paymongo_enabled() and get_currency_settings().get('display_currency') == 'PHP' and action == 'save_local':
            from app.utils.paymongo import create_payment_source
            
            payment_info = create_payment_source(
                payment_type=payment_method,
                billing_cycle=billing_cycle,
                tenant_id=profile.tenant_id,
                tenant_slug=profile.tenant_slug,
            )
            
            if payment_info and payment_info.get('checkout_url'):
                # Store the checkout session for reference
                subscription.paymongo_customer_id = payment_info.get('session_id')
                db.session.commit()
                return redirect(payment_info['checkout_url'])
            else:
                flash('Could not initiate PayMongo payment. Please try again.', 'danger')
                return redirect(url_for('main.billing_plans'))
        else:
            # Local-only payment method
            flash(f'Billing plan updated to {selected_plan} ({billing_cycle}).', 'success')
            return redirect(url_for('main.billing_plans'))

    return render_template(
        'billing/plans.html',
        profile=profile,
        subscription=subscription,
        form=form,
        plans=BILLING_PLANS,
        show_billing_tabs=False,
    )


@main.route('/billing/payment', methods=['GET', 'POST'])
@login_required
def billing_payment():
    return redirect(url_for('main.billing_plans'))


@main.route('/billing/history')
@login_required
def billing_history():
    return redirect(url_for('main.billing_plans'))


# index route removed — handled by app root '/' → tenant blueprint
