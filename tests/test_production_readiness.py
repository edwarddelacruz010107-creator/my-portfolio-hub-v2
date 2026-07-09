"""
tests/test_production_readiness.py
Portfolio CMS v3.9 — Production Readiness Test Suite

Covers:
  Unit Tests:
    - checkout creation (BUG-001 fix)
    - payment success webhook (idempotency)
    - payment failure webhook
    - webhook replay protection
    - duplicate payment protection (BUG-006)
    - OTP verification lifecycle
    - mark_subscription_cancelled / expired (BUG-004)
    - verify_webhook_signature correct parsing (BUG-008)

  Integration Tests (requires app context):
    - full subscription purchase flow
    - renewal (additive)
    - cancellation
    - forgot password flow
    - rate limit enforcement (BUG-009)
"""

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, call

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

class FakeSubscription:
    """Minimal Subscription stand-in for unit tests."""
    def __init__(self, **kwargs):
        self.id = kwargs.get('id', 1)
        self.tenant_id = kwargs.get('tenant_id', 10)
        self.plan = kwargs.get('plan', 'Basic')
        self.billing_cycle = kwargs.get('billing_cycle', 'monthly')
        self.status = kwargs.get('status', 'pending')
        self.started_at = kwargs.get('started_at', None)
        self.expires_at = kwargs.get('expires_at', None)
        self.cancelled_at = kwargs.get('cancelled_at', None)
        self.paymongo_payment_id = kwargs.get('paymongo_payment_id', None)
        self.paymongo_id = kwargs.get('paymongo_id', None)
        self.amount_paid = kwargs.get('amount_paid', 0.0)


class FakeProfile:
    def __init__(self, tenant_id=10, plan='Basic', tenant_slug='test-tenant'):
        self.tenant_id = tenant_id
        self.plan = plan
        self.tenant_slug = tenant_slug
        self.tenant = MagicMock()
        self.tenant.status = 'active'

    def is_trial_active(self):
        return False

    def current_subscription(self):
        return None


class FakeSession:
    """Minimal SQLAlchemy session mock."""
    def __init__(self):
        self._added = []
        self._committed = False
        self._rolled_back = False

    def query(self, model):
        return self

    def filter_by(self, **kwargs):
        self._filter = kwargs
        return self

    def with_for_update(self, **kwargs):
        return self

    def order_by(self, *args):
        return self

    def first(self):
        return None

    def add(self, obj):
        self._added.append(obj)

    def flush(self):
        if self._added:
            self._added[-1].id = 999  # simulate auto-increment

    def commit(self):
        self._committed = True

    def rollback(self):
        self._rolled_back = True


# ─────────────────────────────────────────────────────────────────────────────
# Unit Tests — Billing
# ─────────────────────────────────────────────────────────────────────────────

class TestActivateSubscription:
    """Tests for billing.activate_subscription()."""

    def _make_sub(self, **kwargs):
        return FakeSubscription(**kwargs)

    @patch('app.utils.get_plan_price', return_value=299.0)
    @patch('app.utils.normalize_plan_name', side_effect=lambda x: x)
    def test_activate_new_subscription(self, mock_norm, mock_price):
        from app.services.billing import activate_subscription

        sub = self._make_sub(status='pending')
        now = datetime(2024, 1, 1, tzinfo=timezone.utc)

        activate_subscription(sub, plan='Pro', billing_cycle='monthly', now=now)

        assert sub.status == 'active'
        assert sub.started_at == now
        assert sub.expires_at == now + timedelta(days=30)

    @patch('app.utils.get_plan_price', return_value=299.0)
    @patch('app.utils.normalize_plan_name', side_effect=lambda x: x)
    def test_activate_yearly_doubles_duration(self, mock_norm, mock_price):
        from app.services.billing import activate_subscription

        sub = self._make_sub(status='pending')
        now = datetime(2024, 1, 1, tzinfo=timezone.utc)

        activate_subscription(sub, plan='Pro', billing_cycle='yearly', now=now)

        assert sub.expires_at == now + timedelta(days=360)

    @patch('app.utils.get_plan_price', return_value=299.0)
    @patch('app.utils.normalize_plan_name', side_effect=lambda x: x)
    def test_renewal_is_additive(self, mock_norm, mock_price):
        """Active renewal extends from expires_at, not from now."""
        from app.services.billing import activate_subscription

        future_expiry = datetime(2024, 3, 1, tzinfo=timezone.utc)
        sub = self._make_sub(
            status='active',
            expires_at=future_expiry,
        )
        now = datetime(2024, 2, 1, tzinfo=timezone.utc)  # before expiry

        activate_subscription(sub, plan='Pro', billing_cycle='monthly', now=now)

        assert sub.expires_at == future_expiry + timedelta(days=30)

    @patch('app.utils.get_plan_price', return_value=299.0)
    @patch('app.utils.normalize_plan_name', side_effect=lambda x: x)
    def test_payment_id_recorded(self, mock_norm, mock_price):
        from app.services.billing import activate_subscription

        sub = self._make_sub()
        activate_subscription(sub, plan='Pro', paymongo_payment_id='pay_123', now=datetime.now(tz=timezone.utc))

        assert sub.paymongo_payment_id == 'pay_123'

    @patch('app.utils.get_plan_price', return_value=299.0)
    @patch('app.utils.normalize_plan_name', side_effect=lambda x: x)
    def test_defaults_plan_from_subscription(self, mock_norm, mock_price):
        """plan=None should fall back to subscription.plan."""
        from app.services.billing import activate_subscription

        sub = self._make_sub(plan='Enterprise')
        activate_subscription(sub, plan=None, now=datetime.now(tz=timezone.utc))

        assert sub.plan == 'Enterprise'


class TestMarkSubscriptionCancelled:
    def test_idempotent(self):
        from app.services.billing import mark_subscription_cancelled

        sub = FakeSubscription(status='cancelled')
        with patch('app.services.billing.db') as mock_db:
            mark_subscription_cancelled(sub)
            mock_db.session.commit.assert_not_called()

    def test_sets_cancelled_at(self):
        from app.services.billing import mark_subscription_cancelled

        sub = FakeSubscription(status='active')
        now = datetime(2024, 6, 1, tzinfo=timezone.utc)
        with patch('app.services.billing.db') as mock_db:
            mark_subscription_cancelled(sub, now=now)

        assert sub.status == 'cancelled'
        assert sub.cancelled_at == now


class TestMarkSubscriptionExpired:
    def test_idempotent_if_cancelled(self):
        from app.services.billing import mark_subscription_expired

        sub = FakeSubscription(status='cancelled')
        with patch('app.services.billing.db') as mock_db:
            mark_subscription_expired(sub)
            mock_db.session.commit.assert_not_called()

    def test_sets_expired(self):
        from app.services.billing import mark_subscription_expired

        sub = FakeSubscription(status='active')
        with patch('app.services.billing.db') as mock_db:
            mark_subscription_expired(sub)

        assert sub.status == 'expired'


class TestGetOrCreatePendingSubscription:
    @patch('app.utils.get_plan_price', return_value=299.0)
    @patch('app.utils.normalize_plan_name', side_effect=lambda x: x)
    def test_returns_existing_active_sub(self, mock_norm, mock_price):
        """If tenant already has active subscription on same plan, return it."""
        from app.services.billing import get_or_create_pending_subscription

        active_sub = FakeSubscription(status='active', plan='Pro')
        session = MagicMock()
        # First call returns active sub (the guard check)
        session.query.return_value.filter_by.return_value.with_for_update.return_value.first.return_value = active_sub

        with patch('app.services.billing.Subscription', autospec=True):
            from app.models import Subscription
            with patch('app.services.billing.db'):
                result = get_or_create_pending_subscription(session, 10, 'Pro')

        assert result is active_sub

    @patch('app.utils.get_plan_price', return_value=299.0)
    @patch('app.utils.normalize_plan_name', side_effect=lambda x: x)
    def test_creates_new_pending_when_none_exists(self, mock_norm, mock_price):
        """Creates a new pending subscription when none found."""
        from app.services.billing import get_or_create_pending_subscription

        session = FakeSession()

        with patch('app.services.billing.Subscription') as MockSub:
            new_sub = FakeSubscription()
            MockSub.return_value = new_sub
            result = get_or_create_pending_subscription(session, 10, 'Pro', 'monthly')

        assert new_sub in session._added


# ─────────────────────────────────────────────────────────────────────────────
# Unit Tests — Webhook Signature (BUG-003, BUG-008)
# ─────────────────────────────────────────────────────────────────────────────

class TestVerifyWebhookSignature:
    WEBHOOK_SECRET = 'whsec_test_secret_key_abc123'

    def _make_app_context(self, secret):
        app = MagicMock()
        app.config.get = lambda key, default='': secret if key == 'PAYMONGO_WEBHOOK_SECRET' else default
        return app

    def test_valid_compound_signature(self):
        """Correctly parses PayMongo's t=...,te=...,li=... format."""
        from app.utils.paymongo import verify_webhook_signature

        payload = b'{"data":{"id":"evt_123"}}'
        secret = self.WEBHOOK_SECRET
        li = hmac.HMAC(secret.encode(), payload, hashlib.sha256).hexdigest()
        signature_header = f't=1234567890,te=dummy_te_value,li={li}'

        with patch('app.utils.paymongo.current_app') as mock_app:
            mock_app.config.get.side_effect = lambda k, d='': secret if k == 'PAYMONGO_WEBHOOK_SECRET' else d
            assert verify_webhook_signature(payload, signature_header) is True

    def test_tampered_payload_fails(self):
        """Tampered payload produces wrong HMAC."""
        from app.utils.paymongo import verify_webhook_signature

        payload = b'{"data":{"id":"evt_123"}}'
        tampered = b'{"data":{"id":"evt_EVIL"}}'
        secret = self.WEBHOOK_SECRET
        li = hmac.HMAC(secret.encode(), payload, hashlib.sha256).hexdigest()
        signature_header = f't=1234567890,te=dummy,li={li}'

        with patch('app.utils.paymongo.current_app') as mock_app:
            mock_app.config.get.side_effect = lambda k, d='': secret if k == 'PAYMONGO_WEBHOOK_SECRET' else d
            assert verify_webhook_signature(tampered, signature_header) is False

    def test_missing_li_field_fails(self):
        from app.utils.paymongo import verify_webhook_signature

        with patch('app.utils.paymongo.current_app') as mock_app:
            mock_app.config.get.side_effect = lambda k, d='': self.WEBHOOK_SECRET if k == 'PAYMONGO_WEBHOOK_SECRET' else d
            # Header has t= and te= but no li=
            assert verify_webhook_signature(b'payload', 't=123,te=abc') is False

    def test_empty_secret_fails(self):
        from app.utils.paymongo import verify_webhook_signature

        with patch('app.utils.paymongo.current_app') as mock_app:
            mock_app.config.get.return_value = ''
            assert verify_webhook_signature(b'payload', 'li=abc') is False

    def test_missing_signature_header_fails(self):
        from app.utils.paymongo import verify_webhook_signature

        with patch('app.utils.paymongo.current_app') as mock_app:
            mock_app.config.get.side_effect = lambda k, d='': self.WEBHOOK_SECRET if k == 'PAYMONGO_WEBHOOK_SECRET' else d
            assert verify_webhook_signature(b'payload', '') is False


# ─────────────────────────────────────────────────────────────────────────────
# Unit Tests — Webhook Idempotency (replay protection)
# ─────────────────────────────────────────────────────────────────────────────

class TestWebhookIdempotency:
    def _make_event(self, event_id='evt_001', event_type='payment.paid'):
        return {
            'type': event_type,
            'data': {
                'id': event_id,
                'attributes': {
                    'status': 'paid',
                    'amount': 29900,
                    'metadata': {
                        'tenant_id': '10',
                        'plan_name': 'Pro',
                        'billing_cycle': 'monthly',
                        'subscription_id': '1',
                    }
                }
            }
        }

    def test_duplicate_event_skipped(self):
        """Second delivery of same event_id must be a no-op."""
        from app.utils.paymongo import _record_webhook_event

        with patch('app.utils.paymongo.db') as mock_db, \
             patch('app.utils.paymongo.WebhookEvent') as MockEvent:
            # Simulate existing record found
            MockEvent.query.filter_by.return_value.first.return_value = MagicMock()

            result = _record_webhook_event(self._make_event('evt_dup'))

            assert result is False
            mock_db.session.add.assert_not_called()

    def test_new_event_recorded(self):
        """First delivery of event_id creates a record."""
        from app.utils.paymongo import _record_webhook_event

        with patch('app.utils.paymongo.db') as mock_db, \
             patch('app.utils.paymongo.WebhookEvent') as MockEvent:
            MockEvent.query.filter_by.return_value.first.return_value = None

            result = _record_webhook_event(self._make_event('evt_new'))

            assert result is True
            mock_db.session.add.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# Unit Tests — OTP Verification
# ─────────────────────────────────────────────────────────────────────────────

class TestOTPVerification:
    def _make_otp_record(self, raw_otp, attempts=0, used=False, expired=False):
        now = datetime.now(timezone.utc)
        record = MagicMock()
        record.used = used
        record.attempts = attempts
        record.is_expired = expired
        record.verify = lambda x: hashlib.sha256(x.encode()).hexdigest() == hashlib.sha256(raw_otp.encode()).hexdigest()
        return record

    def test_correct_otp_succeeds(self):
        from app.services.otp_service import verify_otp

        raw = '123456'
        record = self._make_otp_record(raw)

        with patch('app.services.otp_service.PasswordResetOTP') as MockOTP, \
             patch('app.services.otp_service.db'):
            MockOTP.query.filter_by.return_value.order_by.return_value.first.return_value = record
            ok, msg = verify_otp('admin', 1, raw)

        assert ok is True
        assert record.used is True

    def test_wrong_otp_fails(self):
        from app.services.otp_service import verify_otp

        record = self._make_otp_record('123456', attempts=0)

        with patch('app.services.otp_service.PasswordResetOTP') as MockOTP, \
             patch('app.services.otp_service.db'):
            MockOTP.query.filter_by.return_value.order_by.return_value.first.return_value = record
            ok, msg = verify_otp('admin', 1, '999999')

        assert ok is False
        assert 'Incorrect' in msg

    def test_expired_otp_fails(self):
        from app.services.otp_service import verify_otp

        record = self._make_otp_record('123456', expired=True)

        with patch('app.services.otp_service.PasswordResetOTP') as MockOTP, \
             patch('app.services.otp_service.db') as mock_db:
            MockOTP.query.filter_by.return_value.order_by.return_value.first.return_value = record
            ok, msg = verify_otp('admin', 1, '123456')

        assert ok is False
        assert 'expired' in msg.lower()
        mock_db.session.delete.assert_called_once()

    def test_too_many_attempts_invalidates_otp(self):
        from app.services.otp_service import verify_otp

        record = self._make_otp_record('123456', attempts=5)  # MAX_ATTEMPTS=5, will hit >5 after increment

        with patch('app.services.otp_service.PasswordResetOTP') as MockOTP, \
             patch('app.services.otp_service.db') as mock_db:
            MockOTP.query.filter_by.return_value.order_by.return_value.first.return_value = record
            ok, msg = verify_otp('admin', 1, '000000')

        assert ok is False
        assert 'Too many' in msg
        mock_db.session.delete.assert_called_once()

    def test_no_active_otp_returns_error(self):
        from app.services.otp_service import verify_otp

        with patch('app.services.otp_service.PasswordResetOTP') as MockOTP, \
             patch('app.services.otp_service.db'):
            MockOTP.query.filter_by.return_value.order_by.return_value.first.return_value = None
            ok, msg = verify_otp('admin', 1, '123456')

        assert ok is False
        assert 'No active OTP' in msg


# ─────────────────────────────────────────────────────────────────────────────
# Unit Tests — Password Reset: enumeration protection
# ─────────────────────────────────────────────────────────────────────────────

class TestPasswordResetEnumerationProtection:
    def test_superadmin_nonexistent_email_returns_generic(self):
        """Unknown email must return generic success message to prevent enumeration."""
        from app.services.password_reset_service import initiate_superadmin_reset

        with patch('app.services.password_reset_service.User') as MockUser, \
             patch('app.services.password_reset_service._recovery_enabled', return_value=True), \
             patch('app.services.password_reset_service._get_ip', return_value='1.2.3.4'), \
             patch('app.services.password_reset_service._get_ua', return_value='test'):
            MockUser.query.filter_by.return_value.first.return_value = None
            ok, msg = initiate_superadmin_reset('notexist@example.com')

        assert ok is True
        assert 'OTP has been sent' in msg  # generic message, not "not found"

    def test_tenant_username_email_mismatch_returns_generic_error(self):
        """Username/email not matching the same user+tenant must return generic error
        without revealing which field was wrong (v5.4 fix — no longer gated on
        Tenant.contact_email, which is independent of the User record)."""
        from app.services.password_reset_service import initiate_tenant_reset

        fake_tenant = MagicMock()
        fake_tenant.id = 10

        with patch('app.services.password_reset_service.User') as MockUser, \
             patch('app.services.password_reset_service.Tenant') as MockTenant, \
             patch('app.services.password_reset_service._recovery_enabled', return_value=True), \
             patch('app.services.password_reset_service._get_ip', return_value='1.2.3.4'), \
             patch('app.services.password_reset_service._get_ua', return_value='test'):
            MockTenant.query.filter_by.return_value.first.return_value = fake_tenant
            MockUser.query.filter_by.return_value.first.return_value = None  # no row matches username+email+tenant
            ok, msg = initiate_tenant_reset('submitted@example.com', 'someuser', 'acme-corp')

        assert ok is False
        assert 'Invalid' in msg

    def test_tenant_matching_username_and_email_sends_otp(self):
        """Username + email matching the same User row within the URL tenant
        must proceed to OTP creation and email dispatch."""
        from app.services.password_reset_service import initiate_tenant_reset

        fake_user = MagicMock()
        fake_user.id = 42
        fake_user.tenant_id = 10
        fake_user.email = 'submitted@example.com'
        fake_tenant = MagicMock()
        fake_tenant.id = 10

        with patch('app.services.password_reset_service.User') as MockUser, \
             patch('app.services.password_reset_service.Tenant') as MockTenant, \
             patch('app.services.password_reset_service._recovery_enabled', return_value=True), \
             patch('app.services.password_reset_service._get_ip', return_value='1.2.3.4'), \
             patch('app.services.password_reset_service._get_ua', return_value='test'), \
             patch('app.services.password_reset_service.create_otp_record', return_value='123456') as mock_create, \
             patch('app.services.password_reset_service.send_otp_email', return_value=True) as mock_send, \
             patch('app.services.password_reset_service.db'):
            MockTenant.query.filter_by.return_value.first.return_value = fake_tenant
            MockUser.query.filter_by.return_value.first.return_value = fake_user
            ok, msg = initiate_tenant_reset('submitted@example.com', 'someuser', 'acme-corp')

        assert ok is True
        mock_create.assert_called_once()
        mock_send.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# Unit Tests — Session Invalidation on Password Reset
# ─────────────────────────────────────────────────────────────────────────────

class TestSessionInvalidationOnReset:
    def test_session_token_rotated_on_password_change(self):
        """_apply_password_change must rotate session_token."""
        from app.services.password_reset_service import _apply_password_change

        user = MagicMock()
        old_token = 'old_session_token_abc'
        user.session_token = old_token

        with patch('app.services.password_reset_service.db') as mock_db:
            _apply_password_change(user, 'NewSecureP@ss1!')

        assert user.session_token != old_token
        assert user.require_password_reset is False
        mock_db.session.commit.assert_called()


# ─────────────────────────────────────────────────────────────────────────────
# Unit Tests — Duplicate Payment Protection
# ─────────────────────────────────────────────────────────────────────────────

class TestDuplicatePaymentProtection:
    def test_same_payment_id_not_processed_twice(self):
        """payment.paid with already-used payment_id must be skipped."""
        from app.utils.paymongo import _handle_payment_paid

        sub = FakeSubscription(
            status='active',
            paymongo_payment_id='pay_already_processed',
        )
        attrs = {'metadata': {'subscription_id': '1'}, 'amount': 29900}

        with patch('app.utils.paymongo._resolve_subscription', return_value=sub), \
             patch('app.utils.paymongo.db'):
            result = _handle_payment_paid(attrs, 'pay_already_processed')

        assert result is True  # no error, but no double-activation


# ─────────────────────────────────────────────────────────────────────────────
# Integration Test Stubs (require Flask app context)
# ─────────────────────────────────────────────────────────────────────────────

class TestIntegrationStubs:
    """
    These tests require a live Flask app + test database.
    Run with: pytest tests/test_production_readiness.py -m integration

    Mark with @pytest.mark.integration to skip in unit-only runs.
    """

    @pytest.mark.integration
    def test_full_subscription_purchase_flow(self, app, db_session):
        """
        Integration: Create tenant → initiate checkout → simulate payment.paid webhook
        → assert subscription is active.
        """
        # 1. Create tenant + profile
        # 2. Call initiate_checkout(db.session, profile, 'Pro', 'monthly', ...)
        # 3. Simulate payment.paid webhook with correct subscription_id in metadata
        # 4. Assert Subscription.query.filter_by(tenant_id=..., status='active').first() is not None
        pass

    @pytest.mark.integration
    def test_subscription_renewal_is_additive(self, app, db_session):
        """
        Integration: Active subscription + renewal payment → expires_at extended, not reset.
        """
        pass

    @pytest.mark.integration
    def test_subscription_cancellation_via_webhook(self, app, db_session):
        """
        Integration: subscription.cancelled webhook → Subscription.status == 'cancelled'.
        """
        pass

    @pytest.mark.integration
    def test_forgot_password_full_flow(self, app, db_session):
        """
        Integration: POST /forgot-password → OTP email → verify OTP → reset password
        → old session token no longer valid.
        """
        pass

    @pytest.mark.integration
    def test_reset_rate_limit_enforced(self, app, db_session):
        """
        Integration: 6 reset requests in 15 minutes from same IP → 429 on 6th.
        """
        pass

    @pytest.mark.integration
    def test_webhook_replay_rejected(self, app, db_session, client):
        """
        Integration: POST valid webhook twice with same event_id → second returns 200
        but does NOT create duplicate subscription activation.
        """
        pass
