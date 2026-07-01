#!/usr/bin/env python
"""Billing v3.4 — PayMongo automated subscription tests."""

import hashlib
import hmac
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app, db
from app.models.portfolio import Tenant, Profile, Subscription, WebhookEvent, normalize_plan_name
from app.services.billing import (
    activate_subscription,
    subscription_access_status,
    is_in_grace_period,
    plan_duration_days,
)


def _make_app():
    return create_app('testing')


def _make_tenant(slug, plan='Basic', trial_days=0):
    tenant = Tenant(slug=slug, company_name=slug, email=f'{slug}@test.com', plan=plan)
    db.session.add(tenant)
    db.session.flush()
    trial_ends = (
        datetime.now(timezone.utc) + timedelta(days=trial_days) if trial_days else None
    )
    profile = Profile(
        tenant=tenant, name=slug, email=f'{slug}@test.com', plan=plan,
        free_trial_days=trial_days, free_trial_ends=trial_ends,
    )
    db.session.add(profile)
    db.session.flush()
    return tenant, profile


def _make_sub(tenant, **kwargs):
    data = {'plan': 'Basic', 'status': 'pending'}
    data.update(kwargs)
    sub = Subscription(tenant=tenant, **data)
    db.session.add(sub)
    db.session.flush()
    return sub


def test_subscription_paymongo_columns():
    app = _make_app()
    with app.app_context():
        db.drop_all()
        db.create_all()
        tenant, _ = _make_tenant('cols')
        sub = _make_sub(tenant, paymongo_id='cs_test', paymongo_payment_id='pay_test')
        db.session.commit()
        fetched = db.session.get(Subscription, sub.id)
        assert fetched.paymongo_id == 'cs_test'
        assert fetched.paymongo_payment_id == 'pay_test'
        assert fetched.status == 'pending'
        print('OK subscription PayMongo columns')


def test_activate_subscription():
    app = _make_app()
    with app.app_context():
        db.drop_all()
        db.create_all()
        tenant, profile = _make_tenant('act')
        sub = _make_sub(tenant, plan='Pro')
        db.session.commit()
        activate_subscription(sub, plan='Pro', paymongo_payment_id='pay_123', amount=49.0)
        assert sub.status == 'active'
        assert sub.paymongo_payment_id == 'pay_123'
        assert profile.effective_plan() == 'Pro'
        print('OK activate_subscription')


def test_grace_period():
    app = _make_app()
    with app.app_context():
        db.drop_all()
        db.create_all()
        tenant, profile = _make_tenant('grace')
        sub = _make_sub(
            tenant, status='active',
            started_at=datetime.now(timezone.utc) - timedelta(days=35),
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        sub.status = 'expired'
        db.session.commit()
        assert is_in_grace_period(profile) is True
        assert subscription_access_status(profile) == 'grace'
        print('OK grace period')


def test_webhook_signature():
    app = _make_app()
    with app.app_context():
        app.config['PAYMONGO_WEBHOOK_SECRET'] = 'whsec_test'
        from app.utils.paymongo import verify_webhook_signature
        payload = b'{"data":{"id":"evt_1"}}'
        sig = hmac.new(b'whsec_test', payload, hashlib.sha256).hexdigest()
        assert verify_webhook_signature(payload, sig) is True
        assert verify_webhook_signature(payload, 'bad') is False
        print('OK webhook signature')


def test_webhook_idempotency():
    app = _make_app()
    with app.app_context():
        db.drop_all()
        db.create_all()
        app.config['PAYMONGO_WEBHOOK_SECRET'] = 'whsec_test'
        from app.utils.paymongo import handle_payment_webhook

        tenant, profile = _make_tenant('wh')
        sub = _make_sub(tenant, plan='Basic')
        db.session.commit()

        event = {
            'type': 'payment.paid',
            'data': {
                'id': 'evt_unique_1',
                'attributes': {
                    'amount': 1900,
                    'metadata': {
                        'tenant_id': str(tenant.id),
                        'subscription_id': str(sub.id),
                        'plan_name': 'Basic',
                        'billing_cycle': 'monthly',
                    },
                },
            },
        }
        assert handle_payment_webhook(event) is True
        assert sub.status == 'active'
        assert WebhookEvent.query.filter_by(event_id='evt_unique_1').count() == 1
        handle_payment_webhook(event)  # duplicate
        assert WebhookEvent.query.filter_by(event_id='evt_unique_1').count() == 1
        print('OK webhook idempotency')


def test_license_route_redirects():
    app = _make_app()
    with app.app_context():
        db.drop_all()
        db.create_all()
        client = app.test_client()
        resp = client.get('/admin/license', follow_redirects=False)
        assert resp.status_code in (302, 401)
        print('OK license route deprecated')


if __name__ == '__main__':
    tests = [
        test_subscription_paymongo_columns,
        test_activate_subscription,
        test_grace_period,
        test_webhook_signature,
        test_webhook_idempotency,
        test_license_route_redirects,
    ]
    passed = failed = 0
    for fn in tests:
        try:
            fn()
            passed += 1
        except Exception as e:
            print(f'FAIL {fn.__name__}: {e}')
            failed += 1
    print(f'\n{passed} passed, {failed} failed')
    sys.exit(1 if failed else 0)
