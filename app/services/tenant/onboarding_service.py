"""
app/services/tenant/onboarding_service.py

Create a small, safe set of default portfolio content for new tenants.
Designed to be idempotent and run immediately after email verification
so the new user has a usable workspace on first sign-in.

Everything is performed as a single transaction; any failure raises and
the caller should roll back the enclosing DB session.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from app import db

logger = logging.getLogger(__name__)


def create_default_portfolio_for(user, commit: bool = True) -> bool:
    """Create basic tenant-scoped content for a freshly verified user.

    Args:
        user: `app.models.User` instance (must have tenant_id and tenant_slug).
        commit: whether to commit tenant-bound objects after creation.

    Returns:
        True on success; raises on failure.
    """
    if not getattr(user, 'tenant_id', None) or not getattr(user, 'tenant_slug', None):
        raise ValueError('User missing tenant information')

    tenant_id = user.tenant_id
    tenant_slug = user.tenant_slug

    try:
        # Import tenant-bound models (they use __bind_key__ = 'tenant')
        from app.models.tenant_data import (
            Profile, Skill, Project, Service, Testimonial, Certificate,
        )

        # 1) Ensure Profile exists — registration_service usually creates it,
        #    but be defensive.
        profile = Profile.query.filter_by(tenant_id=tenant_id).first()
        if not profile:
            profile = Profile(
                tenant_id=tenant_id,
                tenant_slug=tenant_slug,
                name=(user.username or tenant_slug).title(),
                email=user.email or '',
                title='Creator',
                subtitle='Welcome to your portfolio',
                bio='This is your new portfolio. Edit this to introduce yourself.',
            )
            db.session.add(profile)

        # 2) Add a starter project
        if not Project.query.filter_by(tenant_id=tenant_id).first():
            p = Project(
                tenant_id=tenant_id,
                tenant_slug=tenant_slug,
                title='My First Project',
                slug='my-first-project',
                description='A starter project to get you going. Replace this with your own work.',
                description_short='Starter project',
                status='published',
            )
            db.session.add(p)

        # 3) Add some default skills
        if not Skill.query.filter_by(tenant_id=tenant_id).first():
            skills = ['HTML', 'CSS', 'JavaScript']
            for i, name in enumerate(skills):
                s = Skill(tenant_id=tenant_id, tenant_slug=tenant_slug, name=name, order=i)
                db.session.add(s)

        # 4) Add a default service
        if not Service.query.filter_by(tenant_id=tenant_id).first():
            svc = Service(
                tenant_id=tenant_id,
                tenant_slug=tenant_slug,
                title='Portfolio Setup',
                description='We build modern, responsive portfolio sites.',
            )
            db.session.add(svc)

        # 5) Add a friendly testimonial (placeholder)
        if not Testimonial.query.filter_by(tenant_id=tenant_id).first():
            t = Testimonial(
                tenant_id=tenant_id,
                tenant_slug=tenant_slug,
                author_name=(user.username or 'You'),
                content='This is my portfolio. Replace this testimonial with real feedback.',
            )
            db.session.add(t)

        # 6) Create a sample certificate entry (non-critical)
        if not Certificate.query.filter_by(tenant_id=tenant_id).first():
            c = Certificate(
                tenant_id=tenant_id,
                tenant_slug=tenant_slug,
                title='Getting Started Badge',
                issuer='Portfolio Hub',
                description='Awarded for creating your first portfolio.',
            )
            db.session.add(c)

        if commit:
            db.session.commit()

        logger.info('Onboarding: created default portfolio for tenant=%s user_id=%s', tenant_slug, user.id)
        return True

    except Exception as exc:
        # Rollback to be safe; caller may already be inside a transaction.
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.exception('Onboarding: failed to create default portfolio for tenant=%s: %s', getattr(user, 'tenant_slug', None), exc)
        raise
