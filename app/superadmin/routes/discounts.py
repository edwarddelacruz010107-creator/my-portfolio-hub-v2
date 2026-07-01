"""
app/superadmin/routes/discounts.py — Superadmin Discount & Promotion
Manager (v6.6 — Discount & Promotion Manager, Phase 2).

CRUD for DiscountCampaign + analytics. All money math is delegated to
app/services/billing/discount_service.py — this module only handles
form intake, flash messaging, and rendering. It never computes a
discounted price itself.
"""
from __future__ import annotations

import logging
import secrets
import string
from datetime import datetime, timezone

from flask import render_template, redirect, url_for, flash, request

from app import db
from app.forms import DiscountCampaignForm
from app.models.core import DiscountCampaign
from app.repositories.discount_repository import (
    discount_campaign_repository,
    discount_redemption_repository,
)
from app.services.billing import discount_service
from app.utils import (
    BILLING_PLANS, log_activity, normalize_plan_name,
    get_yearly_discount, get_yearly_discount_percent, get_yearly_discount_percent_override,
    set_yearly_discount, clear_yearly_discount_override,
)
from app.superadmin.blueprint import superadmin, superadmin_required

logger = logging.getLogger(__name__)


def _plan_choices():
    return [('', 'All plans')] + [(key, key) for key in BILLING_PLANS.keys()]


def _generate_coupon_code(prefix: str = 'SAVE') -> str:
    """Human-typeable coupon code, e.g. SAVE-7K2QX9. Retries on the rare
    collision instead of trusting randomness alone."""
    alphabet = string.ascii_uppercase + string.digits
    for _ in range(10):
        suffix = ''.join(secrets.choice(alphabet) for _ in range(6))
        code = f'{prefix}-{suffix}'
        if not discount_campaign_repository.code_exists(code):
            return code
    # Extremely unlikely fallback — widen the suffix.
    suffix = ''.join(secrets.choice(alphabet) for _ in range(10))
    return f'{prefix}-{suffix}'


def _apply_form_to_campaign(form: DiscountCampaignForm, campaign: DiscountCampaign) -> None:
    campaign.name = form.name.data.strip()
    raw_code = (form.code.data or '').strip().upper()
    campaign.code = raw_code or None
    campaign.description = (form.description.data or '').strip() or None
    campaign.discount_type = form.discount_type.data
    campaign.value = form.value.data
    campaign.applies_to = form.applies_to.data
    campaign.plan_slug = form.plan_slug.data or None
    campaign.is_global = form.is_global.data
    campaign.is_active = form.is_active.data
    campaign.usage_limit = form.usage_limit.data
    campaign.per_tenant_limit = form.per_tenant_limit.data
    campaign.first_time_only = form.first_time_only.data
    campaign.starts_at = (
        datetime.combine(form.starts_at.data, datetime.min.time()).replace(tzinfo=timezone.utc)
        if form.starts_at.data else None
    )
    campaign.expires_at = (
        datetime.combine(form.expires_at.data, datetime.min.time()).replace(tzinfo=timezone.utc)
        if form.expires_at.data else None
    )


@superadmin.route('/discounts')
@superadmin_required
def discounts():
    campaigns = discount_campaign_repository.list_all_ordered()

    total_redemptions = sum(c.usage_count or 0 for c in campaigns)
    total_revenue_impact = discount_redemption_repository.total_revenue_impact()
    active_count = sum(1 for c in campaigns if c.is_active and not c.is_expired)
    expired_count = sum(1 for c in campaigns if c.is_expired)

    most_used = max(campaigns, key=lambda c: (c.usage_count or 0), default=None)
    if most_used is not None and (most_used.usage_count or 0) == 0:
        most_used = None

    # Precompute days-until-expiry here rather than in the template: some
    # DB engines (SQLite in dev) return naive datetimes for a
    # DateTime(timezone=True) column, and mixing naive/aware datetimes in a
    # Jinja subtraction raises TypeError and blanks the whole page.
    now = datetime.now(timezone.utc)
    for c in campaigns:
        c.days_until_expiry = None
        if c.expires_at:
            expires = c.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            c.days_until_expiry = (expires - now).days

    yearly_discount_percent = get_yearly_discount_percent()

    # Per-plan rows for the "Yearly Billing Discount" box: each plan shows
    # its effective rate (override if set, else the global rate) plus the
    # raw override value so the input can be left blank when there isn't one.
    yearly_discount_plans = [
        {
            'key': key,
            'effective_percent': get_yearly_discount_percent(key),
            'override_percent': get_yearly_discount_percent_override(key),
        }
        for key in BILLING_PLANS.keys()
    ]

    return render_template(
        'superadmin/discounts.html',
        campaigns=campaigns,
        total_redemptions=total_redemptions,
        total_revenue_impact=total_revenue_impact,
        active_count=active_count,
        expired_count=expired_count,
        most_used=most_used,
        yearly_discount_percent=yearly_discount_percent,
        yearly_discount_plans=yearly_discount_plans,
    )


@superadmin.route('/discounts/yearly-discount', methods=['POST'])
@superadmin_required
def discount_yearly_update():
    """
    Update the platform-wide "X% off when paying yearly" rate that drives
    price_yearly for every plan (the green 'Save ~X%' badge on the billing-
    cycle toggle, and the per-plan 'Save ₱X vs monthly' note). This is
    separate from DiscountCampaign — it's the baseline yearly pricing, not
    a time-limited promotion — but lives on this page since it's the other
    "why is this price what it is" lever a superadmin reaches for here.
    """
    percent_off = request.form.get('yearly_discount_percent', type=float)
    if percent_off is None or not (0 <= percent_off <= 90):
        flash('Yearly discount must be a number between 0 and 90.', 'danger')
        return redirect(url_for('superadmin.discounts'))

    set_yearly_discount(percent_off)
    log_activity(
        'yearly_discount_updated', entity_type='PlatformSetting', entity_name='yearly_discount_rate',
        description=f'Yearly billing discount set to {percent_off:g}% off',
    )
    flash(f'Yearly billing discount set to {percent_off:g}% off.', 'success')
    return redirect(url_for('superadmin.discounts'))


@superadmin.route('/discounts/yearly-discount/plan/<plan_key>', methods=['POST'])
@superadmin_required
def discount_yearly_update_plan(plan_key):
    """
    Set (or clear) a plan-specific yearly discount override. Leaving the
    field blank on submit clears the override and that plan goes back to
    following the platform-wide rate above.
    """
    norm = normalize_plan_name(plan_key)
    if norm not in BILLING_PLANS:
        flash(f'Plan "{plan_key}" not found.', 'danger')
        return redirect(url_for('superadmin.discounts'))

    raw = (request.form.get('yearly_discount_percent') or '').strip()

    if raw == '':
        clear_yearly_discount_override(norm)
        log_activity(
            'yearly_discount_plan_cleared', entity_type='PlatformSetting',
            entity_name=f'yearly_discount_plan_{norm}',
            description=f'Cleared {norm}-only yearly discount override — now follows the global rate',
        )
        flash(f'{norm} now follows the global yearly discount rate.', 'success')
        return redirect(url_for('superadmin.discounts'))

    try:
        percent_off = float(raw)
    except ValueError:
        percent_off = None

    if percent_off is None or not (0 <= percent_off <= 90):
        flash('Yearly discount must be a number between 0 and 90.', 'danger')
        return redirect(url_for('superadmin.discounts'))

    set_yearly_discount(percent_off, plan=norm)
    log_activity(
        'yearly_discount_plan_updated', entity_type='PlatformSetting',
        entity_name=f'yearly_discount_plan_{norm}',
        description=f'{norm}-only yearly discount set to {percent_off:g}% off',
    )
    flash(f'{norm} yearly discount set to {percent_off:g}% off.', 'success')
    return redirect(url_for('superadmin.discounts'))


@superadmin.route('/discounts/new', methods=['GET', 'POST'])
@superadmin_required
def discount_new():
    form = DiscountCampaignForm()
    form.plan_slug.choices = _plan_choices()

    if form.validate_on_submit():
        code = (form.code.data or '').strip().upper()
        if code and discount_campaign_repository.code_exists(code):
            flash(f'Coupon code "{code}" is already in use. Choose another.', 'danger')
            return render_template('superadmin/discount_form.html', form=form, campaign=None,
                                    page_title='New Discount Campaign')

        campaign = DiscountCampaign()
        _apply_form_to_campaign(form, campaign)
        db.session.add(campaign)
        db.session.commit()

        log_activity('discount_campaign_created', entity_type='DiscountCampaign',
                      entity_name=campaign.name, description=f'Created campaign "{campaign.name}"')
        flash(f'Campaign "{campaign.name}" created.', 'success')
        return redirect(url_for('superadmin.discounts'))

    return render_template('superadmin/discount_form.html', form=form, campaign=None,
                            page_title='New Discount Campaign')


@superadmin.route('/discounts/<int:campaign_id>/edit', methods=['GET', 'POST'])
@superadmin_required
def discount_edit(campaign_id):
    campaign = discount_campaign_repository.get_or_404(campaign_id)
    form = DiscountCampaignForm(obj=campaign)
    form.plan_slug.choices = _plan_choices()

    if request.method == 'GET':
        form.plan_slug.data = campaign.plan_slug or ''
        form.starts_at.data = campaign.starts_at.date() if campaign.starts_at else None
        form.expires_at.data = campaign.expires_at.date() if campaign.expires_at else None

    if form.validate_on_submit():
        code = (form.code.data or '').strip().upper()
        if code and discount_campaign_repository.code_exists(code, exclude_id=campaign.id):
            flash(f'Coupon code "{code}" is already in use. Choose another.', 'danger')
            return render_template('superadmin/discount_form.html', form=form, campaign=campaign,
                                    page_title=f'Edit — {campaign.name}')

        _apply_form_to_campaign(form, campaign)
        db.session.commit()

        log_activity('discount_campaign_updated', entity_type='DiscountCampaign',
                      entity_name=campaign.name, description=f'Updated campaign "{campaign.name}"')
        flash(f'Campaign "{campaign.name}" updated.', 'success')
        return redirect(url_for('superadmin.discounts'))

    return render_template('superadmin/discount_form.html', form=form, campaign=campaign,
                            page_title=f'Edit — {campaign.name}')


@superadmin.route('/discounts/<int:campaign_id>/toggle', methods=['POST'])
@superadmin_required
def discount_toggle(campaign_id):
    campaign = discount_campaign_repository.get_or_404(campaign_id)
    campaign.is_active = not campaign.is_active
    campaign.updated_at = datetime.now(timezone.utc)
    db.session.commit()

    log_activity('discount_campaign_toggled', entity_type='DiscountCampaign',
                  entity_name=campaign.name,
                  description=f'{"Activated" if campaign.is_active else "Deactivated"} campaign "{campaign.name}"')
    flash(f'Campaign "{campaign.name}" {"activated" if campaign.is_active else "deactivated"}.', 'success')
    return redirect(url_for('superadmin.discounts'))


@superadmin.route('/discounts/<int:campaign_id>/delete', methods=['POST'])
@superadmin_required
def discount_delete(campaign_id):
    campaign = discount_campaign_repository.get_or_404(campaign_id)
    name = campaign.name
    discount_campaign_repository.delete(campaign)
    db.session.commit()

    log_activity('discount_campaign_deleted', entity_type='DiscountCampaign',
                  entity_name=name, description=f'Deleted campaign "{name}"')
    flash(f'Campaign "{name}" deleted.', 'success')
    return redirect(url_for('superadmin.discounts'))


@superadmin.route('/discounts/<int:campaign_id>/duplicate', methods=['POST'])
@superadmin_required
def discount_duplicate(campaign_id):
    """Clone a campaign's settings into a new draft. Starts inactive and
    with a freshly generated code so it never collides with the original
    or accidentally goes live before the superadmin reviews it."""
    source = discount_campaign_repository.get_or_404(campaign_id)

    clone = DiscountCampaign()
    clone.name = f'{source.name} (Copy)'
    clone.description = source.description
    clone.code = _generate_coupon_code() if source.code else None
    clone.discount_type = source.discount_type
    clone.value = source.value
    clone.applies_to = source.applies_to
    clone.plan_slug = source.plan_slug
    clone.is_global = source.is_global
    clone.is_active = False  # review before activating — never clone straight to live
    clone.usage_limit = source.usage_limit
    clone.usage_count = 0
    clone.per_tenant_limit = source.per_tenant_limit
    clone.first_time_only = source.first_time_only
    clone.starts_at = None
    clone.expires_at = None

    db.session.add(clone)
    db.session.commit()

    log_activity('discount_campaign_duplicated', entity_type='DiscountCampaign',
                  entity_name=clone.name, description=f'Duplicated campaign "{source.name}" as "{clone.name}"')
    flash(f'Duplicated as "{clone.name}" — inactive, review before enabling.', 'success')
    return redirect(url_for('superadmin.discount_edit', campaign_id=clone.id))


@superadmin.route('/discounts/<int:campaign_id>/analytics')
@superadmin_required
def discount_analytics(campaign_id):
    campaign = discount_campaign_repository.get_or_404(campaign_id)
    redemptions = discount_redemption_repository.list_for_campaign(campaign.id)
    stats = discount_service.campaign_analytics(campaign)
    return render_template(
        'superadmin/discount_analytics.html',
        campaign=campaign, redemptions=redemptions, stats=stats,
    )


@superadmin.route('/discounts/generate-code')
@superadmin_required
def discount_generate_code():
    """Used by the "Generate" button next to the coupon code field —
    returns a fresh unique code as plain text for the form's JS to fill in."""
    return _generate_coupon_code()
