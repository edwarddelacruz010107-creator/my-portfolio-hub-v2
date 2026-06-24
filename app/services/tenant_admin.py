"""Superadmin tenant lifecycle helpers."""

from __future__ import annotations

import logging

from app import db
from app.models.portfolio import (
    ActivityLog,
    Inquiry,
    Profile,
    Tenant,
    Testimonial,
)
from app.models import User

logger = logging.getLogger(__name__)


def delete_tenant_completely(tenant: Tenant) -> None:
    """
    Permanently remove a tenant and all associated data.
    Cannot delete the 'default' tenant.
    """
    if tenant.slug == 'default':
        raise ValueError('The default tenant cannot be deleted.')

    slug = tenant.slug

    Testimonial.query.filter_by(tenant_slug=slug).delete(synchronize_session=False)
    Inquiry.query.filter_by(tenant_slug=slug).delete(synchronize_session=False)
    ActivityLog.query.filter_by(tenant_slug=slug).delete(synchronize_session=False)

    User.query.filter_by(tenant_id=tenant.id).delete(synchronize_session=False)

    profile = Profile.query.filter_by(tenant_id=tenant.id).first()
    if profile:
        db.session.delete(profile)

    db.session.delete(tenant)
    db.session.commit()
