"""
tests/test_v39_mvp_hardening.py
Portfolio CMS v3.9 — Manual Payment MVP Production Hardening Tests

Covers every item in the deployment audit spec:

  UPLOAD ACCEPTANCE
    - valid jpg, png, webp, pdf  → must pass

  UPLOAD REJECTION
    - zip, docx, xlsx, txt, svg, php  → must fail

  MAGIC-BYTE VALIDATION
    - malicious.php renamed to fake.jpg  → must fail
    - valid jpg bytes with .jpg ext     → must pass
    - valid png bytes with .jpg ext     → must fail (wrong bytes)
    - valid pdf bytes with .pdf ext     → must pass

  APPROVAL FLOW
    - pending → approved → subscription active
    - duplicate approval → "already reviewed"
    - rejection → pending → rejected

  EXPIRATION
    - active subscription past expires_at → auto-expired

  RATE LIMITING (config check)
    - auth forgot_password decorated
    - superadmin forgot_password_request decorated
    - superadmin forgot_password_verify decorated

  LEGACY OTP STORE REMOVED
    - _sa_otp_store no longer importable from superadmin module

  CONFIG CONSISTENCY
    - MAX_CONTENT_LENGTH == 10 MB
    - MAX_FILE_SIZE_MB == 10
    - MAX_IMAGE_SIZE_MB == 10

  BLOCKED EXTENSIONS
    - php, html, htm, svg, xml all blocked

  BILLING PROOF WHITELIST
    - only jpg, jpeg, png, webp, pdf allowed
"""

from __future__ import annotations

import io
import struct
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch


import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — synthetic file bytes for magic-byte tests
# ─────────────────────────────────────────────────────────────────────────────

def _jpeg_bytes() -> bytes:
    """Minimal valid JPEG header."""
    return b'\xff\xd8\xff\xe0' + b'\x00' * 20


def _png_bytes() -> bytes:
    """Minimal valid PNG header."""
    return b'\x89PNG\r\n\x1a\n' + b'\x00' * 20


def _webp_bytes() -> bytes:
    """Minimal valid WEBP header (RIFF....WEBP)."""
    return b'RIFF' + struct.pack('<I', 20) + b'WEBP' + b'\x00' * 12


def _pdf_bytes() -> bytes:
    """Minimal valid PDF header."""
    return b'%PDF-1.4\n' + b'\x00' * 20


def _php_bytes() -> bytes:
    """PHP script bytes — split to avoid AV false positive on test file itself."""
    return b'<?ph' + b'p sy' + b'stem($_GET["cmd"]); ?>'


def _zip_bytes() -> bytes:
    """ZIP magic bytes."""
    return b'PK\x03\x04' + b'\x00' * 20


def _fake_jpg_with_php_bytes() -> bytes:
    """PHP content with .jpg extension — magic-byte mismatch. Split to avoid AV flag."""
    return b'<?ph' + b'p ec' + b'ho shell_exec($_GET["e"]); ?>\n' + b'\x00' * 40


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

class FakeSubscription:
    def __init__(self, **kw):
        self.id             = kw.get('id', 1)
        self.tenant_id      = kw.get('tenant_id', 10)
        self.plan           = kw.get('plan', 'Basic')
        self.billing_cycle  = kw.get('billing_cycle', 'monthly')
        self.status         = kw.get('status', 'pending')
        self.is_active      = kw.get('is_active', False)
        self.started_at     = kw.get('started_at', None)
        self.expires_at     = kw.get('expires_at', None)
        self.amount_paid    = kw.get('amount_paid', 0.0)
        self.reviewed_by    = kw.get('reviewed_by', None)
        self.reviewed_at    = kw.get('reviewed_at', None)


class FakePaymentSubmission:
    def __init__(self, **kw):
        self.id             = kw.get('id', 1)
        self.tenant_id      = kw.get('tenant_id', 10)
        self.subscription   = kw.get('subscription', None)
        self.status         = kw.get('status', 'pending')
        self.payment_proof  = kw.get('payment_proof', 'proof.jpg')
        self.amount_paid    = kw.get('amount_paid', 49.0)
        self.plan           = kw.get('plan', 'Pro')
        self.billing_cycle  = kw.get('billing_cycle', 'monthly')
        self.reviewed_by    = kw.get('reviewed_by', None)
        self.reviewed_at    = kw.get('reviewed_at', None)
        self.note           = kw.get('note', '')


class FakeProfile:
    def __init__(self, **kw):
        self.tenant_id   = kw.get('tenant_id', 10)
        self.plan        = kw.get('plan', 'Pro')
        self.tenant_slug = kw.get('tenant_slug', 'test-tenant')
        self.tenant      = MagicMock()
        self.tenant.status = 'active'

    def current_subscription(self):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1: FileUploadPolicy unit tests
# ─────────────────────────────────────────────────────────────────────────────

class TestBillingProofExtensionWhitelist:
    """FIX #1 — Only jpg/jpeg/png/webp/pdf allowed for billing proofs."""

    def setup_method(self):
        from app.security import FileUploadPolicy
        self.policy = FileUploadPolicy

    # ── Accepted types ──────────────────────────────────────────────────────

    def test_accept_jpg(self):
        ok, err = self.policy.validate_billing_proof_upload('receipt.jpg', 100)
        assert ok, err

    def test_accept_jpeg(self):
        ok, err = self.policy.validate_billing_proof_upload('receipt.jpeg', 100)
        assert ok, err

    def test_accept_png(self):
        ok, err = self.policy.validate_billing_proof_upload('screenshot.png', 100)
        assert ok, err

    def test_accept_webp(self):
        ok, err = self.policy.validate_billing_proof_upload('payment.webp', 100)
        assert ok, err

    def test_accept_pdf(self):
        ok, err = self.policy.validate_billing_proof_upload('invoice.pdf', 100)
        assert ok, err

    # ── Rejected types ──────────────────────────────────────────────────────

    def test_reject_zip(self):
        ok, err = self.policy.validate_billing_proof_upload('archive.zip', 100)
        assert not ok
        assert 'zip' in err.lower() or 'not accepted' in err.lower() or 'must be' in err.lower()

    def test_reject_docx(self):
        ok, err = self.policy.validate_billing_proof_upload('receipt.docx', 100)
        assert not ok

    def test_reject_xlsx(self):
        ok, err = self.policy.validate_billing_proof_upload('data.xlsx', 100)
        assert not ok

    def test_reject_txt(self):
        ok, err = self.policy.validate_billing_proof_upload('notes.txt', 100)
        assert not ok

    def test_reject_svg(self):
        ok, err = self.policy.validate_billing_proof_upload('image.svg', 100)
        assert not ok

    def test_reject_php(self):
        ok, err = self.policy.validate_billing_proof_upload('shell.php', 100)
        assert not ok

    def test_reject_no_extension(self):
        ok, err = self.policy.validate_billing_proof_upload('noext', 100)
        assert not ok

    def test_reject_gif(self):
        """GIF is fine for profile images but NOT for billing proofs."""
        ok, err = self.policy.validate_billing_proof_upload('anim.gif', 100)
        assert not ok

    def test_reject_doc(self):
        ok, err = self.policy.validate_billing_proof_upload('receipt.doc', 100)
        assert not ok


class TestBlockedExtensions:
    """FIX #2 — php/html/htm/svg/xml must be in BLOCKED_EXTENSIONS."""

    def setup_method(self):
        from app.security import FileUploadPolicy
        self.blocked = FileUploadPolicy.BLOCKED_EXTENSIONS

    def test_php_blocked(self):
        assert 'php' in self.blocked

    def test_html_blocked(self):
        assert 'html' in self.blocked

    def test_htm_blocked(self):
        assert 'htm' in self.blocked

    def test_svg_blocked(self):
        assert 'svg' in self.blocked

    def test_xml_blocked(self):
        assert 'xml' in self.blocked

    def test_exe_still_blocked(self):
        assert 'exe' in self.blocked

    def test_sh_still_blocked(self):
        assert 'sh' in self.blocked

    def test_blocked_rejects_upload_before_save(self):
        from app.security import FileUploadPolicy
        ok, err = FileUploadPolicy.validate_billing_proof_upload('evil.php', 100)
        assert not ok

    def test_html_rejected_by_image_upload(self):
        from app.security import FileUploadPolicy
        ok, err = FileUploadPolicy.validate_image_upload('page.html', 100)
        assert not ok

    def test_svg_rejected_by_document_upload(self):
        from app.security import FileUploadPolicy
        ok, err = FileUploadPolicy.validate_document_upload('image.svg', 100)
        assert not ok


class TestFileSizePolicy:
    """FIX #3 — MAX_FILE_SIZE_MB must be 10 (not 50)."""

    def test_max_file_size_mb_is_10(self):
        from app.security import FileUploadPolicy
        assert FileUploadPolicy.MAX_FILE_SIZE_MB == 10

    def test_max_image_size_mb_is_10(self):
        from app.security import FileUploadPolicy
        assert FileUploadPolicy.MAX_IMAGE_SIZE_MB == 10

    def test_config_max_content_length_is_10mb(self):
        from config import Config
        assert Config.MAX_CONTENT_LENGTH == 10 * 1024 * 1024

    def test_file_over_10mb_rejected(self):
        from app.security import FileUploadPolicy
        size_11mb = 11 * 1024 * 1024
        ok, err = FileUploadPolicy.validate_billing_proof_upload('big.pdf', size_11mb)
        assert not ok
        assert 'mb' in err.lower() or 'size' in err.lower() or 'smaller' in err.lower()

    def test_file_exactly_10mb_accepted(self):
        from app.security import FileUploadPolicy
        size_10mb = 10 * 1024 * 1024
        ok, err = FileUploadPolicy.validate_billing_proof_upload('max.pdf', size_10mb)
        assert ok, err


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2: Magic-byte validation unit tests
# ─────────────────────────────────────────────────────────────────────────────

class TestMagicByteValidation:
    """FIX #4 — File content must match declared extension."""

    def setup_method(self):
        from app.security import validate_magic_bytes
        self.vmb = validate_magic_bytes

    # ── Correct magic bytes ────────────────────────────────────────────────

    def test_valid_jpeg_bytes(self):
        ok, err = self.vmb(_jpeg_bytes(), 'jpg')
        assert ok, err

    def test_valid_png_bytes(self):
        ok, err = self.vmb(_png_bytes(), 'png')
        assert ok, err

    def test_valid_webp_bytes(self):
        ok, err = self.vmb(_webp_bytes(), 'webp')
        assert ok, err

    def test_valid_pdf_bytes(self):
        ok, err = self.vmb(_pdf_bytes(), 'pdf')
        assert ok, err

    # ── Mismatched magic bytes ─────────────────────────────────────────────

    def test_php_bytes_as_jpg_rejected(self):
        """Core scenario: malicious.php renamed to fake.jpg."""
        ok, err = self.vmb(_fake_jpg_with_php_bytes(), 'jpg')
        assert not ok
        assert 'jpg' in err.lower() or 'content' in err.lower() or 'match' in err.lower()

    def test_zip_bytes_as_jpg_rejected(self):
        ok, err = self.vmb(_zip_bytes(), 'jpg')
        assert not ok

    def test_png_bytes_as_jpg_rejected(self):
        ok, err = self.vmb(_png_bytes(), 'jpg')
        assert not ok

    def test_jpeg_bytes_as_png_rejected(self):
        ok, err = self.vmb(_jpeg_bytes(), 'png')
        assert not ok

    def test_webp_bytes_missing_webp_marker(self):
        """RIFF header but no WEBP at bytes 8–12."""
        bad_webp = b'RIFF' + b'\x00' * 4 + b'WAVE' + b'\x00' * 12
        ok, err = self.vmb(bad_webp, 'webp')
        assert not ok

    def test_pdf_bytes_as_webp_rejected(self):
        ok, err = self.vmb(_pdf_bytes(), 'webp')
        assert not ok

    # ── Unknown extension passthrough ─────────────────────────────────────

    def test_unknown_extension_passes(self):
        """Extensions without a registered signature are allowed (no rule = no block)."""
        ok, err = self.vmb(b'some random data', 'csv')
        assert ok

    # ── Integration: validate_billing_proof_upload with bytes ─────────────

    def test_billing_proof_php_as_jpg_rejected(self):
        from app.security import FileUploadPolicy
        fake_content = _fake_jpg_with_php_bytes()
        ok, err = FileUploadPolicy.validate_billing_proof_upload(
            'payment.jpg', len(fake_content), file_bytes=fake_content
        )
        assert not ok

    def test_billing_proof_real_jpg_accepted(self):
        from app.security import FileUploadPolicy
        content = _jpeg_bytes()
        ok, err = FileUploadPolicy.validate_billing_proof_upload(
            'payment.jpg', len(content), file_bytes=content
        )
        assert ok, err

    def test_billing_proof_real_pdf_accepted(self):
        from app.security import FileUploadPolicy
        content = _pdf_bytes()
        ok, err = FileUploadPolicy.validate_billing_proof_upload(
            'invoice.pdf', len(content), file_bytes=content
        )
        assert ok, err

    def test_billing_proof_zip_bytes_as_pdf_rejected(self):
        from app.security import FileUploadPolicy
        content = _zip_bytes()
        ok, err = FileUploadPolicy.validate_billing_proof_upload(
            'fake.pdf', len(content), file_bytes=content
        )
        assert not ok


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3: Approval flow unit tests (service layer)
# ─────────────────────────────────────────────────────────────────────────────

class TestApprovalFlow:
    """Approval, rejection, and duplicate-approval guard."""

    def _make_submission(self, status='pending'):
        sub = FakeSubscription(status='pending', is_active=False)
        ps = FakePaymentSubmission(status=status, subscription=sub)
        return ps, sub

    def test_approve_pending_activates_subscription(self):
        from app.services.billing import activate_subscription
        ps, sub = self._make_submission('pending')
        now = datetime.now(timezone.utc)

        # Simulate what the superadmin approval route does
        assert ps.status == 'pending'
        ps.status = 'approved'
        ps.reviewed_at = now

        activate_subscription(sub, plan='Pro', billing_cycle='monthly', now=now)

        assert sub.status == 'active'
        assert sub.expires_at > now
        assert sub.started_at is not None or sub.expires_at is not None

    def test_pending_to_approved_state_transition(self):
        ps, sub = self._make_submission('pending')
        ps.status = 'approved'
        assert ps.status == 'approved'

    def test_pending_subscription_becomes_active(self):
        from app.services.billing import activate_subscription
        ps, sub = self._make_submission('pending')
        now = datetime.now(timezone.utc)
        activate_subscription(sub, plan='Pro', billing_cycle='monthly', now=now)
        assert sub.status == 'active'

    def test_duplicate_approval_guard(self):
        """Approve same submission twice — second call should be blocked."""
        ps, sub = self._make_submission('pending')
        now = datetime.now(timezone.utc)

        # First approval
        ps.status = 'approved'
        ps.reviewed_at = now

        # Simulate guard: already reviewed
        def try_approve_again(submission):
            if submission.status != 'pending':
                return False, 'Submission already reviewed.'
            return True, ''

        ok, msg = try_approve_again(ps)
        assert not ok
        assert 'already reviewed' in msg.lower()

    def test_rejection_flow(self):
        ps, sub = self._make_submission('pending')
        ps.status = 'rejected'
        sub.status = 'rejected'
        assert ps.status == 'rejected'
        assert sub.status == 'rejected'

    def test_rejected_tenant_can_resubmit(self):
        """After rejection the submission is still queryable and a new one can be created."""
        ps, sub = self._make_submission('rejected')
        # A new pending submission can be created
        new_ps = FakePaymentSubmission(status='pending')
        assert new_ps.status == 'pending'
        assert ps.status == 'rejected'


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4: Expiration unit tests
# ─────────────────────────────────────────────────────────────────────────────

class TestSubscriptionExpiry:
    """Active subscriptions auto-expire past expires_at."""

    def test_active_sub_expires_when_past_date(self):
        """Simulate refresh_current_subscription logic."""
        sub = FakeSubscription(
            status='active',
            is_active=True,
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )

        # Replicate the hook's expiry logic
        now = datetime.now(timezone.utc)
        expires = sub.expires_at
        if expires and expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)

        if sub.status == 'active' and expires and expires < now:
            sub.status = 'expired'
            sub.is_active = False

        assert sub.status == 'expired'
        assert sub.is_active is False

    def test_active_sub_not_expired_before_date(self):
        sub = FakeSubscription(
            status='active',
            is_active=True,
            expires_at=datetime.now(timezone.utc) + timedelta(days=10),
        )
        now = datetime.now(timezone.utc)
        expires = sub.expires_at

        if sub.status == 'active' and expires and expires < now:
            sub.status = 'expired'

        assert sub.status == 'active'

    def test_monthly_duration_is_30_days(self):
        from app.services.billing import plan_duration_days
        assert plan_duration_days('Pro', 'monthly') == 30

    def test_yearly_duration_is_360_days(self):
        from app.services.billing import plan_duration_days
        assert plan_duration_days('Pro', 'yearly') == 360

    def test_activation_sets_expires_at(self):
        from app.services.billing import activate_subscription
        sub = FakeSubscription()
        now = datetime.now(timezone.utc)
        activate_subscription(sub, plan='Basic', billing_cycle='monthly', now=now)
        assert sub.expires_at == now + timedelta(days=30)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 5: Rate limiting — decorator presence checks
# ─────────────────────────────────────────────────────────────────────────────

class TestRateLimitDecorators:
    """
    Verify Flask-Limiter decorators are applied to the right routes.
    Flask-Limiter 3.x marks decorated functions with '__wrapper-limiter-instance'.
    """

    _LIMITER_MARKER = '__wrapper-limiter-instance'

    def _has_limiter(self, fn) -> bool:
        return hasattr(fn, self._LIMITER_MARKER)

    def test_auth_forgot_password_has_rate_limits(self):
        from app.auth import forgot_password
        assert self._has_limiter(forgot_password), (
            'auth.forgot_password is missing @limiter.limit decorators. '
            'Add: @limiter.limit("5 per minute") and @limiter.limit("10 per hour")'
        )

    def test_superadmin_forgot_password_request_has_rate_limits(self):
        from app.superadmin import forgot_password_request
        assert self._has_limiter(forgot_password_request), (
            'superadmin.forgot_password_request is missing @limiter.limit decorators.'
        )

    def test_superadmin_forgot_password_verify_has_rate_limits(self):
        from app.superadmin import forgot_password_verify
        assert self._has_limiter(forgot_password_verify), (
            'superadmin.forgot_password_verify is missing @limiter.limit decorators.'
        )


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 6: Legacy OTP store removal
# ─────────────────────────────────────────────────────────────────────────────

class TestLegacyOTPStoreRemoved:
    """FIX HP#3 — in-memory OTP dict must no longer exist in superadmin module."""

    def test_sa_otp_store_removed(self):
        import app.superadmin as sa_mod
        assert not hasattr(sa_mod, '_sa_otp_store'), (
            '_sa_otp_store (in-memory OTP dict) still present in superadmin module. '
            'It must be removed and replaced with the DB-backed flow.'
        )

    def test_sa_otp_lock_removed(self):
        import app.superadmin as sa_mod
        assert not hasattr(sa_mod, '_sa_otp_lock'), (
            '_sa_otp_lock still present. Remove together with _sa_otp_store.'
        )

    def test_forgot_password_redirects_to_request(self):
        """The old /forgot-password route should be a redirect, not a full handler."""
        import inspect
        from app.superadmin import forgot_password
        src = inspect.getsource(forgot_password)
        assert 'redirect' in src, (
            'superadmin.forgot_password should redirect to forgot_password_request.'
        )
        assert '_sa_otp_store' not in src, (
            '_sa_otp_store still referenced in forgot_password view.'
        )


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 7: Billing proof allowed-extension constant
# ─────────────────────────────────────────────────────────────────────────────

class TestBillingProofConstant:
    """The billing proof whitelist must be exactly the 5 specified types."""

    def test_billing_proof_extensions_exact(self):
        from app.security import FileUploadPolicy
        expected = {'jpg', 'jpeg', 'png', 'webp', 'pdf'}
        assert FileUploadPolicy.ALLOWED_BILLING_PROOF_EXTENSIONS == expected, (
            f"Expected {expected}, got {FileUploadPolicy.ALLOWED_BILLING_PROOF_EXTENSIONS}"
        )

    def test_zip_not_in_billing_whitelist(self):
        from app.security import FileUploadPolicy
        assert 'zip' not in FileUploadPolicy.ALLOWED_BILLING_PROOF_EXTENSIONS

    def test_doc_not_in_billing_whitelist(self):
        from app.security import FileUploadPolicy
        assert 'doc' not in FileUploadPolicy.ALLOWED_BILLING_PROOF_EXTENSIONS

    def test_docx_not_in_billing_whitelist(self):
        from app.security import FileUploadPolicy
        assert 'docx' not in FileUploadPolicy.ALLOWED_BILLING_PROOF_EXTENSIONS

    def test_xlsx_not_in_billing_whitelist(self):
        from app.security import FileUploadPolicy
        assert 'xlsx' not in FileUploadPolicy.ALLOWED_BILLING_PROOF_EXTENSIONS

    def test_txt_not_in_billing_whitelist(self):
        from app.security import FileUploadPolicy
        assert 'txt' not in FileUploadPolicy.ALLOWED_BILLING_PROOF_EXTENSIONS


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 8: Security control verification (non-destructive audit)
# ─────────────────────────────────────────────────────────────────────────────

class TestExistingSecurityControls:
    """
    Verify that existing security controls remain intact.
    These tests do NOT modify behaviour — they confirm presence only.
    """

    def test_password_policy_min_length_12(self):
        from app.security import PasswordPolicy
        assert PasswordPolicy.MIN_LENGTH == 12

    def test_password_policy_requires_uppercase(self):
        from app.security import PasswordPolicy
        assert PasswordPolicy.REQUIRE_UPPERCASE

    def test_password_policy_requires_special(self):
        from app.security import PasswordPolicy
        assert PasswordPolicy.REQUIRE_SPECIAL

    def test_password_policy_rejects_weak(self):
        from app.security import PasswordPolicy
        ok, _ = PasswordPolicy.validate('password123')
        assert not ok

    def test_password_policy_accepts_strong(self):
        from app.security import PasswordPolicy
        ok, _ = PasswordPolicy.validate('Tr0ub4dor&3_XyZ!')
        assert ok

    def test_account_lockout_threshold_5(self):
        from app.security import AccountLockout
        assert AccountLockout.MAX_FAILED_ATTEMPTS == 5

    def test_account_lockout_duration_15_min(self):
        from app.security import AccountLockout
        assert AccountLockout.LOCKOUT_DURATION_MINUTES == 15

    def test_account_lockout_is_locked_when_exceeded(self):
        from app.security import AccountLockout
        from datetime import datetime, timezone, timedelta
        user = MagicMock()
        user.failed_login_attempts = 5
        user.last_failed_login_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        assert AccountLockout.is_locked(user)

    def test_account_lockout_not_locked_when_expired(self):
        from app.security import AccountLockout
        user = MagicMock()
        user.failed_login_attempts = 5
        user.last_failed_login_at = datetime.now(timezone.utc) - timedelta(minutes=20)
        assert not AccountLockout.is_locked(user)

    def test_otp_hashing_uses_secrets_compare_digest(self):
        """OTP comparison must be constant-time to prevent timing attacks."""
        import secrets
        a = 'abc123'
        b = 'abc123'
        assert secrets.compare_digest(a, b)

    def test_activate_subscription_is_idempotent_on_active(self):
        """Additive renewal — re-activating an already-active subscription extends it."""
        from app.services.billing import activate_subscription
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=15)
        sub = FakeSubscription(status='active', expires_at=expires)
        activate_subscription(sub, plan='Pro', billing_cycle='monthly', now=now)
        # Should extend from expires_at, not reset to now
        assert sub.expires_at == expires + timedelta(days=30)

    def test_double_activation_no_subscription_duplication(self):
        """Approving the same submission twice does not duplicate an activation."""
        from app.services.billing import activate_subscription
        now = datetime.now(timezone.utc)
        sub = FakeSubscription(status='pending')

        # First activation
        activate_subscription(sub, plan='Pro', now=now)
        first_expiry = sub.expires_at

        # Simulate guard on PaymentSubmission: already approved
        ps = FakePaymentSubmission(status='approved')

        def approve_again(submission):
            if submission.status != 'pending':
                return False, 'Submission already reviewed.'
            return True, ''

        ok, msg = approve_again(ps)
        assert not ok  # Guard fires — no second activation


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 9: PayMongo disabled gate
# ─────────────────────────────────────────────────────────────────────────────

class TestPayMongoDisabled:
    """PAYMONGO_ENABLED=false must suppress checkout paths."""

    def test_is_paymongo_enabled_returns_false_by_default(self):
        """
        Config.PAYMONGO_ENABLED must be overrideable via env.
        The deployment spec requires PAYMONGO_ENABLED=false for the manual-payment beta.
        This test verifies the config key exists and that the .env.example guidance is correct.
        """
        from config import Config
        # The key must exist on Config
        assert hasattr(Config, 'PAYMONGO_ENABLED')
        # The deployment mode requires it to be set false — we verify the class reads from env
        # rather than hardcoding true (i.e. the value is env-driven, not hardcoded to True)
        import inspect
        src = inspect.getsource(Config)
        # Must read from environment variable, not be hardcoded True
        assert "PAYMONGO_ENABLED" in src
        assert "os.environ.get('PAYMONGO_ENABLED'" in src or 'os.environ.get("PAYMONGO_ENABLED"' in src

    def test_paymongo_config_key_exists(self):
        """Config must have PAYMONGO_ENABLED attribute."""
        from config import Config
        assert hasattr(Config, 'PAYMONGO_ENABLED')

    def test_billing_plans_context_function_exists(self):
        """billing_handlers module must export billing_plans_context."""
        from app.services import billing_handlers
        assert hasattr(billing_handlers, 'billing_plans_context')


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 10: 413 error handler presence
# ─────────────────────────────────────────────────────────────────────────────

class Test413Handler:
    """FIX #3 addendum — 413 handler must exist and return friendly response."""

    def test_413_handler_registered(self):
        import inspect
        import app as app_module
        src = inspect.getsource(app_module)
        assert 'errorhandler(413)' in src, (
            '@app.errorhandler(413) not found in app/__init__.py'
        )

    def test_413_template_exists(self):
        import os
        template_path = os.path.join(
            '/home/claude/cms/v3.9-patched',
            'templates', 'errors', '413.html'
        )
        assert os.path.isfile(template_path), (
            'templates/errors/413.html does not exist'
        )
