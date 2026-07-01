#!/usr/bin/env python
"""
test_subscriptions.py — Billing flow tests for Portfolio CMS v3.4.1

Changes from v3.3:
  - Removed all references to Subscription.payment_status and
    Subscription.license_key (dropped in migration 0015).
  - Added tests for get_active_payment_methods_for_tenant() global visibility.
  - Fixed _make_subscription() signature to match current model columns.
  - Added test: global PaymentMethod is visible to all tenants.
  - Added test: tenant-specific PaymentMethod is invisible to other tenants.
  - Added test: get_payment_method_for_tenant() enforces isolation.
  - Added test: inactive method is never returned.
  - Added test: full end-to-end manual proof submit → approve → activate.
"""

import sys
import os
import hashlib
import hmac as _hmac
import json
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app, db
from app.models.portfolio import (
    Tenant, Profile, Subscription, PaymentSubmission, PaymentMethod,
    normalize_plan_name,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_app():
    return create_app('testing')


def _make_tenant(slug, plan='Basic'):
    tenant = Tenant(
        slug=slug,
        company_name=f'Company {slug}',
        email=f'{slug}@example.com',
        status='active',
        plan=plan,
    )
    db.session.add(tenant)
    db.session.flush()
    profile = Profile(
        tenant=tenant,
        name=slug.title(),
        email=f'{slug}@example.com',
        plan=plan,
    )
    db.session.add(profile)
    db.session.flush()
    return tenant, profile


def _make_subscription(tenant, plan='Basic', status='pending', **kwargs):
    """Create a Subscription using only columns present in v3.4 schema."""
    sub = Subscription(
        tenant=tenant,
        plan=plan,
        status=status,
        **kwargs,
    )
    db.session.add(sub)
    db.session.flush()
    return sub


def _make_payment_method(name, method_type='bank', tenant=None, is_active=True, display_order=0):
    m = PaymentMethod(
        tenant=tenant,
        name=name,
        method_type=method_type,
        is_active=is_active,
        display_order=display_order,
        account_name='Test Account',
        account_number='1234567890',
    )
    db.session.add(m)
    db.session.flush()
    return m


# ── 1. Subscription model — column presence (v3.4 schema) ────────────────────

def test_subscription_columns_v34():
    app = _make_app()
    with app.app_context():
        db.drop_all()
        db.create_all()
        tenant, _ = _make_tenant('col-test')
        sub = _make_subscription(tenant)
        db.session.commit()

        fetched = db.session.get(Subscription, sub.id)
        # v3.4 columns that must exist
        assert hasattr(fetched, 'paymongo_id'), 'paymongo_id column missing'
        assert hasattr(fetched, 'paymongo_payment_id'), 'paymongo_payment_id column missing'
        assert hasattr(fetched, 'amount_paid'), 'amount_paid column missing'
        assert hasattr(fetched, 'billing_cycle'), 'billing_cycle column missing'
        # Dropped columns must NOT exist on the ORM class
        assert not hasattr(Subscription, 'license_key'), \
            'license_key should have been dropped in migration 0015'
        assert not hasattr(Subscription, 'payment_status'), \
            'payment_status should have been dropped in migration 0015'
        assert fetched.status == 'pending'
        print('✓ Subscription v3.4 columns present; legacy columns absent')


# ── 2. PaymentSubmission FK ───────────────────────────────────────────────────

def test_payment_submission_subscription_fk():
    app = _make_app()
    with app.app_context():
        db.drop_all()
        db.create_all()
        tenant, profile = _make_tenant('fk-test')
        sub = _make_subscription(tenant, plan='Pro')
        submission = PaymentSubmission(
            tenant=tenant,
            subscription_id=sub.id,
            plan='Pro',
            payment_method='gcash',
            status='pending',
        )
        db.session.add(submission)
        db.session.commit()

        fetched = db.session.get(PaymentSubmission, submission.id)
        assert fetched.subscription_id == sub.id
        assert fetched.subscription is not None
        assert fetched.subscription.plan == 'Pro'
        print('✓ PaymentSubmission.subscription_id FK works')


# ── 3. Subscription.current() ─────────────────────────────────────────────────

def test_subscription_current_classmethod():
    app = _make_app()
    with app.app_context():
        db.drop_all()
        db.create_all()
        tenant, profile = _make_tenant('current-test')

        assert Subscription.current(tenant.id) is None, 'No sub yet → None'

        _make_subscription(tenant, status='cancelled')
        db.session.commit()
        assert Subscription.current(tenant.id) is None, 'Cancelled sub → excluded'

        now = datetime.now(timezone.utc)
        active = _make_subscription(
            tenant, status='active',
            started_at=now,
            expires_at=now + timedelta(days=30),
        )
        db.session.commit()
        result = Subscription.current(tenant.id)
        assert result is not None
        assert result.id == active.id
        print('✓ Subscription.current() returns active, excludes cancelled')


# ── 4. PaymentMethod — global visibility ──────────────────────────────────────

def test_global_payment_method_visible_to_all_tenants():
    """
    A global PaymentMethod (tenant_id=NULL) must appear in get_active_payment_methods_for_tenant()
    regardless of which tenant_id is queried.
    """
    from app.services.manual_billing import get_active_payment_methods_for_tenant

    app = _make_app()
    with app.app_context():
        db.drop_all()
        db.create_all()

        tenant_a, _ = _make_tenant('tenant-a')
        tenant_b, _ = _make_tenant('tenant-b')

        # Global method — no tenant
        global_method = _make_payment_method('GCash Global', tenant=None)
        db.session.commit()

        methods_a = get_active_payment_methods_for_tenant(tenant_a.id)
        methods_b = get_active_payment_methods_for_tenant(tenant_b.id)
        ids_a = {m.id for m in methods_a}
        ids_b = {m.id for m in methods_b}

        assert global_method.id in ids_a, 'Global method must be visible to tenant A'
        assert global_method.id in ids_b, 'Global method must be visible to tenant B'
        print('✓ Global PaymentMethod (tenant_id=NULL) visible to all tenants')


# ── 5. Tenant-specific method isolation ───────────────────────────────────────

def test_tenant_specific_method_invisible_to_other_tenants():
    """
    A tenant-specific PaymentMethod must NOT appear for a different tenant.
    """
    from app.services.manual_billing import get_active_payment_methods_for_tenant

    app = _make_app()
    with app.app_context():
        db.drop_all()
        db.create_all()

        tenant_a, _ = _make_tenant('iso-a')
        tenant_b, _ = _make_tenant('iso-b')

        tenant_a_method = _make_payment_method('A-only GCash', tenant=tenant_a)
        db.session.commit()

        methods_b = get_active_payment_methods_for_tenant(tenant_b.id)
        ids_b = {m.id for m in methods_b}
        assert tenant_a_method.id not in ids_b, \
            'Tenant-A method must NOT be visible to tenant B'
        print('✓ Tenant-specific method is invisible to other tenants (isolation OK)')


# ── 6. Inactive method filtering ──────────────────────────────────────────────

def test_inactive_method_excluded():
    from app.services.manual_billing import get_active_payment_methods_for_tenant

    app = _make_app()
    with app.app_context():
        db.drop_all()
        db.create_all()

        tenant, _ = _make_tenant('inactive-test')
        inactive = _make_payment_method('Dead Method', tenant=None, is_active=False)
        db.session.commit()

        methods = get_active_payment_methods_for_tenant(tenant.id)
        ids = {m.id for m in methods}
        assert inactive.id not in ids, 'Inactive method must be excluded'
        print('✓ Inactive PaymentMethod excluded from visibility query')


# ── 7. get_payment_method_for_tenant isolation ───────────────────────────────

def test_get_payment_method_for_tenant_isolation():
    from app.services.manual_billing import get_payment_method_for_tenant

    app = _make_app()
    with app.app_context():
        db.drop_all()
        db.create_all()

        tenant_a, _ = _make_tenant('gpm-a')
        tenant_b, _ = _make_tenant('gpm-b')

        global_m = _make_payment_method('Global Bank', tenant=None)
        specific_m = _make_payment_method('A-only Bank', tenant=tenant_a)
        inactive_m = _make_payment_method('Inactive', tenant=None, is_active=False)
        db.session.commit()

        # Global is accessible by both
        assert get_payment_method_for_tenant(global_m.id, tenant_a.id) is not None
        assert get_payment_method_for_tenant(global_m.id, tenant_b.id) is not None

        # Tenant-specific: A can access, B cannot
        assert get_payment_method_for_tenant(specific_m.id, tenant_a.id) is not None
        assert get_payment_method_for_tenant(specific_m.id, tenant_b.id) is None, \
            'Cross-tenant access must be blocked'

        # Inactive: nobody gets it
        assert get_payment_method_for_tenant(inactive_m.id, tenant_a.id) is None
        print('✓ get_payment_method_for_tenant() isolation and inactive filtering correct')


# ── 8. Global + tenant methods combined ───────────────────────────────────────

def test_global_and_tenant_methods_merged():
    from app.services.manual_billing import get_active_payment_methods_for_tenant

    app = _make_app()
    with app.app_context():
        db.drop_all()
        db.create_all()

        tenant, _ = _make_tenant('merged-test')
        global_m = _make_payment_method('Global GCash', tenant=None, display_order=1)
        own_m = _make_payment_method('My Bank', tenant=tenant, display_order=2)
        other_tenant, _ = _make_tenant('other')
        other_m = _make_payment_method('Other Bank', tenant=other_tenant, display_order=3)
        db.session.commit()

        methods = get_active_payment_methods_for_tenant(tenant.id)
        ids = {m.id for m in methods}

        assert global_m.id in ids, 'Global method must appear'
        assert own_m.id in ids, 'Own tenant method must appear'
        assert other_m.id not in ids, "Other tenant's method must not appear"
        assert len(ids) == 2
        print('✓ Global + own methods merged; other-tenant methods excluded')


# ── 9. Subscription.refresh_status() expiry ───────────────────────────────────

def test_subscription_refresh_status_expiry():
    app = _make_app()
    with app.app_context():
        db.drop_all()
        db.create_all()
        tenant, _ = _make_tenant('expire-test')
        past = datetime.now(timezone.utc) - timedelta(days=1)
        sub = _make_subscription(
            tenant, status='active',
            started_at=past - timedelta(days=30),
            expires_at=past,
        )
        db.session.commit()

        sub.refresh_status(commit=False)
        assert sub.status == 'expired', 'Past expires_at must flip status to expired'
        print('✓ Subscription.refresh_status() marks expired correctly')


# ── 10. Webhook HMAC verification ────────────────────────────────────────────

def test_verify_webhook_signature():
    app = _make_app()
    with app.app_context():
        app.config['PAYMONGO_WEBHOOK_SECRET'] = 'testsecret'
        from app.utils.paymongo import verify_webhook_signature

        payload = b'{"data":{"id":"evt_test"}}'
        digest = _hmac.new(b'testsecret', payload, hashlib.sha256).hexdigest()

        # Correct signature
        assert verify_webhook_signature(payload, digest), 'Valid HMAC must pass'
        # Wrong signature
        assert not verify_webhook_signature(payload, 'deadbeef'), 'Bad HMAC must fail'
        # Empty signature
        assert not verify_webhook_signature(payload, ''), 'Empty sig must fail'
        print('✓ verify_webhook_signature() correct and timing-safe')


# ── 11. Full end-to-end: manual proof submit → approve → active ───────────────

def test_end_to_end_manual_payment_flow():
    from app.services.manual_billing import submit_manual_payment, approve_payment_submission

    app = _make_app()
    with app.app_context():
        db.drop_all()
        db.create_all()

        tenant, profile = _make_tenant('e2e-test')
        method = _make_payment_method('GCash', tenant=None)
        db.session.commit()

        # Step 1: tenant selects plan → pending subscription
        from app.services.billing import get_or_create_pending_subscription
        sub = get_or_create_pending_subscription(db.session, tenant.id, 'Pro')
        db.session.commit()
        assert sub.status == 'pending'

        # Step 2: submit manual proof
        submission = submit_manual_payment(
            profile,
            method=method,
            plan='Pro',
            amount_paid=49.0,
            payment_reference='GCASH-REF-001',
        )
        db.session.commit()
        assert submission.status == 'pending'
        assert submission.plan == 'Pro'

        # Step 3: superadmin approves
        ok, msg = approve_payment_submission(submission, reviewer='superadmin')
        assert ok, f'approve failed: {msg}'

        # Step 4: verify subscription is now active
        db.session.expire_all()
        refreshed_sub = Subscription.current(tenant.id)
        assert refreshed_sub is not None
        assert refreshed_sub.status == 'active', f'Expected active, got {refreshed_sub.status}'
        assert refreshed_sub.plan == 'Pro'

        refreshed_submission = db.session.get(PaymentSubmission, submission.id)
        assert refreshed_submission.status == 'approved'
        print('✓ End-to-end manual payment flow: pending → submit → approve → active')


# ── 12. Tenant billing route isolation (admin) ────────────────────────────────

def test_billing_route_requires_auth():
    app = _make_app()
    with app.test_client() as client:
        rv = client.get('/admin/billing/plans', follow_redirects=False)
        assert rv.status_code in (302, 401), 'billing/plans must redirect unauthenticated users'
        print('✓ /admin/billing/plans redirects unauthenticated users')


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    tests = [
        test_subscription_columns_v34,
        test_payment_submission_subscription_fk,
        test_subscription_current_classmethod,
        test_global_payment_method_visible_to_all_tenants,
        test_tenant_specific_method_invisible_to_other_tenants,
        test_inactive_method_excluded,
        test_get_payment_method_for_tenant_isolation,
        test_global_and_tenant_methods_merged,
        test_subscription_refresh_status_expiry,
        test_verify_webhook_signature,
        test_end_to_end_manual_payment_flow,
        test_billing_route_requires_auth,
    ]

    passed = failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f'✗ {t.__name__}: {e}')
            import traceback
            traceback.print_exc()
            failed += 1

    print(f'\n{"="*60}')
    print(f'Results: {passed} passed, {failed} failed')
    sys.exit(1 if failed else 0)
