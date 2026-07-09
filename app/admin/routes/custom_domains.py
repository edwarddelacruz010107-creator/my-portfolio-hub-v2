"""Tenant custom-domain settings routes.

Targeted additive feature: lets eligible tenants add one or more verified
custom domains without changing existing slug-based public routes.
"""
from __future__ import annotations

import logging

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user

from app import db
from app.admin.blueprint import admin, admin_required, _active_tenant_plan_features, _active_tenant_slug, _load_tenant_profile
from app.models.core import Tenant, TenantCustomDomain
from app.services.custom_domain_service import (
    create_or_replace_domain,
    dns_instructions,
    normalize_custom_domain,
    verify_domain_dns,
)
from app.utils import log_activity

logger = logging.getLogger(__name__)


def _current_tenant() -> Tenant | None:
    slug = _active_tenant_slug()
    return Tenant.query.filter_by(slug=slug).first()


@admin.route('/settings/custom-domain', methods=['GET', 'POST'])
@admin_required
def custom_domain_settings():
    tenant = _current_tenant()
    profile = _load_tenant_profile()
    plan_features = _active_tenant_plan_features()
    can_use_custom_domain = bool(plan_features.get('custom_domain'))

    if tenant is None:
        flash('Tenant was not found. Please sign in again.', 'danger')
        return redirect(url_for('admin.dashboard'))

    if request.method == 'POST':
        action = (request.form.get('action') or '').strip().lower()

        if not can_use_custom_domain and action in {'add', 'verify', 'set_primary'}:
            flash('Custom domains are available on Pro, Enterprise, and Administrator plans.', 'warning')
            return redirect(url_for('admin.custom_domain_settings'))

        if action == 'add':
            raw_domain = request.form.get('domain', '')
            record, error = create_or_replace_domain(tenant, raw_domain)
            if error or record is None:
                flash(error or 'Unable to save custom domain.', 'danger')
                return redirect(url_for('admin.custom_domain_settings'))

            db.session.commit()
            log_activity('update', 'custom_domain', record.normalized_domain, 'Custom domain added for verification')
            flash('Custom domain saved. Add the DNS records below, then click Verify DNS.', 'success')
            return redirect(url_for('admin.custom_domain_settings'))

        if action == 'verify':
            record_id = request.form.get('domain_id', type=int)
            record = TenantCustomDomain.query.filter_by(id=record_id, tenant_id=tenant.id).first()
            if record is None:
                flash('Custom domain record was not found.', 'danger')
                return redirect(url_for('admin.custom_domain_settings'))

            result = verify_domain_dns(record)
            db.session.add(record)
            db.session.commit()
            if result.verified:
                log_activity('update', 'custom_domain', record.normalized_domain, 'Custom domain DNS verified')
                flash(result.message, 'success')
            else:
                flash(result.message, 'warning')
            return redirect(url_for('admin.custom_domain_settings'))

        if action == 'set_primary':
            record_id = request.form.get('domain_id', type=int)
            record = TenantCustomDomain.query.filter_by(id=record_id, tenant_id=tenant.id).first()
            if record is None:
                flash('Custom domain record was not found.', 'danger')
                return redirect(url_for('admin.custom_domain_settings'))
            if record.status != 'verified':
                flash('Only verified domains can be set as primary.', 'warning')
                return redirect(url_for('admin.custom_domain_settings'))

            TenantCustomDomain.query.filter_by(tenant_id=tenant.id).update({'is_primary': False})
            record.is_primary = True
            db.session.add(record)
            db.session.commit()
            log_activity('update', 'custom_domain', record.normalized_domain, 'Custom domain set as primary')
            flash('Primary custom domain updated.', 'success')
            return redirect(url_for('admin.custom_domain_settings'))

        if action == 'remove':
            record_id = request.form.get('domain_id', type=int)
            record = TenantCustomDomain.query.filter_by(id=record_id, tenant_id=tenant.id).first()
            if record is None:
                flash('Custom domain record was not found.', 'danger')
                return redirect(url_for('admin.custom_domain_settings'))
            domain = record.normalized_domain
            db.session.delete(record)
            db.session.commit()
            log_activity('delete', 'custom_domain', domain, 'Custom domain removed')
            flash('Custom domain removed. DNS records can now be deleted from your domain provider.', 'success')
            return redirect(url_for('admin.custom_domain_settings'))

        flash('Unknown custom-domain action.', 'warning')
        return redirect(url_for('admin.custom_domain_settings'))

    domains = (
        TenantCustomDomain.query
        .filter_by(tenant_id=tenant.id)
        .order_by(TenantCustomDomain.is_primary.desc(), TenantCustomDomain.created_at.desc())
        .all()
    )
    domain_rows = [
        {
            'record': record,
            'dns': dns_instructions(record),
        }
        for record in domains
    ]

    return render_template(
        'admin/custom_domain.html',
        tenant=tenant,
        profile=profile,
        domain_rows=domain_rows,
        can_use_custom_domain=can_use_custom_domain,
    )
