"""Tests for manual payment methods workflow (v3.4)."""

from datetime import datetime, timezone

from app import create_app, db
from app.models.portfolio import PaymentMethod, PaymentSubmission, Profile, Subscription, Tenant
from app.services.manual_billing import (
    approve_payment_submission,
    get_active_payment_methods,
    submit_manual_payment,
)


def _make_app():
    app = create_app('testing')
    return app


def _seed_tenant(slug='manual-test'):
    tenant = Tenant(slug=slug, company_name='Manual Test', email='test@example.com', status='active', plan='Basic')
    db.session.add(tenant)
    db.session.flush()
    profile = Profile(tenant=tenant, name='Test', title='Dev', email='admin@example.com')
    db.session.add(profile)
    db.session.commit()
    return tenant, profile


def test_payment_method_scoped_to_tenant():
    app = _make_app()
    with app.app_context():
        db.create_all()
        tenant_a, _ = _seed_tenant('tenant-a')
        tenant_b, _ = _seed_tenant('tenant-b')

        global_method = PaymentMethod(name='GCash', method_type='ewallet', is_active=True)
        tenant_method = PaymentMethod(name='BPI', method_type='bank', tenant_id=tenant_a.id, is_active=True)
        db.session.add_all([global_method, tenant_method])
        db.session.commit()

        methods_a = get_active_payment_methods(tenant_a.id)
        methods_b = get_active_payment_methods(tenant_b.id)
        assert len(methods_a) == 2
        assert len(methods_b) == 1
        assert methods_b[0].name == 'GCash'
        print('OK: Payment methods respect tenant isolation')


def test_manual_submit_and_approve():
    app = _make_app()
    with app.app_context():
        db.create_all()
        tenant, profile = _seed_tenant('flow-manual')
        method = PaymentMethod(name='Maya', method_type='ewallet', is_active=True, account_number='09171234567')
        db.session.add(method)
        db.session.commit()

        submission = submit_manual_payment(
            profile,
            method=method,
            plan='Pro',
            amount_paid=990.0,
            payment_reference='MAYA-123',
            note='Test payment',
        )
        assert submission.status == 'pending'
        assert submission.payment_method_id == method.id

        ok, msg = approve_payment_submission(submission, reviewer='superadmin')
        assert ok, msg
        sub = Subscription.query.filter_by(tenant_id=tenant.id).first()
        assert sub is not None
        assert sub.status == 'active'
        assert submission.status == 'approved'
        print('OK: Manual submit + approve activates subscription')


if __name__ == '__main__':
    test_payment_method_scoped_to_tenant()
    test_manual_submit_and_approve()
    print('All manual billing tests passed.')
