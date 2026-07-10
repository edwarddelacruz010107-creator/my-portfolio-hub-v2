"""
app/superadmin/routes/tenants.py — Tenant CRUD (list / create / edit / delete / reset-password / suspend) (Phase 4b, batch 5)

Moved here verbatim from the former monolithic app/superadmin/__init__.py.
No behavior, route, or endpoint-name changes — see blueprint split plan.
"""

import csv
import io
import logging
import re
import secrets
import string
from datetime import datetime, timezone, timedelta
from functools import wraps

from flask import (
    Blueprint, render_template, redirect, url_for,
    flash, request, session, current_app, Response,
)
from urllib.parse import urlparse
from flask_login import current_user, logout_user, login_required
from pathlib import Path
from sqlalchemy import or_, func
from werkzeug.utils import secure_filename

from app.auth import _handle_login
from app.forms import (
    ChangePasswordForm, SuperadminAccountForm,
    TenantForm, SuperadminMessageForm, PaymentInstructionForm, PaymentMethodForm,
)
from app import db, limiter
from app.repositories import (
    profile_repository,
    tenant_repository,
    user_repository,
    project_repository,
    testimonial_repository,
    inquiry_repository,
    activity_log_repository,
    subscription_repository,
    payment_method_repository,
    payment_submission_repository,
    subscription_notification_repository,
    webhook_event_repository,
    global_email_config_repository,
)
from app.services.manual_billing import (
    approve_payment_submission,
    reject_payment_submission,
    save_billing_upload,
    set_default_payment_method,
)
from app.services.tenant_admin import delete_tenant_completely
from app.utils import is_paymongo_enabled, set_paymongo_enabled
from app.models import User
from app.models.portfolio import (Profile, PaymentMethod, PaymentSubmission, Subscription, WebhookEvent,
                                   ActivityLog, Project, Inquiry, Tenant, PaymentInstruction, PAID_PLAN_NAMES,
                                   normalize_plan_name)


from app.utils import log_activity, BILLING_PLANS, YEARLY_DISCOUNT
from app.security import log_security_event
from app.tenant_security import RESERVED_SLUGS, validate_slug, stamp_session_tenant
from app.models.portfolio import TenantCommunicationSettings
from app.system_plan import (
    ADMINISTRATOR_PLAN_NAME,
    ensure_default_tenant_administrator_plan,
    has_administrator_access,
    is_administrator_plan,
)
from app.models.portfolio import _utcnow
from app.services.billing import (
    compute_billing_metrics,
    tenant_billing_summary,
    force_activate_subscription,
    sync_subscription_from_paymongo,
)
from app.services.billing.trial_limits import get_trial_duration_days
from app.services.billing.trial_history import ensure_trial_subscription_record
from app.services.auth.email_policy import EmailPolicyError, assert_email_allowed_for_user, normalize_email


from app.superadmin.blueprint import superadmin, superadmin_required, _normalize_timestamp

logger = logging.getLogger(__name__)

_PUBLIC_PLAN_CHOICES = [
    ('Trial', 'Trial (not subscribed)'),
    ('Basic', 'Basic'),
    ('Pro', 'Pro'),
    ('Enterprise', 'Enterprise'),
]

def _configure_tenant_form_plan_choices(form: TenantForm, include_administrator: bool = False) -> None:
    form.plan.choices = list(_PUBLIC_PLAN_CHOICES)
    if include_administrator:
        form.plan.choices.append((ADMINISTRATOR_PLAN_NAME, 'Administrator — protected system plan'))


def _plan_display_label(raw_plan: str | None) -> str:
    """Return the label shown to superadmin users."""
    norm = normalize_plan_name(raw_plan or 'Trial')
    return {
        'trial': 'Trial',
        'starter': 'Basic',
        'basic': 'Basic',
        'pro': 'Pro',
        'business': 'Business',
        'enterprise': 'Enterprise',
        'administrator': 'Administrator',
    }.get(norm, (raw_plan or 'Trial').strip().title())


def _trial_days_remaining(profile: Profile, tenant_obj: Tenant | None = None) -> int | None:
    """Days left in trial, or None when the tenant is not on trial."""
    if has_administrator_access(profile):
        return None
    state = (getattr(tenant_obj, 'subscription_state', '') or '').strip().lower() if tenant_obj else ''
    trial_ends = getattr(tenant_obj, 'trial_ends_at', None) if tenant_obj else None
    if trial_ends is None:
        trial_ends = getattr(profile, 'free_trial_ends', None)
    if state != 'trial' and not trial_ends:
        return None
    trial_ends = _normalize_timestamp(trial_ends)
    if trial_ends is None:
        return None if state != 'trial' else 0
    return max(0, (trial_ends - datetime.now(timezone.utc)).days)


def _effective_plan_label(profile: Profile, tenant_obj: Tenant | None = None) -> str:
    """Plan label that respects core Tenant.subscription_state."""
    if has_administrator_access(profile):
        return 'Administrator'
    state = (getattr(tenant_obj, 'subscription_state', '') or '').strip().lower() if tenant_obj else ''
    if state == 'trial' or _trial_days_remaining(profile, tenant_obj) is not None:
        return 'Trial'
    try:
        return _plan_display_label(profile.effective_plan())
    except Exception:
        return _plan_display_label(getattr(profile, 'plan', None))


@superadmin.route('/tenants')
@superadmin_required
def tenants():
    q             = request.args.get('q', '').strip()
    status_filter = request.args.get('status', 'all').strip()
    page          = request.args.get('page', 1, type=int)

    query = profile_repository.query.order_by(Profile.updated_at.desc())

    if q:
        query = query.filter(
            or_(
                Profile.name.ilike(f'%{q}%'),
                Profile.tenant_slug.ilike(f'%{q}%'),
                Profile.email.ilike(f'%{q}%'),
            )
        )

    if status_filter == 'active':
        try:
            query = query.filter(Profile.is_available == True)   # noqa: E712
        except Exception:
            pass
    elif status_filter == 'inactive':
        try:
            query = query.filter(Profile.is_available == False)   # noqa: E712
        except Exception:
            pass

    tenant_page = query.paginate(page=page, per_page=15, error_out=False)

    slugs = [t.tenant_slug for t in tenant_page.items]

    core_tenants_by_slug = {}
    if slugs:
        try:
            core_rows = Tenant.query.filter(Tenant.slug.in_(slugs)).all()
            core_tenants_by_slug = {row.slug: row for row in core_rows}
        except Exception:
            core_tenants_by_slug = {}

    project_counts = {}
    if slugs:
        rows = (
            db.session.query(Project.tenant_slug, func.count(Project.id))
            .filter(Project.tenant_slug.in_(slugs))
            .group_by(Project.tenant_slug)
            .all()
        )
        project_counts = {r[0]: r[1] for r in rows}

    tenant_owners = {}
    if slugs:
        owners = (
            user_repository.query
            .filter(User.tenant_slug.in_(slugs), User.is_admin == True)   # noqa: E712
            .all()
        )
        for o in owners:
            if o.tenant_slug not in tenant_owners:
                tenant_owners[o.tenant_slug] = o

    try:
        active_count = profile_repository.query.filter(Profile.is_available == True).count()   # noqa: E712
    except Exception:
        active_count = tenant_page.total

    try:
        trial_count = Tenant.query.filter(Tenant.subscription_state == 'trial').count()
    except Exception:
        trial_count = 0

    days_active_map = {}
    trial_days_remaining_map = {}
    plan_label_map = {}
    now = datetime.now(timezone.utc)
    for tenant in tenant_page.items:
        updated_at = _normalize_timestamp(tenant.updated_at)
        if updated_at:
            delta = now - updated_at
            days_active_map[tenant.tenant_slug] = max(0, delta.days)
        else:
            days_active_map[tenant.tenant_slug] = None
        core_tenant = core_tenants_by_slug.get(tenant.tenant_slug)
        plan_label_map[tenant.tenant_slug] = _effective_plan_label(tenant, core_tenant)
        trial_days_remaining_map[tenant.tenant_slug] = _trial_days_remaining(tenant, core_tenant)

    return render_template(
        'superadmin/tenants.html',
        tenants=tenant_page,
        q=q,
        status_filter=status_filter,
        project_counts=project_counts,
        tenant_owners=tenant_owners,
        active_count=active_count,
        trial_count=trial_count,
        days_active_map=days_active_map,
        plan_label_map=plan_label_map,
        trial_days_remaining_map=trial_days_remaining_map,
    )

@superadmin.route('/tenants/new', methods=['GET', 'POST'])
@superadmin_required
def tenant_new():
    form = TenantForm()
    _configure_tenant_form_plan_choices(form, include_administrator=False)

    if form.validate_on_submit():
        slug = form.tenant_slug.data.strip().lower()

        # v3.7: use canonical validate_slug() from tenant_security (covers RESERVED_SLUGS + format)
        slug_ok, slug_err = validate_slug(slug)
        if not slug_ok:
            flash(slug_err, 'danger')
            return render_template('superadmin/tenant_form.html', form=form, page_title='Create Tenant')

        if profile_repository.get_by_tenant_slug(slug):
            flash(f'Slug "{slug}" is already taken. Choose a different one.', 'danger')
            return render_template('superadmin/tenant_form.html', form=form, page_title='Create Tenant')

        username = form.admin_username.data.strip()
        email    = normalize_email(form.admin_email.data)
        password = request.form.get('admin_password', '').strip()
        password_confirm = request.form.get('admin_password_confirm', '').strip()

        if not password:
            flash('Initial password is required for new tenants.', 'danger')
            return render_template('superadmin/tenant_form.html', form=form, page_title='Create Tenant')

        # FIX PWD: enforce full PasswordPolicy (12+ chars, upper/lower/number/special)
        from app.security import PasswordPolicy
        pwd_ok, pwd_err = PasswordPolicy.validate(password)
        if not pwd_ok:
            flash(f'Password does not meet policy: {pwd_err}', 'danger')
            return render_template('superadmin/tenant_form.html', form=form, page_title='Create Tenant')

        if password != password_confirm:
            flash('Passwords do not match.', 'danger')
            return render_template('superadmin/tenant_form.html', form=form, page_title='Create Tenant')

        if user_repository.query.filter(User.username == username).first():
            flash('Username already exists.', 'danger')
            return render_template('superadmin/tenant_form.html', form=form, page_title='Create Tenant')
        try:
            email = assert_email_allowed_for_user(email, role='tenant_admin', slug=slug)
        except EmailPolicyError as exc:
            flash(str(exc), 'danger')
            return render_template('superadmin/tenant_form.html', form=form, page_title='Create Tenant')

        monthly_rate_val = 0.0
        try:
            monthly_rate_val = float(form.monthly_rate.data or 0)
        except (ValueError, TypeError):
            pass

        free_trial_days_val = 0
        try:
            free_trial_days_val = int(form.free_trial_days.data or 0)
        except (ValueError, TypeError):
            free_trial_days_val = 0

        plan_choice = (form.plan.data or 'Trial').strip()
        if plan_choice == 'Trial' and free_trial_days_val <= 0:
            free_trial_days_val = get_trial_duration_days()

        free_trial_ends = (
            datetime.now(timezone.utc) + timedelta(days=free_trial_days_val)
            if free_trial_days_val > 0 else None
        )

        if is_administrator_plan(plan_choice):
            flash('Administrator is a protected internal system plan and cannot be assigned to normal tenants.', 'danger')
            return render_template('superadmin/tenant_form.html', form=form, page_title='Create Tenant')
        normalized_plan = normalize_plan_name(plan_choice)
        is_system_profile = False
        is_trial_plan = normalized_plan == 'trial'
        tenant = Tenant(
            slug=slug,
            company_name=form.name.data.strip(),
            email=email,
            contact_email=form.contact_email.data.strip().lower() if form.contact_email.data else email,
            status='active' if request.form.get('is_active') == 'on' else 'inactive',
            plan=normalized_plan,
            plan_name=normalized_plan,
            subscription_state='trial' if is_trial_plan else 'pending',
            trial_status='trial' if is_trial_plan else 'pending',
            trial_started_at=datetime.now(timezone.utc) if is_trial_plan else None,
            trial_ends_at=free_trial_ends if is_trial_plan else None,
            grace_period_ends_at=(free_trial_ends + timedelta(days=3)) if is_trial_plan and free_trial_ends else None,
        )
        db.session.add(tenant)
        # ── CRITICAL: flush to core_db so PostgreSQL assigns tenant.id ──────────
        # Profile lives in the TENANT database (cross-DB, no SQLAlchemy FK).
        # SQLAlchemy cannot back-populate tenant_id automatically across binds.
        # Without flush(), tenant.id is None at Profile construction time, which
        # violates the NOT NULL constraint on profile.tenant_id.
        db.session.flush()
        ensure_trial_subscription_record(tenant, commit=False)

        # Guard: if id is still None the sequence/autoincrement is broken
        if tenant.id is None:
            raise RuntimeError(
                "Tenant id is None after flush — check core_db sequence/autoincrement."
            )

        logger.warning(
            "Tenant flushed to core_db: id=%s slug=%s",
            tenant.id,
            tenant.slug,
        )

        # Trial tenants rely on free_trial_ends — no subscription until they pay.
        # free_trial_ends was computed before Tenant/Profile construction so both
        # core_db.tenants and tenant_db.profile stay in sync.

        # ── Profile construction ─────────────────────────────────────────────────
        # Pass tenant_id and tenant_slug EXPLICITLY (post-flush, id is valid).
        # Also pass tenant=tenant so the in-memory cache is set; the setter will
        # overwrite tenant_id with value.id — which is now a real integer.
        profile = Profile(
            tenant=tenant,
            tenant_id=tenant.id,       # explicit — guards against setter ordering
            tenant_slug=tenant.slug,   # use tenant.slug (canonical source of truth)
            name=form.name.data.strip(),
            plan=normalized_plan,
            monthly_rate=monthly_rate_val,
            free_trial_days=free_trial_days_val,
            free_trial_ends=free_trial_ends,
            internal_notes=form.internal_notes.data or '',
            email=email,
        )

        # Belt-and-suspenders: assert tenant_id was not nulled out by the setter
        if profile.tenant_id is None:
            profile.tenant_id = tenant.id
            profile.tenant_slug = tenant.slug
            logger.warning(
                "tenant_id was None after Profile() constructor; re-applied: id=%s",
                tenant.id,
            )

        if (not is_system_profile) and normalized_plan in PAID_PLAN_NAMES:
            subscription = Subscription(
                tenant=tenant,
                plan=normalized_plan,
                status='pending',
                payment_method='admin-provisioned',
            )
            db.session.add(subscription)
            if free_trial_days_val <= 0:
                now = datetime.now(timezone.utc)
                from app.services.billing import plan_duration_days
                subscription.status = 'active'
                subscription.started_at = now
                subscription.expires_at = now + timedelta(days=plan_duration_days(normalized_plan))
                subscription.payment_method = 'admin-provisioned'

        if hasattr(profile, 'is_available'):
            profile.is_available = (request.form.get('is_active') == 'on')

        db.session.add(profile)

        logger.warning(
            "Profile staged for tenant db: tenant_id=%s tenant_slug=%s profile.tenant_id=%s",
            tenant.id,
            tenant.slug,
            profile.tenant_id,
        )

        user = User(
            username=username,
            email=email,
            tenant_slug=slug,
            tenant=tenant,
            is_admin=True,
            is_superadmin=False,
        )
        user.password = password
        db.session.add(user)

        try:
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            logger.exception('Failed to create tenant: %s', exc)
            flash('Database error while creating tenant. Please try again.', 'danger')
            return render_template('superadmin/tenant_form.html', form=form, page_title='Create Tenant')

        log_activity('create', 'tenant', slug, f'Created tenant "{form.name.data}" ({slug})')
        flash(f'Tenant "{form.name.data}" created successfully!', 'success')
        return redirect(url_for('superadmin.tenants'))

    return render_template('superadmin/tenant_form.html', form=form, page_title='Create Tenant')

@superadmin.route('/tenants/<int:tenant_id>/edit', methods=['GET', 'POST'])
@superadmin_required
def tenant_edit(tenant_id):
    profile = db.session.get(Profile, tenant_id)
    if profile is None:
        from flask import abort
        abort(404)
    form    = TenantForm(obj=profile)
    is_system_profile = has_administrator_access(profile)
    _configure_tenant_form_plan_choices(form, include_administrator=is_system_profile)

    owner = user_repository.query.filter_by(
        tenant_slug=profile.tenant_slug, is_admin=True
    ).first()

    if request.method == 'GET' and owner:
        form.admin_username.data = owner.username
        form.admin_email.data    = owner.email
        if is_system_profile:
            form.plan.data = ADMINISTRATOR_PLAN_NAME
            form.tenant_slug.data = profile.tenant_slug
        # Pre-populate contact_email from Tenant model
        if profile.tenant and profile.tenant.contact_email:
            form.contact_email.data = profile.tenant.contact_email

    if form.validate_on_submit():
        old_slug = profile.tenant_slug
        new_slug = old_slug if is_system_profile else form.tenant_slug.data.strip().lower()

        if new_slug != old_slug:
            # v3.7 VULN-02 FIX: enforce RESERVED_SLUGS on rename too
            slug_ok, slug_err = validate_slug(new_slug)
            if not slug_ok:
                flash(slug_err, 'danger')
                return render_template(
                    'superadmin/tenant_form.html', form=form,
                    page_title='Edit Tenant', tenant_id=tenant_id, profile=profile,
                )
            if profile_repository.get_by_tenant_slug(new_slug):
                flash(f'Slug "{new_slug}" is already taken.', 'danger')
                return render_template(
                    'superadmin/tenant_form.html', form=form,
                    page_title='Edit Tenant', tenant_id=tenant_id, profile=profile,
                )

        monthly_rate_val = 0.0
        try:
            monthly_rate_val = float(form.monthly_rate.data or 0)
        except (ValueError, TypeError):
            pass

        profile.name           = form.name.data.strip()
        if not is_system_profile:
            profile.tenant.slug    = new_slug
        profile.monthly_rate   = monthly_rate_val
        profile.internal_notes = form.internal_notes.data or ''

        free_trial_days_val = 0
        try:
            free_trial_days_val = int(form.free_trial_days.data or 0)
        except (ValueError, TypeError):
            free_trial_days_val = 0

        old_trial_days = profile.free_trial_days or 0
        if is_system_profile:
            free_trial_days_val = 0
        profile.free_trial_days = free_trial_days_val
        if is_system_profile:
            profile.free_trial_ends = None
        elif free_trial_days_val != old_trial_days or profile.free_trial_ends is None:
            profile.free_trial_ends = (
                datetime.now(timezone.utc) + timedelta(days=free_trial_days_val)
                if free_trial_days_val > 0 else None
            )

        plan_choice = ADMINISTRATOR_PLAN_NAME if is_system_profile else (form.plan.data or 'Trial').strip()
        if not is_system_profile and is_administrator_plan(plan_choice):
            flash('Administrator is a protected internal system plan and cannot be assigned to normal tenants.', 'danger')
            return render_template('superadmin/tenant_form.html', form=form, page_title='Edit Tenant', tenant_id=tenant_id, profile=profile)
        normalized_plan = ADMINISTRATOR_PLAN_NAME if is_system_profile else normalize_plan_name(plan_choice)
        profile.plan = normalized_plan
        if profile.tenant:
            profile.tenant.plan = normalized_plan
            profile.tenant.plan_name = normalized_plan if not is_system_profile else 'administrator'
            if is_system_profile:
                profile.tenant.status = 'active'
                profile.tenant.subscription_state = 'active'
                profile.tenant.trial_status = 'active'
                profile.tenant.trial_ends_at = None
                profile.tenant.grace_period_ends_at = None
                profile.tenant.subscription_expires_at = None
            elif normalized_plan == 'trial':
                profile.tenant.subscription_state = 'trial'
                profile.tenant.trial_status = 'trial'
                if profile.tenant.trial_started_at is None:
                    profile.tenant.trial_started_at = datetime.now(timezone.utc)
                profile.tenant.trial_ends_at = profile.free_trial_ends
                profile.tenant.grace_period_ends_at = (
                    profile.free_trial_ends + timedelta(days=3)
                    if profile.free_trial_ends else None
                )
                profile.tenant.subscription_expires_at = None
            elif normalized_plan in PAID_PLAN_NAMES:
                # A superadmin-provisioned paid plan still requires payment/admin
                # activation unless the existing billing flow marks it active.
                profile.tenant.subscription_state = 'pending'
                profile.tenant.trial_status = 'pending'
                profile.tenant.trial_ends_at = None
                profile.tenant.grace_period_ends_at = None
            # Update contact_email if provided
            new_contact_email = request.form.get('contact_email', '').strip().lower()
            if new_contact_email:
                import re as _re
                if _re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]{2,}$', new_contact_email):
                    profile.tenant.contact_email = new_contact_email
                else:
                    flash('Invalid contact email format.', 'warning')

        if (not is_system_profile) and normalized_plan in PAID_PLAN_NAMES:
            subscription = profile.current_subscription()
            if not subscription:
                subscription = Subscription(
                    tenant=profile.tenant,
                    plan=normalized_plan,
                    status='pending',
                    payment_method='admin-provisioned',
                )
                db.session.add(subscription)
            else:
                subscription.plan = normalized_plan
        elif is_system_profile:
            ensure_default_tenant_administrator_plan(commit=False)
        elif normalized_plan == 'trial' and profile.current_subscription():
            # Switching back to trial — remove pending admin-provisioned sub
            sub = profile.current_subscription()
            if sub and sub.status in ('pending', 'expired', 'cancelled'):
                db.session.delete(sub)

        is_active_raw = request.form.get('is_active')
        if hasattr(profile, 'is_available'):
            profile.is_available = True if is_system_profile else (is_active_raw == 'on')

        new_username = form.admin_username.data.strip()
        new_email    = normalize_email(form.admin_email.data)

        if owner:
            if new_username != owner.username:
                if user_repository.query.filter(User.username == new_username, User.id != owner.id).first():
                    flash('Username already taken.', 'danger')
                    return render_template(
                        'superadmin/tenant_form.html', form=form,
                        page_title='Edit Tenant', tenant_id=tenant_id, profile=profile,
                    )
                owner.username = new_username

            if new_email != owner.email:
                try:
                    new_email = assert_email_allowed_for_user(
                        new_email,
                        user=owner,
                        tenant=profile.tenant,
                        role='tenant_admin',
                        slug=new_slug,
                    )
                except EmailPolicyError as exc:
                    flash(str(exc), 'danger')
                    return render_template(
                        'superadmin/tenant_form.html', form=form,
                        page_title='Edit Tenant', tenant_id=tenant_id, profile=profile,
                    )
                owner.email = new_email

            if new_slug != old_slug and not is_system_profile:
                owner.tenant_slug = new_slug

        # FIX: CASCADE slug rename to ALL tenant-scoped tables.
        # Without this, a slug rename orphans all projects/skills/etc.
        if new_slug != old_slug and not is_system_profile:
            profile.tenant.slug = new_slug
            from app.models.portfolio import Skill, Testimonial, ActivityLog, Inquiry
            for model in (Project, Skill, Testimonial, ActivityLog, Inquiry):
                try:
                    db.session.query(model).filter_by(tenant_slug=old_slug).update(
                        {'tenant_slug': new_slug}, synchronize_session='fetch'
                    )
                except Exception as exc:
                    logger.warning('Slug cascade update failed for %s: %s', model.__name__, exc)
            # Also update any other admin users for this tenant
            db.session.query(User).filter_by(tenant_slug=old_slug).update(
                {'tenant_slug': new_slug}, synchronize_session='fetch'
            )

        try:
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            logger.exception('Failed to update tenant: %s', exc)
            flash('Database error. Please try again.', 'danger')
            return render_template(
                'superadmin/tenant_form.html', form=form,
                page_title='Edit Tenant', tenant_id=tenant_id, profile=profile,
            )

        log_activity('update', 'tenant', new_slug, f'Updated tenant "{profile.name}"')
        flash(f'Tenant "{profile.name}" updated successfully!', 'success')
        return redirect(url_for('superadmin.tenants'))

    days_active = None
    updated_at = _normalize_timestamp(profile.updated_at)
    if updated_at:
        days_active = max(0, (datetime.now(timezone.utc) - updated_at).days)

    return render_template(
        'superadmin/tenant_form.html', form=form,
        page_title='Edit Tenant', tenant_id=tenant_id, profile=profile,
        days_active=days_active,
    )

@superadmin.route('/tenants/<int:tenant_id>/delete', methods=['POST'])
@superadmin_required
@limiter.limit('10 per minute')
def tenant_delete(tenant_id):
    profile = db.session.get(Profile, tenant_id)
    if profile is None:
        from flask import abort
        abort(404)

    if profile.tenant_slug in ('default', 'administrator') or has_administrator_access(profile):
        flash('The default tenant cannot be deleted.', 'danger')
        return redirect(url_for('superadmin.tenants'))

    tenant = profile.tenant
    if tenant is None:
        flash('Tenant record not found.', 'danger')
        return redirect(url_for('superadmin.tenants'))

    slug = profile.tenant_slug
    name = profile.name or slug

    try:
        delete_tenant_completely(tenant)
    except ValueError as exc:
        flash(str(exc), 'danger')
        return redirect(url_for('superadmin.tenants'))
    except Exception as exc:
        db.session.rollback()
        logger.exception('Failed to delete tenant %s: %s', slug, exc)
        flash('Error deleting tenant. Please try again.', 'danger')
        return redirect(url_for('superadmin.tenants'))

    log_activity('delete', 'tenant', slug, f'Deleted tenant "{name}" ({slug})')
    flash(f'Tenant "{name}" has been deleted.', 'success')
    return redirect(url_for('superadmin.tenants'))

@superadmin.route('/tenants/<int:tenant_id>/reset-password', methods=['POST'])
@superadmin_required
def tenant_reset_password(tenant_id):
    profile = db.session.get(Profile, tenant_id)
    if profile is None:
        from flask import abort
        abort(404)

    owner = user_repository.query.filter_by(
        tenant_slug=profile.tenant_slug,
        is_admin=True,
    ).first()
    if owner is None:
        flash('No tenant admin user found for this tenant.', 'danger')
        return redirect(url_for('superadmin.tenants'))

    # FIX PWD: include special chars so generated password can pass PasswordPolicy
    # if the tenant ever logs in and attempts to change it through a validated flow.
    # Also mark require_password_reset so the tenant must change it on first login.
    _temp_charset = string.ascii_letters + string.digits + '!@#$%^&*'
    new_password = ''.join(secrets.choice(_temp_charset) for _ in range(16))
    owner.password = new_password
    if hasattr(owner, 'require_password_reset'):
        owner.require_password_reset = True

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.exception('Failed to reset tenant admin password: %s', exc)
        flash('Unable to reset tenant admin password. Please try again.', 'danger')
        return redirect(url_for('superadmin.tenants'))

    log_activity(
        'security', 'user', owner.username,
        f'Reset password for tenant admin {owner.username} of {profile.tenant_slug}'
    )
    flash(
        f'Tenant admin password for "{owner.username}" has been reset. '
        f'New temporary password: {new_password}',
        'success'
    )
    return redirect(url_for('superadmin.tenants'))

@superadmin.route('/tenants/<int:tenant_id>/toggle-suspend', methods=['POST'])
@superadmin_required
def tenant_toggle_suspend(tenant_id):
    profile = db.session.get(Profile, tenant_id)
    if profile is None:
        from flask import abort
        abort(404)

    if has_administrator_access(profile):
        ensure_default_tenant_administrator_plan(commit=True)
        flash('The protected system portfolio cannot be suspended or downgraded.', 'warning')
        return redirect(url_for('superadmin.tenants'))

    if not hasattr(profile, 'is_available'):
        flash('Tenant suspension is not supported by this platform version.', 'warning')
        return redirect(url_for('superadmin.tenants'))

    profile.is_available = not profile.is_available
    status = 'unsuspended' if profile.is_available else 'suspended'

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.exception('Failed to toggle tenant suspension: %s', exc)
        flash('Unable to update tenant status. Please try again.', 'danger')
        return redirect(url_for('superadmin.tenants'))

    log_activity(
        'update', 'tenant', profile.tenant_slug,
        f'{status.title()} tenant {profile.tenant_slug}'
    )
    flash(f'Tenant "{profile.name or profile.tenant_slug}" has been {status}.', 'success')
    return redirect(url_for('superadmin.tenants'))
