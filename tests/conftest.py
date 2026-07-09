"""Pytest fixtures: bootstrap the in-memory testing DB and seed minimal records.

This autouse, session-scoped fixture ensures tests that expect DB tables
to exist will find a clean in-memory schema and a few essential rows.
"""
from __future__ import annotations

import pytest

from app import create_app, db


@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    """Create all tables in the testing DB and seed minimal fixtures.

    Yields after setup; drops all tables at teardown.
    """
    app = create_app("testing")

    with app.app_context():
        # Create schema for the default bind and any named binds
        db.create_all()
        binds = app.config.get('SQLALCHEMY_BINDS') or {}
        for bind_name in binds.keys():
            try:
                db.create_all(bind=bind_name)
            except Exception:
                # Some binds may be in-memory or not available in tests — ignore failures
                pass

        # Minimal seed: tenants, user, global email config. Import lazily
        try:
            from app.models.core import Tenant, User, GlobalEmailConfig

            tenant = Tenant.query.filter_by(slug="default").first()
            if not tenant:
                tenant = Tenant(slug="default", company_name="Default Tenant", email="default@example.com")
                db.session.add(tenant)
                db.session.commit()

            user = User.query.filter_by(is_superadmin=False).first()
            if not user:
                user = User(
                    username="testuser",
                    email="test@example.com",
                    tenant_slug=tenant.slug,
                    tenant_id=tenant.id,
                    is_admin=True,
                    is_superadmin=False,
                )
                user.password = "Password123!"
                db.session.add(user)
                db.session.commit()

            # Ensure singleton email config exists
            GlobalEmailConfig.get()

            # Tenant-scoped fixtures: profile (tenant DB), subscription, and tenant communication
            try:
                from app.models.tenant_data import Profile
                from app.models.core import Subscription, TenantCommunicationSettings

                # Create a minimal Profile in the tenant DB
                profile = None
                try:
                    profile = Profile.query.filter_by(tenant_id=tenant.id).first()
                except Exception:
                    # tenant bind may be unavailable in some test environments
                    profile = None
                if not profile:
                    p = Profile(
                        tenant_id=tenant.id,
                        tenant_slug=tenant.slug,
                        name=f"{tenant.company_name} Profile",
                        email=tenant.email or 'profile@example.com',
                    )
                    db.session.add(p)
                    try:
                        db.session.commit()
                    except Exception:
                        db.session.rollback()

                # Create a simple active Subscription in core DB
                sub = Subscription.query.filter_by(tenant_id=tenant.id).first()
                if not sub:
                    sub = Subscription(
                        tenant_id=tenant.id,
                        plan='Basic',
                        status='active',
                        amount_paid=0.0,
                    )
                    db.session.add(sub)
                    try:
                        db.session.commit()
                    except Exception:
                        db.session.rollback()

                # Ensure tenant communication settings exist
                try:
                    TenantCommunicationSettings.get_or_create(tenant.id, tenant.slug)
                    try:
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
                except Exception:
                    db.session.rollback()

            except Exception:
                # If tenant-bind models or subscriptions fail, continue — some tests stub these.
                try:
                    db.session.rollback()
                except Exception:
                    pass

        except Exception:
            # If imports fail, don't crash setup — tests may handle missing models.
            db.session.rollback()

    yield

    # Teardown: drop all tables to clean up memory
    with app.app_context():
        try:
            db.session.remove()
            db.drop_all()
        except Exception:
            pass


@pytest.fixture(scope='session')
def app():
    """Provide the Flask app instance used by pytest-flask and seed DB."""
    app = create_app('testing')
    with app.app_context():
        try:
            db.create_all()
            binds = app.config.get('SQLALCHEMY_BINDS') or {}
            for bind_name in binds.keys():
                try:
                    db.create_all(bind=bind_name)
                except Exception:
                    pass

            # Seed minimal tenant and admin users for the app instance
            from app.models.core import Tenant, User, GlobalEmailConfig
            tenant = Tenant.query.filter_by(slug='default').first()
            if not tenant:
                tenant = Tenant(slug='default', company_name='Default Tenant', email='default@example.com')
                db.session.add(tenant)
                db.session.commit()

            user = User.query.filter_by(is_superadmin=False).first()
            if not user:
                user = User(
                    username='testuser',
                    email='test@example.com',
                    tenant_slug=tenant.slug,
                    tenant_id=tenant.id,
                    is_admin=True,
                    is_superadmin=False,
                )
                user.password = 'Password123!'
                db.session.add(user)
                db.session.commit()

            GlobalEmailConfig.get()

            # Tenant-scoped fixtures (best-effort)
            try:
                from app.models.tenant_data import Profile
                from app.models.core import Subscription, TenantCommunicationSettings
                if not Profile.query.filter_by(tenant_id=tenant.id).first():
                    p = Profile(tenant_id=tenant.id, tenant_slug=tenant.slug, name=f"{tenant.company_name} Profile", email=tenant.email)
                    db.session.add(p)
                    db.session.commit()

                if not Subscription.query.filter_by(tenant_id=tenant.id).first():
                    s = Subscription(tenant_id=tenant.id, plan='Basic', status='active', amount_paid=0.0)
                    db.session.add(s)
                    db.session.commit()

                try:
                    TenantCommunicationSettings.get_or_create(tenant.id, tenant.slug)
                    db.session.commit()
                except Exception:
                    db.session.rollback()
            except Exception:
                db.session.rollback()
        except Exception:
            app.logger.exception('App fixture DB create/seed failed')
    yield app
    with app.app_context():
        try:
            db.session.remove()
            db.drop_all()
        except Exception:
            pass
