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

v3.9-SEO:
  GET /sitemap.xml     → dynamic XML sitemap covering all public tenants
  GET /robots.txt      → instructs crawlers and links to sitemap
  GET /<slug>/sitemap.xml  → per-tenant sitemap (served via tenant blueprint)
"""
import uuid
import logging
from datetime import timezone
from pathlib import Path

from flask import Blueprint, current_app, flash, render_template, request, jsonify, abort, url_for, redirect, Response, make_response
from flask_login import current_user, login_required
from sqlalchemy import or_
from werkzeug.utils import secure_filename

from app import csrf, db, limiter
from app.forms import PlanSelectionForm, PaymentUploadForm
from app.models.portfolio import (
    Profile, Project,
    Inquiry, Subscription, PaymentInstruction, PaymentSubmission,
    normalize_plan_name,
)
from app.security import FileUploadPolicy, log_security_event
from app.utils import BILLING_PLANS, is_paymongo_enabled, log_activity

logger = logging.getLogger(__name__)
main   = Blueprint('main', __name__)


# ── SEO: robots.txt ───────────────────────────────────────────────────────────

@main.route('/robots.txt')
def robots_txt():
    """
    Serve a dynamic robots.txt that:
      • Allows all crawlers on public portfolio pages.
      • Blocks crawlers on admin, billing, auth, and superadmin routes.
      • Points to the sitemap.
    """
    base_url = request.host_url.rstrip('/')
    sitemap_url = f"{base_url}/sitemap.xml"

    content = f"""User-agent: *
Allow: /

# Admin and internal routes — not for indexing
Disallow: /admin/
Disallow: /superadmin/
Disallow: /auth/
Disallow: /billing/
Disallow: /heartbeat/
Disallow: /webhooks/

# Sitemap
Sitemap: {sitemap_url}
"""
    resp = make_response(content.strip(), 200)
    resp.headers['Content-Type'] = 'text/plain; charset=utf-8'
    resp.headers['Cache-Control'] = 'public, max-age=86400'  # 24h
    return resp


# ── SEO: sitemap.xml ──────────────────────────────────────────────────────────

@main.route('/sitemap.xml')
def sitemap_xml():
    """
    Dynamic XML sitemap covering:
      • Root portfolio page for every active tenant
      • All published projects for every active tenant

    Priority/changefreq tuning:
      homepage  → priority 1.0, weekly
      project   → priority 0.8, monthly

    Cached for 6 hours so heavy DB queries don't run on every crawl.
    Returns 200 even if DB is unavailable (empty sitemap, no 500).
    """
    from datetime import datetime

    urls: list[dict] = []

    try:
        from app.models.portfolio import Tenant, Profile, Project

        # All active tenants with a published profile
        tenants = (
            Tenant.query
            .filter_by(status='active')
            .all()
        )

        for tenant in tenants:
            profile = Profile.query.filter_by(tenant_id=tenant.id).first()
            if not profile:
                continue

            # Tenant homepage
            if tenant.is_root_domain if hasattr(tenant, 'is_root_domain') else False:
                homepage_url = request.host_url.rstrip('/')
            else:
                homepage_url = url_for(
                    'tenant.portfolio',
                    tenant_slug=tenant.slug,
                    _external=True,
                )

            lastmod = (
                profile.updated_at.strftime('%Y-%m-%d')
                if hasattr(profile, 'updated_at') and profile.updated_at
                else datetime.now(timezone.utc).strftime('%Y-%m-%d')
            )

            urls.append({
                'loc':        homepage_url,
                'lastmod':    lastmod,
                'changefreq': 'weekly',
                'priority':   '1.0',
            })

            # Published projects for this tenant
            projects = (
                Project.query
                .filter_by(tenant_slug=tenant.slug, status='published')
                .order_by(Project.id.desc())
                .all()
            )
            for project in projects:
                if not project.slug:
                    continue
                proj_url = url_for(
                    'tenant.project_detail',
                    tenant_slug=tenant.slug,
                    slug=project.slug,
                    _external=True,
                )
                proj_lastmod = (
                    project.updated_at.strftime('%Y-%m-%d')
                    if hasattr(project, 'updated_at') and project.updated_at
                    else lastmod
                )
                urls.append({
                    'loc':        proj_url,
                    'lastmod':    proj_lastmod,
                    'changefreq': 'monthly',
                    'priority':   '0.8',
                })

    except Exception as exc:
        logger.warning('sitemap_xml: DB query failed — returning empty sitemap: %s', exc)

    xml_lines = ['<?xml version="1.0" encoding="UTF-8"?>',
                 '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        xml_lines.append('  <url>')
        xml_lines.append(f'    <loc>{u["loc"]}</loc>')
        xml_lines.append(f'    <lastmod>{u["lastmod"]}</lastmod>')
        xml_lines.append(f'    <changefreq>{u["changefreq"]}</changefreq>')
        xml_lines.append(f'    <priority>{u["priority"]}</priority>')
        xml_lines.append('  </url>')
    xml_lines.append('</urlset>')

    resp = make_response('\n'.join(xml_lines), 200)
    resp.headers['Content-Type'] = 'application/xml; charset=utf-8'
    resp.headers['Cache-Control'] = 'public, max-age=21600'  # 6h
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

    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if ip and ',' in ip:
        ip = ip.split(',')[0].strip()

    from app.services.contact_service import process_contact_submission
    result = process_contact_submission(
        tenant_slug='default',
        name=name,
        email=email,
        subject=subject,
        message=message,
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
        .first_or_404()
    )
    # Redirect to canonical tenant-scoped URL
    return redirect(
        url_for('tenant.project_detail',
                tenant_slug=project.tenant_slug or 'default',
                slug=slug),
        code=301
    )


def _redirect_default_billing():
    return redirect(url_for('root'))


def _load_default_billing_profile():
    profile = Profile.query.filter_by(tenant_slug='default').first()
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
        if is_paymongo_enabled() and action == 'save_local':
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
