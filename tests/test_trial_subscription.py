from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import inspect, text

from app import db
from app.models.core import Tenant
from app.middleware.subscription_guard import compute_subscription_state
from app.services.auth.registration_service import register_local_user
from app.services.billing.billing import expire_trial_if_needed
from app.services.studio.dashboard_service import DashboardService
from types import SimpleNamespace


def test_register_local_user_assigns_seven_day_trial(app):
    with app.app_context():
        db.session.remove()
        db.drop_all()
        db.create_all()

        user = register_local_user(
            username='trialuser',
            full_name='Trial User',
            email='trial@example.com',
            password='Password123!',
            ip='127.0.0.1',
        )

        tenant = Tenant.query.get(user.tenant_id)
        assert tenant is not None
        assert tenant.subscription_status == 'trial'
        assert tenant.plan == 'starter'
        assert tenant.subscription_state == 'trial'
        assert tenant.can_publish() is True
        assert tenant.has_feature('analytics') is False
        assert tenant.trial_started_at is not None
        assert tenant.trial_ends_at is not None
        assert tenant.trial_ends_at >= tenant.trial_started_at
        assert (tenant.trial_ends_at - tenant.trial_started_at).days == 7


def test_expire_trial_if_needed_marks_expired(app):
    with app.app_context():
        db.session.remove()
        db.drop_all()
        db.create_all()

        tenant = Tenant(
            slug='trial-expired',
            company_name='Trial Expired',
            email='expired@example.com',
            status='active',
            plan='starter',
            subscription_status='trial',
            plan_name='starter',
            trial_started_at=datetime.now(timezone.utc) - timedelta(days=8),
            trial_ends_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        db.session.add(tenant)
        db.session.commit()

        changed = expire_trial_if_needed(tenant)

        assert changed is True
        assert tenant.subscription_status == 'grace'
        assert tenant.plan == 'starter'
        assert tenant.subscription_state == 'grace'


def test_compute_subscription_state_transitions_trial_to_grace_and_readonly(app):
    with app.app_context():
        db.session.remove()
        db.drop_all()
        db.create_all()

        now = datetime.now(timezone.utc)
        tenant = Tenant(
            slug='trial-state-machine',
            company_name='Trial State Machine',
            email='state@example.com',
            status='active',
            plan='starter',
            subscription_state='trial',
            plan_name='starter',
            trial_started_at=now - timedelta(days=6),
            trial_ends_at=now - timedelta(hours=1),
        )
        db.session.add(tenant)
        db.session.commit()

        state = compute_subscription_state(tenant)
        assert state == 'grace'
        assert tenant.subscription_state == 'grace'
        assert tenant.grace_period_ends_at is not None

        tenant.grace_period_ends_at = now - timedelta(days=1)
        state = compute_subscription_state(tenant)
        assert state == 'readonly'
        assert tenant.subscription_state == 'readonly'


def test_dashboard_service_builds_tenant_context(app):
    with app.app_context():
        db.session.remove()
        db.drop_all()
        db.create_all()

        tenant = Tenant(
            slug='dashboard-context',
            company_name='Dashboard Context',
            email='dashboard@example.com',
            status='active',
            plan='starter',
            subscription_state='trial',
            plan_name='starter',
            trial_started_at=datetime.now(timezone.utc),
            trial_ends_at=datetime.now(timezone.utc) + timedelta(days=5),
        )
        db.session.add(tenant)
        db.session.flush()

        from app.models.tenant_data import Profile

        profile = Profile(tenant=tenant, tenant_id=tenant.id, tenant_slug=tenant.slug, name='Dashboard User', email='dashboard@example.com')
        db.session.add(profile)
        db.session.commit()

        service = DashboardService()
        context = service.build_context(SimpleNamespace(tenant_id=tenant.id))

        assert context['subscription_state'] == 'trial'
        assert context['subscription_badge'] == 'Trial'
        assert context['trial_days_left'] >= 0
        assert context['tenant_context'].tenant is tenant
        assert context['tenant_context'].profile is profile


def test_ensure_tenant_columns_adds_subscription_columns_for_legacy_sqlite(app):
    with app.app_context():
        db.session.remove()
        db.drop_all()

        db.session.execute(text("""
            CREATE TABLE tenants (
                id INTEGER NOT NULL PRIMARY KEY,
                slug VARCHAR(120) NOT NULL,
                company_name VARCHAR(200) NOT NULL,
                email VARCHAR(120) NOT NULL,
                status VARCHAR(50) NOT NULL,
                plan VARCHAR(50) NOT NULL,
                created_at DATETIME,
                updated_at DATETIME
            )
        """))
        db.session.commit()

        from app import _ensure_tenant_columns

        _ensure_tenant_columns()

        columns = {column['name'] for column in inspect(db.engine).get_columns('tenants')}
        assert 'subscription_state' in columns
        assert 'trial_ends_at' in columns
        assert 'grace_period_ends_at' in columns
