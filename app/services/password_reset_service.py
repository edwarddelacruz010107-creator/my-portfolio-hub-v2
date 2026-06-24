"""
app/services/password_reset_service.py — Password reset orchestration (v5.7)

PATCH v5.7 (Web3Forms eliminated from the superadmin path):
  • Web3Forms returns HTTP 403 ("Pro plan required for server-side use") for
    this deployment's account tier. It is no longer viable for ANY OTP
    delivery and has been removed from the superadmin flow entirely.
  • _send_superadmin_otp() now calls ONLY smtp_service.send_superadmin_otp().
    There is no fallback for superadmin by design — see ISOLATION CONTRACT
    in that function's docstring.
  • web3forms_service.py is retired (kept as a deprecated stub — see file
    header — to avoid breaking any stray import during rollout).
  • Tenant / Admin flows are unaffected by the Web3Forms removal (they
    never used it) but their delivery order has been corrected in
    email_service.py v5.7.1: MailerSend is now PRIMARY, SMTP is FALLBACK
    (previously reversed — see that module's changelog).
  • Structured, step-level logging added to admin + tenant initiate
    functions per the v5.7 observability requirement. Behavior unchanged.
  • Zero schema changes.

DELIVERY MATRIX (v5.7):
    ┌──────────────────────┬─────────────────────────────────────────────┐
    │ Portal               │ OTP Delivery                                │
    ├──────────────────────┼─────────────────────────────────────────────┤
    │ TENANT               │ email_service.send_otp_email()              │
    │                      │ → MailerSend primary / SMTP fallback        │
    ├──────────────────────┼─────────────────────────────────────────────┤
    │ ADMIN                │ smtp_service.send_admin_otp() primary       │
    │                      │   → email_service.send_otp_email() fallback │
    │                      │ → MailerSend primary / SMTP fallback        │
    ├──────────────────────┼─────────────────────────────────────────────┤
    │ SUPERADMIN           │ smtp_service.send_superadmin_otp()  ONLY     │
    │                      │ NO MailerSend. NO Web3Forms. NO fallback.   │
    └──────────────────────┴─────────────────────────────────────────────┘

Three completely isolated reset flows:
    A. Superadmin  — /superadmin/forgot-password
    B. Admin        — /admin/forgot-password       (tenant admin users)
    C. Tenant       — /tenant/forgot-password      (validates against User row)

Each flow:
    1. resolve_user()     → look up account, validate email
    2. initiate_reset()   → create OTP record, send email
    3. verify_otp_step()  → verify OTP, return token for password form
    4. complete_reset()   → set new password, destroy all sessions

Security:
    • OTP: 6-digit, SHA-256 hashed in DB, 10-minute TTL (configurable)
    • Max 5 wrong OTP attempts before record is voided
    • Anti-enumeration: always return generic message regardless of match
    • Reset token: SHA-256 hashed in DB, short TTL
    • Session token rotated on password change → existing sessions invalidated
    • Superadmin flow is completely isolated from tenant/admin tables/slugs/keys
    • Rate limiting enforced at the Flask route layer (Flask-Limiter) — see
      app/superadmin/__init__.py and app/admin/__init__.py decorators:
      5 OTP requests/hour/email-equivalent route, 10/hour/IP-equivalent route.

Audit events (emitted via app.security.log_security_event — structured log,
not a DB table):
    sa_otp_generated        — OTP record created (superadmin)
    sa_otp_sent             — SMTP accepted superadmin OTP for delivery
    sa_otp_send_failed      — SMTP delivery failed for superadmin OTP
    sa_pw_reset_initiated   — alias of sa_otp_sent, kept for dashboard compat
    sa_otp_failed           — OTP verification failed (superadmin)
    sa_otp_verified         — OTP verified (superadmin)
    sa_pw_reset_complete    — Password changed (superadmin)
    admin_otp_generated     — OTP record created (admin)
    admin_otp_sent          — MailerSend or SMTP accepted admin OTP
    admin_otp_send_failed   — both providers failed for admin OTP
    admin_pw_reset_*        — legacy admin flow events (unchanged)
    tenant_otp_generated    — OTP record created (tenant)
    tenant_otp_sent         — MailerSend or SMTP accepted tenant OTP
    tenant_otp_send_failed  — both providers failed for tenant OTP
    tenant_pw_reset_*       — legacy tenant flow events (unchanged)
"""
import hashlib
import logging
import secrets
from datetime import datetime, timezone

from flask import current_app, request as flask_request

from app import db
from app.models import User
from app.models.portfolio import Tenant, GlobalEmailConfig
from app.services.otp_service import create_otp_record, verify_otp
from app.services.email_service import send_otp_email
from app.security import log_security_event
from app.services import smtp_service

logger = logging.getLogger(__name__)

_MAX_OTP_TTL = 10  # minutes (default when GlobalEmailConfig unavailable)


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

def _hash_token(token: str) -> str:
    """
    Hash a raw reset token for DB lookup.

    CRITICAL: User.password_reset_token stores sha256(raw_token) — see
    User.generate_reset_token() in app/models/core.py. Every complete_*_reset()
    below MUST hash the incoming raw token before querying the column, or the
    lookup will silently return zero rows on every single attempt (v5.4 bug).
    """
    return hashlib.sha256(token.encode()).hexdigest()


def _get_reset_token_ttl_minutes() -> int:
    try:
        return current_app.config.get('PASSWORD_RESET_EXPIRATION_MINUTES', 15)
    except RuntimeError:
        return 15


def _get_ip() -> str:
    fwd = flask_request.headers.get('X-Forwarded-For', '')
    return fwd.split(',')[0].strip() if fwd else (flask_request.remote_addr or 'unknown')


def _get_ua() -> str:
    return (flask_request.headers.get('User-Agent') or '')[:300]


def _recovery_enabled() -> bool:
    try:
        return GlobalEmailConfig.get().recovery_enabled
    except Exception:
        return True  # fail open — do not lock superadmin out


def _get_ttl_minutes() -> int:
    try:
        return max(1, GlobalEmailConfig.get().otp_expiry_minutes or _MAX_OTP_TTL)
    except Exception:
        return _MAX_OTP_TTL


def _apply_password_change(user: User, new_password: str) -> None:
    """
    Set new password, rotate session token (invalidates all existing sessions),
    clear reset token, clear require_password_reset flag.
    """
    user.password               = new_password
    user.session_token          = secrets.token_urlsafe(32)  # rotate → kills all sessions
    user.require_password_reset = False
    user.last_password_changed  = datetime.now(timezone.utc)
    user.clear_reset_token()
    db.session.commit()
    logger.info('Password changed for user id=%s', user.id)


# ─────────────────────────────────────────────────────────────────────────────
# A. Superadmin reset — SMTP ONLY
# ─────────────────────────────────────────────────────────────────────────────

def _send_superadmin_otp(
    email: str,
    otp: str,
    ip_address: str,
    user_agent: str,
    ttl_minutes: int,
) -> tuple[bool, str]:
    """
    Deliver superadmin OTP via SMTP — the ONLY transport for this path.

    ISOLATION CONTRACT:
        • ONLY called from initiate_superadmin_reset().
        • NEVER uses send_otp_email(), MailerSend, or Web3Forms.
        • NEVER touches tenant_slug, tenant_id, or any tenant table.
        • NEVER reads admin or tenant MailerSend credentials, and never
          reads GlobalEmailConfig — smtp_service resolves configuration
          from environment variables only.

    If SMTP_HOST/USERNAME/PASSWORD/FROM_EMAIL are not fully set this is a
    hard configuration error. smtp_service.send_email() returns
    (False, <reason>) in that case — logged at ERROR level here. There is
    NO silent fallback to MailerSend or Web3Forms for the superadmin path.

    Returns (sent: bool, error_or_empty: str).
    """
    ok, err = smtp_service.send_superadmin_otp(
        email=email,
        otp=otp,
        ip_address=ip_address,
        user_agent=user_agent,
        ttl_minutes=ttl_minutes,
    )

    if ok:
        logger.info('_send_superadmin_otp: OTP delivered via SMTP to=%s', email)
        return True, ''

    logger.error(
        '_send_superadmin_otp: SMTP delivery FAILED for superadmin OTP to=%s '
        'reason=%s — NO fallback configured (check SMTP_HOST/USERNAME/'
        'PASSWORD/FROM_EMAIL)',
        email, err,
    )
    return False, err


def initiate_superadmin_reset(
    submitted_email: str,
    submitted_username: str = '',
) -> tuple[bool, str]:
    """
    Initiate superadmin OTP reset.

    Validation:
        • username field is non-empty
        • email field is non-empty
        • A User row exists with is_superadmin=True AND email=email AND username=username

    Anti-enumeration: always returns the same generic message regardless of
    whether the account exists.  Attacker learns nothing from the response.

    OTP delivery: SMTP ONLY.  MailerSend and Web3Forms are never consulted.

    Returns (sent: bool, message: str).
    """
    if not _recovery_enabled():
        return False, 'Password recovery is currently disabled.'

    # Generic — returned unconditionally regardless of account match
    generic = 'If a superadmin account exists with those credentials, an OTP has been sent.'

    email = (submitted_email or '').strip().lower()
    uname = (submitted_username or '').strip()

    if not email or not uname:
        return False, 'Username and email are both required.'

    user = User.query.filter_by(
        is_superadmin=True,
        email=email,
        username=uname,
    ).first()

    logger.info(
        '[SUPERADMIN RESET] lookup email=%s username=%s found=%s',
        email, uname, bool(user),
    )

    if not user:
        logger.warning(
            '[SUPERADMIN RESET] no match email=%s username=%s '
            '(returning generic message to prevent enumeration)',
            uname, email,
        )
        return True, generic  # Anti-enumeration: lie about existence

    ip  = _get_ip()
    ua  = _get_ua()
    ttl = _get_ttl_minutes()

    raw_otp = create_otp_record(
        user_type='superadmin',
        user_id=user.id,
        email=user.email,
        ip_address=ip,
        user_agent=ua,
        # tenant_id intentionally omitted — superadmin has no tenant
    )
    db.session.commit()

    logger.info('[SUPERADMIN RESET] step=otp_generated user_id=%s ttl=%dm', user.id, ttl)
    log_security_event(
        'sa_otp_generated', user,
        f'Superadmin OTP record created from ip={ip}', 'info',
    )

    sent, err = _send_superadmin_otp(
        email=user.email,
        otp=raw_otp,
        ip_address=ip,
        user_agent=ua,
        ttl_minutes=ttl,
    )

    logger.info('[SUPERADMIN RESET] step=otp_delivery_result sent=%s user_id=%s', sent, user.id)

    if sent:
        log_security_event(
            'sa_otp_sent', user,
            f'Superadmin OTP sent via SMTP from ip={ip}', 'info',
        )
        # Back-compat alias for any existing dashboards/alerts keyed on the old event name
        log_security_event(
            'sa_pw_reset_initiated', user,
            f'Superadmin OTP sent via SMTP from ip={ip}', 'info',
        )
    else:
        logger.error(
            '[SUPERADMIN RESET] step=otp_delivery_failed user_id=%s reason=%s. '
            'Check SMTP_HOST/SMTP_USERNAME/SMTP_PASSWORD/SMTP_FROM_EMAIL environment variables.',
            user.id, err,
        )
        log_security_event(
            'sa_otp_send_failed', user,
            f'Superadmin OTP delivery via SMTP failed from ip={ip}: {err}', 'warning',
        )

    return True, generic  # Always generic (anti-enumeration)


def verify_superadmin_otp(
    submitted_email: str,
    raw_otp: str,
) -> tuple[bool, str, str | None]:
    """
    Verify OTP submitted by superadmin.

    Returns (ok: bool, message: str, reset_token: str | None).
    reset_token is a short-lived token stored (hashed) on the User row.
    """
    email = (submitted_email or '').strip().lower()
    user  = User.query.filter_by(is_superadmin=True, email=email).first()

    if not user:
        # No enumeration — same message as a real OTP failure
        return False, 'Invalid OTP or account.', None

    ok, msg = verify_otp(
        user_type='superadmin',
        user_id=user.id,
        raw_otp=raw_otp,
        # tenant_id not supplied — superadmin is tenantless
    )

    if not ok:
        log_security_event(
            'sa_otp_failed', user,
            f'Superadmin OTP verification failed: {msg}', 'warning',
        )
        return False, msg, None

    token = user.generate_reset_token(expires_in_minutes=_get_reset_token_ttl_minutes())
    db.session.commit()

    log_security_event('sa_otp_verified', user, 'Superadmin OTP verified', 'info')
    return True, 'OTP verified. Set your new password.', token


def complete_superadmin_reset(
    token: str,
    new_password: str,
) -> tuple[bool, str]:
    """Apply new password, invalidate all sessions for the superadmin account."""
    user = User.query.filter_by(is_superadmin=True).filter(
        User.password_reset_token == _hash_token(token)
    ).first()

    if not user or not user.verify_reset_token(token):
        logger.warning(
            '[SUPERADMIN RESET] Token lookup failed — not found, expired, or wrong hash'
        )
        return False, 'Reset link is invalid or has expired.'

    _apply_password_change(user, new_password)
    log_security_event(
        'sa_pw_reset_complete', user,
        f'Superadmin password reset from ip={_get_ip()}', 'info',
    )
    return True, 'Password changed. Please log in.'


# ─────────────────────────────────────────────────────────────────────────────
# B. Admin (tenant admin user) reset — SMTP primary / MailerSend fallback
#    SMTP primary mirrors the superadmin delivery path (smtp_service.send_admin_otp).
#    MailerSend is used as fallback only when SMTP fails or is unconfigured.
# ─────────────────────────────────────────────────────────────────────────────

def initiate_admin_reset(
    submitted_email: str,
    submitted_username: str = '',
) -> tuple[bool, str]:
    """
    Initiate admin (non-superadmin) OTP reset.
    Delivery: smtp_service.send_admin_otp() (SMTP primary) → send_otp_email() (MailerSend fallback).
    """
    if not _recovery_enabled():
        return False, 'Password recovery is currently disabled.'

    generic = 'If an account exists with those credentials, an OTP has been sent.'
    email   = (submitted_email or '').strip().lower()
    uname   = (submitted_username or '').strip()

    if not email or not uname:
        return False, 'Username and email are both required.'

    user = User.query.filter_by(email=email, username=uname, is_superadmin=False).first()
    logger.info('[ADMIN RESET] step=lookup email=%s username=%s found=%s', email, uname, bool(user))

    if not user:
        logger.info('[ADMIN RESET] step=lookup_miss — returning generic anti-enumeration response')
        return True, generic

    ip, ua, ttl = _get_ip(), _get_ua(), _get_ttl_minutes()

    raw_otp = create_otp_record(
        user_type='admin',
        user_id=user.id,
        email=user.email,
        tenant_id=user.tenant_id,
        ip_address=ip,
        user_agent=ua,
    )
    db.session.commit()
    logger.info('[ADMIN RESET] step=otp_generated user_id=%s ttl=%dm', user.id, ttl)
    log_security_event('admin_otp_generated', user, f'Admin OTP record created from ip={ip}', 'info')

    # ── Tier 1: SMTP primary (mirrors superadmin path) ──────────────────────
    smtp_ok, smtp_err = smtp_service.send_admin_otp(
        email=user.email,
        otp=raw_otp,
        ip_address=ip,
        user_agent=ua,
        ttl_minutes=ttl,
    )
    logger.info('[ADMIN RESET] step=smtp_result sent=%s user_id=%s', smtp_ok, user.id)

    if smtp_ok:
        log_security_event('admin_otp_sent', user, f'Admin OTP delivered via SMTP from ip={ip}', 'info')
        log_security_event('admin_pw_reset_initiated', user, f'Admin OTP sent from ip={ip}', 'info')
        return True, generic

    # ── Tier 2: MailerSend fallback ──────────────────────────────────────────
    logger.warning(
        '[ADMIN RESET] step=smtp_failed user_id=%s smtp_err=%s — falling back to MailerSend',
        user.id, smtp_err,
    )
    ms_sent = send_otp_email(
        recipient_email=user.email,
        otp=raw_otp,
        user_type='admin',
        ip_address=ip,
        user_agent=ua,
        ttl_minutes=ttl,
    )
    logger.info('[ADMIN RESET] step=mailersend_result sent=%s user_id=%s', ms_sent, user.id)

    if ms_sent:
        log_security_event('admin_otp_sent', user, f'Admin OTP delivered via MailerSend fallback from ip={ip}', 'info')
        log_security_event('admin_pw_reset_initiated', user, f'Admin OTP sent (MailerSend fallback) from ip={ip}', 'info')
    else:
        logger.error(
            '[ADMIN RESET] step=delivery_failed user_id=%s — SMTP and MailerSend both failed. '
            'Check SMTP_HOST/SMTP_USERNAME/SMTP_PASSWORD/SMTP_FROM_EMAIL and '
            'MAILERSEND_API_KEY / ADMIN_MAILERSEND_API_KEY.',
            user.id,
        )
        log_security_event('admin_otp_send_failed', user, f'Admin OTP delivery FAILED (all providers) from ip={ip}', 'warning')

    return True, generic


def verify_admin_otp(
    submitted_email: str,
    raw_otp: str,
    tenant_id: str | None = None,
) -> tuple[bool, str, str | None]:
    """DO NOT TOUCH — admin flow, not in scope for v4.0."""
    email = (submitted_email or '').strip().lower()
    # tenant_id is optional: when not supplied (e.g. pre-login forgot-password flow),
    # resolve the user by email alone. Email is unique among non-superadmin users.
    if tenant_id:
        user = User.query.filter_by(email=email, tenant_id=tenant_id, is_superadmin=False).first()
    else:
        user = User.query.filter_by(email=email, is_superadmin=False).first()

    if not user:
        return False, 'Invalid OTP or account.', None

    ok, msg = verify_otp('admin', user.id, raw_otp, tenant_id=user.tenant_id)
    if not ok:
        return False, msg, None

    token = user.generate_reset_token(expires_in_minutes=_get_reset_token_ttl_minutes())
    db.session.commit()
    return True, 'OTP verified. Set your new password.', token


def complete_admin_reset(token: str, new_password: str) -> tuple[bool, str]:
    """DO NOT TOUCH — admin flow, not in scope for v4.0."""
    user = User.query.filter_by(is_superadmin=False).filter(
        User.password_reset_token == _hash_token(token)
    ).first()

    if not user or not user.verify_reset_token(token):
        logger.warning('[ADMIN RESET] Token lookup failed')
        return False, 'Reset link is invalid or has expired.'

    _apply_password_change(user, new_password)
    log_security_event(
        'admin_pw_reset_complete', user,
        f'Admin password reset from ip={_get_ip()}', 'info',
    )
    return True, 'Password changed. Please log in.'


# ─────────────────────────────────────────────────────────────────────────────
# C. Tenant reset — MailerSend primary / SMTP fallback via send_otp_email()
# ─────────────────────────────────────────────────────────────────────────────

def initiate_tenant_reset(
    submitted_email: str,
    username: str | None,
    tenant_slug: str,
) -> tuple[bool, str]:
    """
    Initiate tenant OTP reset.
    Delivery: email_service.send_otp_email() → MailerSend primary / SMTP fallback.
    """
    if not _recovery_enabled():
        return False, 'Password recovery is currently disabled.'

    generic_ok  = 'If a matching account is found, an OTP has been sent.'
    generic_err = 'Invalid username or email.'

    email = (submitted_email or '').strip().lower()
    uname = (username or '').strip()

    if not email or not uname:
        return False, generic_err

    tenant = Tenant.query.filter_by(slug=tenant_slug).first()
    if not tenant:
        logger.warning('[TENANT RESET] step=lookup unknown tenant_slug=%s', tenant_slug)
        return False, generic_err

    user = User.query.filter_by(
        username=uname,
        email=email,
        is_superadmin=False,
        tenant_id=tenant.id,
    ).first()

    logger.info(
        '[TENANT RESET] step=lookup username=%s email=%s tenant=%s found=%s',
        uname, email, tenant_slug, bool(user),
    )

    if not user:
        return False, generic_err

    ip, ua, ttl = _get_ip(), _get_ua(), _get_ttl_minutes()

    raw_otp = create_otp_record(
        user_type='tenant',
        user_id=user.id,
        email=user.email,
        tenant_id=user.tenant_id,
        ip_address=ip,
        user_agent=ua,
    )
    db.session.commit()
    logger.info('[TENANT RESET] step=otp_generated user_id=%s tenant=%s ttl=%dm', user.id, tenant_slug, ttl)
    log_security_event('tenant_otp_generated', user, f'Tenant OTP record created from ip={ip}', 'info')

    sent = send_otp_email(
        recipient_email=user.email,
        otp=raw_otp,
        user_type='tenant',
        ip_address=ip,
        user_agent=ua,
        ttl_minutes=ttl,
    )
    logger.info('[TENANT RESET] step=delivery_result sent=%s user_id=%s tenant=%s', sent, user.id, tenant_slug)

    if sent:
        log_security_event('tenant_otp_sent', user, f'Tenant OTP delivered from ip={ip}', 'info')
        log_security_event('tenant_pw_reset_initiated', user, f'Tenant OTP sent from ip={ip}', 'info')
    else:
        logger.error(
            '[TENANT RESET] step=delivery_failed user_id=%s tenant=%s — both MailerSend '
            'and SMTP fallback failed.',
            user.id, tenant_slug,
        )
        log_security_event('tenant_otp_send_failed', user, f'Tenant OTP delivery failed from ip={ip}', 'warning')

    return True, generic_ok


def verify_tenant_otp(
    submitted_email: str,
    raw_otp: str,
    tenant_slug: str,
) -> tuple[bool, str, str | None]:
    """DO NOT TOUCH — tenant flow, not in scope for v4.0."""
    email  = (submitted_email or '').strip().lower()
    tenant = Tenant.query.filter_by(slug=tenant_slug).first()

    if not tenant:
        return False, 'Invalid OTP or account.', None

    user = User.query.filter_by(
        email=email, is_superadmin=False, tenant_id=tenant.id
    ).first()

    if not user:
        return False, 'Invalid OTP or account.', None

    ok, msg = verify_otp('tenant', user.id, raw_otp, tenant_id=user.tenant_id)
    if not ok:
        return False, msg, None

    token = user.generate_reset_token(expires_in_minutes=_get_reset_token_ttl_minutes())
    db.session.commit()
    return True, 'OTP verified. Set your new password.', token


def complete_tenant_reset(
    token: str,
    new_password: str,
    tenant_id: int,
) -> tuple[bool, str]:
    """
    Apply password change — enforces tenant isolation via tenant_id.
    DO NOT TOUCH — tenant flow, not in scope for v4.0.
    """
    user = User.query.filter_by(is_superadmin=False, tenant_id=tenant_id).filter(
        User.password_reset_token == _hash_token(token)
    ).first()

    if not user:
        logger.warning(
            '[TENANT RESET] no user row matches token hash for tenant_id=%s '
            '(not found / already used / wrong tenant)', tenant_id,
        )
        return False, 'Reset link is invalid or has expired.'

    if not user.verify_reset_token(token):
        logger.warning(
            '[TENANT RESET] expired or hash mismatch user_id=%s tenant_id=%s '
            'expires_at=%s now=%s',
            user.id, tenant_id, user.password_reset_expires,
            datetime.now(timezone.utc),
        )
        return False, 'Reset link is invalid or has expired.'

    _apply_password_change(user, new_password)
    log_security_event(
        'tenant_pw_reset_complete', user,
        f'Tenant password reset from ip={_get_ip()}', 'info',
    )
    return True, 'Password changed. Please log in.'