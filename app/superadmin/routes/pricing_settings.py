"""
app/superadmin/routes/pricing_settings.py — Superadmin Pricing CMS
(Landing Experience Engine, Pricing module).

CRUD for marketing-only pricing-page overrides. Price amounts stay
exclusively in app.utils.BILLING_PLANS — see
app/public/services/pricing_service.py for why this is deliberate.
Draft/publish pattern mirrors app/superadmin/routes/landing_settings.py.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from flask import render_template, redirect, url_for, flash, request

from app import db, limiter
from app.forms import PricingSettingsForm
from app.models.core import PlatformSetting
from app.public.services.pricing_service import (
    get_pricing_content,
    plan_config_key,
    draft_key,
    SECTION_KEYS,
    YEARLY_TOGGLE_KEY,
    DEFAULT_PLAN_CONFIG,
)
from app.superadmin.blueprint import superadmin, superadmin_required
from app.utils import BILLING_PLANS, log_activity, is_paymongo_enabled

logger = logging.getLogger(__name__)

_PLAN_SLUGS = {name: name.lower() for name in BILLING_PLANS.keys()}  # {'Basic': 'basic', ...}

_MAX_FEATURES = 10
_MAX_FEATURE_LEN = 120

# Only allow same-site relative paths or http(s) absolute URLs for CTA
# links. Blocks javascript:, data:, vbscript:, mailto-spoof-style tricks,
# and open-redirect-flavored schemes — this field is rendered as an
# <a href> on the public pricing page.
_ALLOWED_CTA_SCHEMES = {"http", "https", ""}


def _is_safe_cta_url(url: str) -> bool:
    url = (url or "").strip()
    if not url:
        return True  # empty is valid — falls back to the default signup link
    if url.startswith("/") and not url.startswith("//"):
        return True  # relative, same-site path
    parsed = urlparse(url)
    return parsed.scheme.lower() in _ALLOWED_CTA_SCHEMES and bool(parsed.netloc)


def _parse_features(raw_text: str) -> list[str]:
    lines = [ln.strip() for ln in (raw_text or "").splitlines()]
    lines = [ln[:_MAX_FEATURE_LEN] for ln in lines if ln]
    return lines[:_MAX_FEATURES]


def _load_form_data(draft_first: bool = True, fallback_to_published: bool = True) -> dict:
    values: dict = {}
    for field, published_key in SECTION_KEYS.items():
        if draft_first:
            v = PlatformSetting.get_string(draft_key(published_key), default="") or ""
            if not v and fallback_to_published:
                v = PlatformSetting.get_string(published_key, default="") or ""
        else:
            v = PlatformSetting.get_string(published_key, default="") or ""
        values[field] = v

    if draft_first:
        toggle = PlatformSetting.get_bool(draft_key(YEARLY_TOGGLE_KEY))
        if toggle is None and fallback_to_published:
            toggle = PlatformSetting.get_bool(YEARLY_TOGGLE_KEY, default=False)
        values["yearly_toggle_enabled"] = bool(toggle) if toggle is not None else False
    else:
        values["yearly_toggle_enabled"] = bool(PlatformSetting.get_bool(YEARLY_TOGGLE_KEY, default=False))

    for plan_name, slug in _PLAN_SLUGS.items():
        if draft_first:
            cfg = PlatformSetting.get_json(plan_config_key(plan_name, draft=True))
            if cfg is None and fallback_to_published:
                cfg = PlatformSetting.get_json(plan_config_key(plan_name, draft=False))
        else:
            cfg = PlatformSetting.get_json(plan_config_key(plan_name, draft=False))
        cfg = {**DEFAULT_PLAN_CONFIG, **(cfg or {})}

        values[f"{slug}_badge_text"] = cfg["badge_text"]
        values[f"{slug}_cta_text"] = cfg["cta_text"]
        values[f"{slug}_cta_url"] = cfg["cta_url"]
        values[f"{slug}_description_override"] = cfg["description_override"]
        values[f"{slug}_features_override"] = "\n".join(cfg["features_override"])
        values[f"{slug}_highlighted"] = cfg["highlighted"]

    return values


def _save_form_data(form: PricingSettingsForm, publish: bool = False) -> None:
    for field, published_key in SECTION_KEYS.items():
        value = (getattr(form, field).data or "").strip()
        PlatformSetting.set_string(draft_key(published_key), value)
        if publish:
            PlatformSetting.set_string(published_key, value)

    toggle_value = bool(form.yearly_toggle_enabled.data)
    PlatformSetting.set_bool(draft_key(YEARLY_TOGGLE_KEY), toggle_value)
    if publish:
        PlatformSetting.set_bool(YEARLY_TOGGLE_KEY, toggle_value)

    # Enforce "at most one highlighted plan" server-side — never trust the
    # client to have kept the (JS-only) radio-style exclusivity honest.
    highlighted_plan = None
    for plan_name, slug in _PLAN_SLUGS.items():
        if getattr(form, f"{slug}_highlighted").data:
            highlighted_plan = plan_name
            break  # first checked wins; ties broken by BILLING_PLANS order

    for plan_name, slug in _PLAN_SLUGS.items():
        cta_url = (getattr(form, f"{slug}_cta_url").data or "").strip()
        if not _is_safe_cta_url(cta_url):
            cta_url = ""  # silently drop unsafe schemes rather than 500

        config = {
            "badge_text": (getattr(form, f"{slug}_badge_text").data or "").strip(),
            "cta_text": (getattr(form, f"{slug}_cta_text").data or "").strip(),
            "cta_url": cta_url,
            "description_override": (getattr(form, f"{slug}_description_override").data or "").strip(),
            "features_override": _parse_features(getattr(form, f"{slug}_features_override").data),
            "highlighted": plan_name == highlighted_plan,
        }
        PlatformSetting.set_json(plan_config_key(plan_name, draft=True), config)
        if publish:
            PlatformSetting.set_json(plan_config_key(plan_name, draft=False), config)


@superadmin.route('/settings/pricing/preview')
@superadmin_required
def pricing_settings_preview():
    try:
        pricing_content = get_pricing_content(draft_first=True, fallback_to_published=True)
        return render_template(
            'public/pricing.html',
            section=pricing_content["section"],
            plans=pricing_content["plans"],
            yearly_toggle_enabled=pricing_content["yearly_toggle_enabled"],
            paymongo_enabled=is_paymongo_enabled(),
            preview_mode=True,
        )
    except Exception:
        logger.exception('Pricing preview failed')
        flash('Unable to load the pricing preview right now.', 'danger')
        return redirect(url_for('superadmin.pricing_settings'))


@superadmin.route('/settings/pricing', methods=['GET', 'POST'])
@superadmin_required
@limiter.limit('30 per minute', methods=['POST'])
def pricing_settings():
    form = PricingSettingsForm()

    if request.method == 'GET':
        values = _load_form_data(draft_first=True, fallback_to_published=True)
        for field, value in values.items():
            setattr(getattr(form, field), 'data', value)

    if form.validate_on_submit():
        action = (request.form.get('action') or 'save').strip().lower()
        publish = action == 'publish'
        try:
            _save_form_data(form, publish=publish)
            db.session.commit()
            if publish:
                log_activity('publish', 'pricing_page', 'pricing_content', 'Published pricing page content')
                flash('Pricing page content published successfully.', 'success')
            else:
                log_activity('update', 'pricing_page', 'pricing_content', 'Saved pricing draft')
                flash('Pricing draft saved successfully.', 'success')
            return redirect(url_for('superadmin.pricing_settings'))
        except ValueError as exc:
            # e.g. PlatformSetting.set_json size-cap violation
            db.session.rollback()
            logger.warning('Pricing settings rejected: %s', exc)
            flash('One of the fields is too long to save. Please shorten it and try again.', 'danger')
        except Exception:
            logger.exception('Failed to save pricing settings')
            db.session.rollback()
            flash('Failed to update pricing content. Please try again.', 'danger')

    return render_template(
        'superadmin/pricing_settings.html',
        form=form,
        billing_plans=BILLING_PLANS,
        page_title='Pricing Page Content',
    )
