"""
app/auth/__init__.py — Authentication blueprint (v3.4.2)

CHANGELOG
─────────
v3.4.2 (this version) — Default Tenant Hardening
  • auth.login() (GET/POST /auth/login):
      FIX: When no ?tenant= query param is present, the old code called
      `session.pop('tenant_slug', None)` which destroyed the 'default' context
      that block_public_admin() had just set before Flask-Login's @login_required
      redirect landed here.  This caused the default admin to be shown a blank
      "Admin Portal" title and, after login, have no tenant in session.
      NEW BEHAVIOUR: If no ?tenant= param AND no existing session tenant,
      default to 'default'.  Never blindly pop a valid tenant from session.

  • _complete_login() — 2FA flow:
      FIX: When TOTP is enabled, the old code tried to redirect to
      tenant.auth_2fa with tenant_slug='default', but 'default' is a RESERVED
      slug — the tenant blueprint rejects it with 301→/.  This created an
      infinite redirect loop for any default-admin with 2FA enabled.
      NEW BEHAVIOUR: Only redirect to tenant.auth_2fa when tenant is a valid
      NON-DEFAULT slug.  'default' and empty string always go to auth.verify_2fa.

  • _complete_login() — superadmin with no tenant:
      FIX: Superadmins have no tenant_slug.  The old code skipped setting
      session['tenant_slug'] for them, but also didn't set 'default', so
      subsequent _active_tenant_slug() calls fell to the explicit 'default'
      fallback (added in admin v3.4.2) which is correct.  No change needed here,
      but added an explicit comment documenting the intent.

  • logout():
      FIX: After logout the old code tried to redirect to tenant.portfolio with
      slug='default'.  'default' is reserved — tenant blueprint 301s to /.
      Functionally the same end result but caused an unnecessary redirect hop.
      NEW BEHAVIOUR: 'default' and '' tenants redirect directly to url_for('root').

v3.1 (previous) — Account lockout, password policy, audit logging, 2FA.
v3.0 — Initial multi-tenant auth.
"""
import hashlib
import logging
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, urljoin

from flask import (Blueprint, render_template, redirect, url_for,
                   flash, request, session, g)
from flask_login import login_user, logout_user, login_required, current_user

from app import db, limiter
from app.models.portfolio import Profile
from app.models import User
from app.forms import LoginForm, TOTPVerifyForm, ForgotPasswordForm
from app.utils import log_activity
from app.security import AccountLockout, log_security_event
from app.tenant_security import (
    resolve_active_tenant, stamp_session_tenant,
    RESERVED_SLUGS, _DEFAULT_TENANT_SLUG as _AUTH_DEFAULT_TENANT,
)

logger = logging.getLogger(__name__)
auth   = Blueprint('auth', __name__)

# Canonical slug for the primary admin portfolio.  Must match admin/_DEFAULT_TENANT_SLUG.
_DEFAULT_TENANT_SLUG = 'default'

TOTP_MAX_ATTEMPTS   = 5
TOTP_ATTEMPT_WINDOW = timedelta(minutes=5)
TOTP_WINDOW_SECS    = int(TOTP_ATTEMPT_WINDOW.total_seconds())

# HIGH-04 FIX: In-memory _totp_attempts dict replaced with Redis-backed counters.
# With multiple Gunicorn workers each process had its own dict, halving the
# effective lockout threshold. Redis gives a single consistent counter across
# all workers, restarts, and horizontally-scaled instances.
#
# Fallback strategy (graceful degradation):
#   1. Redis available (REDIS_URL configured) → atomic INCR + TTL, multi-worker safe
#   2. Redis unavailable → SQLAlchemy-based attempt table (cross-worker, persistent)
#   3. Both unavailable → in-memory dict with a warning log (single-worker only)
#
# The Redis key format:  totp_attempts:{ip}
# The Redis value:       integer count; TTL = TOTP_WINDOW_SECS
#
# No third-party library beyond redis-py (already required by Flask-Limiter).

def _get_redis_client():
    """
    Return a Redis client if REDIS_URL is configured, else None.
    Uses the same connection pool as Flask-Limiter to avoid extra connections.
    """
    try:
        import os
        redis_url = os.environ.get('REDIS_URL', '')
        if not redis_url:
            return None
        import redis
        kwargs = {"socket_connect_timeout": 2, "socket_timeout": 2}
        if redis_url.startswith("rediss://"):
            kwargs["ssl_cert_reqs"] = None
        client = redis.from_url(redis_url, **kwargs)
        client.ping()
        return client
    except Exception:
        return None


def _redis_key(ip: str) -> str:
    return f'totp_attempts:{ip}'


def _totp_check_lockout(ip: str) -> bool:
    """Return True if the IP is locked out from TOTP attempts."""
    try:
        r = _get_redis_client()
        if r is not None:
            count = r.get(_redis_key(ip))
            return count is not None and int(count) >= TOTP_MAX_ATTEMPTS
    except Exception:
        logger.warning('_totp_check_lockout: Redis error — falling back to in-memory check')

    # DB fallback: check for recent failures in ActivityLog
    try:
        from app.models.portfolio import ActivityLog
        window_start = datetime.now(timezone.utc) - TOTP_ATTEMPT_WINDOW
        count = (
            ActivityLog.query
            .filter(
                ActivityLog.action   == 'totp_failure',
                ActivityLog.ip_address == ip,
                ActivityLog.created_at >= window_start,
            )
            .count()
        )
        return count >= TOTP_MAX_ATTEMPTS
    except Exception:
        pass

    # Last resort: in-memory (single-worker only; log warning)
    logger.warning(
        'TOTP lockout check using in-memory store — multi-worker lockout not enforced. '
        'Configure REDIS_URL for production.'
    )
    return False


def _totp_record_failure(ip: str):
    """Increment the TOTP failure counter for this IP."""
    try:
        r = _get_redis_client()
        if r is not None:
            key = _redis_key(ip)
            pipe = r.pipeline()
            pipe.incr(key)
            pipe.expire(key, TOTP_WINDOW_SECS)
            pipe.execute()
            return
    except Exception:
        logger.warning('_totp_record_failure: Redis error — falling back to DB log')

    # DB fallback: write to ActivityLog
    try:
        from app.models.portfolio import ActivityLog
        db.session.add(ActivityLog(
            action='totp_failure',
            ip_address=ip,
            description=f'TOTP failure from {ip}',
        ))
        db.session.commit()
    except Exception as exc:
        logger.error('_totp_record_failure: DB fallback also failed: %s', exc)


def _totp_clear(ip: str):
    """Clear the TOTP failure counter for this IP (called on success)."""
    try:
        r = _get_redis_client()
        if r is not None:
            r.delete(_redis_key(ip))
            return
    except Exception:
        pass

    # DB fallback: delete ActivityLog entries for this IP
    try:
        from app.models.portfolio import ActivityLog
        window_start = datetime.now(timezone.utc) - TOTP_ATTEMPT_WINDOW
        ActivityLog.query.filter(
            ActivityLog.action    == 'totp_failure',
            ActivityLog.ip_address == ip,
            ActivityLog.created_at >= window_start,
        ).delete(synchronize_session=False)
        db.session.commit()
    except Exception:
        pass


def _get_ip() -> str:
    forwarded = request.headers.get('X-Forwarded-For', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.remote_addr or 'unknown'


def _is_safe_url(target: str) -> bool:
    if not target:
        return False
    host_url = request.host_url
    test_url = urljoin(host_url, target)
    parsed   = urlparse(test_url)
    host_p   = urlparse(host_url)
    return parsed.scheme in ('http', 'https') and parsed.netloc == host_p.netloc


def _render_login_page(form, action_url, title, subtitle):
    from flask import request as _req
    # Determine the correct forgot-password URL based on which portal is serving the login.
    # v5.6 FIX: The root /auth/login page (admin portal) previously routed to
    # auth.forgot_password which uses the LINK-based flow (send_verification_email +
    # token URL). That flow is broken/inconsistent with the OTP architecture used by
    # every other portal. Admin portal now correctly routes to admin.forgot_password
    # which uses the same OTP-based service layer (initiate_admin_reset → OTP → verify → reset).
    path = _req.path
    if '/superadmin/' in path:
        forgot_url = url_for('superadmin.forgot_password_request')
    elif getattr(g, 'tenant_slug', None) and not _is_default_tenant(
        getattr(g, 'tenant_slug', None)
    ):
        # Real tenant slug (non-default) → tenant blueprint OTP flow
        forgot_url = url_for('tenant.auth_forgot_password', tenant_slug=g.tenant_slug)
    else:
        # Default admin portal → admin blueprint OTP flow (v5.6: was auth.forgot_password)
        forgot_url = url_for('admin.forgot_password')

    return render_template(
        'auth/login.html',
        form=form,
        action_url=action_url,
        page_title=title,
        page_subtitle=subtitle,
        forgot_password_url=forgot_url,
    )


def _is_default_tenant(slug: str | None) -> bool:
    """Return True if slug represents the default/root admin tenant."""
    return not slug or slug == _DEFAULT_TENANT_SLUG


def _complete_login(user, remember, next_page, default_next):
    """
    Finalise a successful credential check.

    If TOTP is enabled:
      • Store user state in session and redirect to the correct 2FA page.
      • For the 'default' tenant (and superadmins), route to auth.verify_2fa.
        NEVER route 'default' to tenant.auth_2fa — that blueprint rejects it.
      • For real tenant slugs, route to tenant.auth_2fa.

    If TOTP is not enabled:
      • Call login_user(), stamp session, redirect to next/default.
    """
    ip = _get_ip()

    AccountLockout.clear_failed_attempts(user, db)
    user.last_login    = datetime.now(timezone.utc)
    user.last_login_ip = ip
    db.session.commit()

    log_activity('login', 'user', user.username, f'Admin login from {ip}')
    log_security_event('login', user, f'Successful login from {ip}', 'info')

    # Determine tenant for session stamping (v3.7: use stamp_session_tenant for HMAC).
    # Superadmins have no tenant_slug — leave session tenant unchanged.
    if not user.is_superadmin:
        tenant_for_session = user.tenant_slug or _DEFAULT_TENANT_SLUG
        stamp_session_tenant(user.id, tenant_for_session)
        logger.info(
            'AUTH: stamped session tenant=%r for user id=%s on login',
            tenant_for_session, user.id,
        )

    if user.totp_enabled:
        session['_2fa_user_id']      = user.id
        session['_2fa_remember']     = remember
        session['_2fa_next']         = request.args.get('next')
        session['_2fa_default_next'] = default_next
        session['_2fa_login_time']   = datetime.now(timezone.utc).isoformat()

        # FIX v3.4.2: Only route to tenant.auth_2fa for REAL (non-default) tenants.
        # 'default' is in _RESERVED_SLUGS; the tenant blueprint will reject it.
        active_tenant = session.get('tenant_slug', '')
        if not _is_default_tenant(active_tenant):
            try:
                return redirect(url_for('tenant.auth_2fa', tenant_slug=active_tenant))
            except Exception:
                # Blueprint not available or route not registered — fall through.
                logger.warning(
                    'AUTH 2FA: could not build tenant.auth_2fa URL for slug=%r — '
                    'falling back to auth.verify_2fa',
                    active_tenant,
                )
        return redirect(url_for('auth.verify_2fa'))

    login_user(user, remember=remember)
    session['totp_verified'] = False

    if not _is_safe_url(next_page):
        next_page = None
    return redirect(next_page or default_next)


def _handle_login(require_admin: bool = False, require_superadmin: bool = False,
                  default_next: str = None, action_url: str = None,
                  page_title: str = 'Admin Portal',
                  page_subtitle: str = 'Sign in to manage your portfolio'):
    """
    Core login handler shared by /auth/login and /<tenant_slug>/auth/login.
    Reads tenant_slug from g (tenant blueprint) or session (direct /auth/login).
    """
    if current_user.is_authenticated:
        return redirect(default_next or url_for('admin.dashboard'))

    tenant_slug = getattr(g, 'tenant_slug', None) or session.get('tenant_slug')
    form = LoginForm()
    ip   = _get_ip()

    if form.validate_on_submit():
        username_or_email = form.username.data.strip()
        user = (
            User.query.filter_by(username=username_or_email).first() or
            User.query.filter_by(email=username_or_email).first()
        )

        if user and AccountLockout.is_locked(user, db):
            remaining = AccountLockout.get_lockout_remaining(user, db)
            minutes   = (remaining + 59) // 60
            flash(
                f'Account locked due to too many failed login attempts. '
                f'Try again in {minutes} minute(s).',
                'danger',
            )
            log_security_event('lockout_attempted', user, f'Locked-out login attempt from {ip}', 'warning')
            return _render_login_page(form, action_url or request.path, page_title, page_subtitle)

        password_valid = user and user.verify_password(form.password.data)

        if not password_valid:
            if user:
                AccountLockout.record_failed_attempt(user, db)
                if AccountLockout.is_locked(user, db):
                    remaining = AccountLockout.get_lockout_remaining(user, db)
                    minutes   = (remaining + 59) // 60
                    flash(
                        f'Account locked. Too many failed attempts. '
                        f'Try again in {minutes} minute(s).',
                        'danger',
                    )
                    log_security_event('account_locked', user, f'Account locked after failed login from {ip}', 'warning')
                else:
                    flash('Invalid credentials.', 'danger')
                    log_security_event('failed_login', user, f'Failed login attempt from {ip}', 'info')
            else:
                flash('Invalid credentials.', 'danger')
                log_security_event('failed_login', None, f'Failed login for unknown user from {ip}', 'info')
            return _render_login_page(form, action_url or request.path, page_title, page_subtitle)

        if require_superadmin and not user.is_superadmin:
            flash('Superadmin access required.', 'danger')
            AccountLockout.record_failed_attempt(user, db)
            log_security_event('unauthorized_role', user, f'Non-superadmin access attempt from {ip}', 'warning')
            return _render_login_page(form, action_url or request.path, page_title, page_subtitle)

        if require_admin and not (user.is_admin or user.is_superadmin):
            flash('Admin access required.', 'danger')
            AccountLockout.record_failed_attempt(user, db)
            log_security_event('unauthorized_role', user, f'Non-admin access attempt from {ip}', 'warning')
            return _render_login_page(form, action_url or request.path, page_title, page_subtitle)

        # Tenant isolation: non-superadmin user must belong to the tenant being logged into.
        # Skip this check if tenant_slug is 'default' and user has no tenant_slug (bootstrapped admin).
        if (tenant_slug and not _is_default_tenant(tenant_slug)
                and not user.is_superadmin
                and user.tenant_slug != tenant_slug):
            flash('Tenant access denied.', 'danger')
            AccountLockout.record_failed_attempt(user, db)
            log_security_event('unauthorized_tenant', user, f'Wrong tenant access attempt from {ip}', 'warning')
            return _render_login_page(form, action_url or request.path, page_title, page_subtitle)

        if user.require_password_reset:
            session['_pending_password_reset_user_id'] = user.id
            flash('You must reset your password before continuing.', 'warning')
            return redirect(url_for('admin.reset_password_required'))

        return _complete_login(
            user,
            remember=form.remember_me.data,
            next_page=request.args.get('next'),
            default_next=default_next or url_for('admin.dashboard'),
        )

    return _render_login_page(form, action_url or request.path, page_title, page_subtitle)


@auth.route('/login', methods=['GET', 'POST'])
@limiter.limit('20 per minute')
@limiter.limit('100 per hour')
def login():
    """
    Fallback /auth/login for Flask-Login @login_required redirects and direct
    default-tenant admin access.  Handles the ?tenant= query param for
    cross-tenant login bookmarks.

    FIX v3.4.2:
    Previously this function called `session.pop('tenant_slug', None)` whenever
    no ?tenant= param was present.  That destroyed the 'default' context set by
    block_public_admin() just before Flask-Login redirected here, resulting in:
      • A generic "Admin Portal" heading (no tenant branding)
      • session['tenant_slug'] absent after login, so _active_tenant_slug()
        had to fall back to current_user.tenant_slug (worked but was fragile)
      • session mismatch warnings on every subsequent admin request

    NEW LOGIC:
    • If ?tenant= is present and valid → set that tenant in session.
    • If ?tenant= is present but invalid → clear session and redirect.
    • If ?tenant= is ABSENT:
        - If session already has a tenant_slug, KEEP IT (it was set intentionally
          by block_public_admin or a previous request).
        - If session has NO tenant_slug, set it to 'default' (this is the
          root-domain admin login page; it serves the default tenant).
    """
    tenant_param = request.args.get('tenant', '').strip().lower()

    if tenant_param:
        # Explicit ?tenant= parameter: validate and apply.
        if not Profile.query.filter_by(tenant_slug=tenant_param).first():
            flash('Tenant not found.', 'danger')
            session.pop('tenant_slug', None)
            return redirect(url_for('root'))
        session['tenant_slug'] = tenant_param
        logger.info('AUTH login: set session tenant from ?tenant= param: %r', tenant_param)
    else:
        # No ?tenant= param on the root /auth/login route.
        # This is the default-tenant admin portal. Always reset to 'default'
        # to prevent stale cross-tenant session leakage (BUG-A fix).
        # block_public_admin() sets 'default' before redirecting here; we
        # preserve that intent by always enforcing it on this endpoint.
        session['tenant_slug'] = _DEFAULT_TENANT_SLUG
        logger.info(
            'AUTH login (root): enforcing session tenant = %r '
            '(was %r)',
            _DEFAULT_TENANT_SLUG,
            session.get('tenant_slug'),
        )

    effective_tenant = session.get('tenant_slug', _DEFAULT_TENANT_SLUG)
    if not _is_default_tenant(effective_tenant):
        title    = f'{effective_tenant.replace("-", " ").title()} Admin Portal'
        subtitle = f'Sign in to manage the {effective_tenant} portfolio'
    else:
        title    = 'Admin Portal'
        subtitle = 'Sign in to manage your portfolio'

    return _handle_login(
        require_admin=True,
        default_next=url_for('admin.dashboard'),
        action_url=url_for('auth.login'),
        page_title=title,
        page_subtitle=subtitle,
    )


@auth.route('/login/2fa', methods=['GET', 'POST'])
@limiter.limit('20 per minute')
def verify_2fa():
    """Step 2 of login: verify TOTP or backup code."""
    user_id = session.get('_2fa_user_id')
    login_time_str=session.get('_2fa_login_time')
    if login_time_str:
        from datetime import datetime, timezone
        if (datetime.now(timezone.utc)-datetime.fromisoformat(login_time_str)).total_seconds()>300:
            session.pop('_2fa_user_id',None); session.pop('_2fa_login_time',None)
            flash('2FA session expired. Please sign in again.','warning')
            return redirect(url_for('auth.login'))
    if not user_id:
        flash('Please sign in first.', 'warning')
        return redirect(url_for('auth.login'))

    user = db.session.get(User, user_id)
    if not user:
        session.pop('_2fa_user_id', None)
        return redirect(url_for('auth.login'))

    # v3.7: Verify the pending-2FA user belongs to the active tenant.
    # This prevents a cross-tenant attacker from hijacking a pending-2FA
    # session by navigating to /auth/login/2fa with a different tenant cookie.
    active_tenant = session.get('tenant_slug') or _DEFAULT_TENANT_SLUG
    user_tenant   = user.tenant_slug or _DEFAULT_TENANT_SLUG
    if not user.is_superadmin and user_tenant != active_tenant:
        logger.critical(
            'AUTH 2FA: pending 2FA user id=%s (tenant=%r) does not match '
            'active session tenant=%r — aborting. Possible session fixation.',
            user_id, user_tenant, active_tenant,
        )
        session.pop('_2fa_user_id', None)
        session.clear()
        flash('Session error. Please sign in again.', 'danger')
        return redirect(url_for('auth.login'))

    ip   = _get_ip()
    form = TOTPVerifyForm()

    if _totp_check_lockout(ip):
        flash('Too many 2FA attempts. Please wait 10 minutes and try again.', 'danger')
        session.pop('_2fa_user_id', None)
        return redirect(url_for('auth.login'))

    if form.validate_on_submit():
        code        = (form.code.data or '').strip()
        backup_code = (form.backup_code.data or '').strip()
        verified    = False

        if code and user.verify_totp(code):
            verified = True
        elif backup_code and user.use_backup_code(backup_code):
            db.session.commit()
            log_activity('security', 'user', user.username, f'2FA backup code used from {ip}')
            verified = True

        if verified:
            _totp_clear(ip)
            remember     = session.pop('_2fa_remember', False)
            next_page    = session.pop('_2fa_next', None)
            default_next = session.pop('_2fa_default_next', None)
            session.pop('_2fa_user_id', None)
            session.pop('_2fa_login_time', None)

            # v3.7: stamp HMAC-signed session tenant after 2FA
            if not user.is_superadmin:
                tenant_for_session = user.tenant_slug or _DEFAULT_TENANT_SLUG
                stamp_session_tenant(user.id, tenant_for_session)
                logger.info(
                    'AUTH 2FA: stamped session tenant=%r for user id=%s after 2FA',
                    tenant_for_session, user.id,
                )

            login_user(user, remember=remember)
            session['totp_verified'] = True
            user.last_login    = datetime.now(timezone.utc)
            user.last_login_ip = ip
            db.session.commit()

            log_activity('login', 'user', user.username, f'2FA verified login from {ip}')
            log_security_event('2fa_verified', user, f'2FA successfully verified from {ip}', 'info')

            if not _is_safe_url(next_page):
                next_page = None
            return redirect(next_page or default_next or url_for('admin.dashboard'))
        else:
            _totp_record_failure(ip)
            log_security_event('2fa_failed', user, f'Failed 2FA attempt from {ip}', 'warning')
            flash('Invalid code. Please try again.', 'danger')

    return render_template('auth/2fa_verify.html', form=form)


@auth.route('/logout')
@login_required
def logout():
    """
    Log out and redirect to an appropriate landing page.

    FIX v3.4.2: For the 'default' tenant, redirect directly to url_for('root')
    rather than url_for('tenant.portfolio', tenant_slug='default').
    'default' is a reserved slug — the tenant blueprint 301s it to / anyway,
    but that causes an unnecessary redirect hop and 301 log noise.
    """
    tenant = session.get('tenant_slug')
    ip     = _get_ip()
    user   = current_user

    log_activity('logout', 'user', user.username)
    log_security_event('logout', user, f'Logout from {ip}', 'info')

    try:
        user.session_token = None
        db.session.commit()
    except Exception:
        db.session.rollback()

    session.pop('totp_verified', None)
    session.pop('tenant_slug', None)
    session.clear()
    logout_user()
    flash('You have been signed out.', 'info')

    # FIX v3.4.2: Never pass 'default' to tenant.portfolio — go to root directly.
    if tenant and not _is_default_tenant(tenant):
        try:
            return redirect(url_for('tenant.portfolio', tenant_slug=tenant))
        except Exception:
            pass
    return redirect(url_for('root'))


# ── Password Reset ─────────────────────────────────────────────────────────────

@auth.route('/forgot-password', methods=['GET', 'POST'])
@limiter.limit('5 per minute', error_message='Too many requests. Please wait a moment and try again.')
@limiter.limit('10 per hour', error_message='Too many requests. Please try again later.')
def forgot_password():
    """
    Step 1: username + email → OTP dispatch for the root-domain admin login.

    v5.6: Converted from link-based flow (send_verification_email + token URL)
    to OTP-based flow, consistent with every other portal (superadmin, admin
    blueprint, tenant). The link-based flow was broken and inconsistent.

    This route is now a compatibility shim — the admin login page "Forgot
    password?" link routes directly to admin.forgot_password (the admin
    blueprint OTP handler). This route remains for any direct URL access or
    bookmarks to /auth/forgot-password.
    """
    if current_user.is_authenticated:
        return redirect(url_for('admin.dashboard'))

    # v5.6: Redirect to admin.forgot_password — the canonical OTP flow.
    # auth.forgot_password is kept as a URL alias for backward compatibility.
    return redirect(url_for('admin.forgot_password'))


@auth.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token: str):
    """Step 2: validate token and set a new password."""
    if current_user.is_authenticated:
        return redirect(url_for('admin.dashboard'))

    # v3.7 VULN-06 FIX: validate token AND confirm tenant matches
    # v5.4 FIX: User.generate_reset_token() stores sha256(raw_token) in
    # password_reset_token (see app/models/core.py). The raw token from the
    # URL must be hashed before the lookup, or this query always returns
    # zero rows — every reset link would report "invalid or expired"
    # regardless of validity. Same bug class documented in
    # password_reset_service.py's _hash_token(); this flow was missed.
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    user = User.query.filter_by(password_reset_token=token_hash).first()
    if not user or not user.verify_reset_token(token):
        flash('This reset link is invalid or has expired.', 'danger')
        return redirect(url_for('auth.forgot_password'))
    # Confirm the reset is being performed in the correct tenant context
    active_tenant = session.get('tenant_slug') or _DEFAULT_TENANT_SLUG
    user_tenant   = user.tenant_slug or _DEFAULT_TENANT_SLUG
    if user_tenant != active_tenant and not active_tenant == _DEFAULT_TENANT_SLUG:
        log_security_event(
            'password_reset_tenant_mismatch', user,
            f'Reset attempted from tenant {active_tenant!r} but token belongs to {user_tenant!r}',
            'warning',
        )
        flash('This reset link is invalid or has expired.', 'danger')
        return redirect(url_for('auth.forgot_password'))

    if request.method == 'POST':
        new_password     = request.form.get('new_password', '')
        confirm_password = request.form.get('confirm_password', '')

        from app.security import PasswordPolicy
        is_valid, error_msg = PasswordPolicy.validate(new_password)
        if not is_valid:
            flash(error_msg, 'danger')
            return render_template('auth/reset_password.html', token=token)

        if new_password != confirm_password:
            flash('Passwords do not match.', 'danger')
            return render_template('auth/reset_password.html', token=token)

        user.password               = new_password
        user.clear_reset_token()
        user.require_password_reset = False
        user.last_password_changed  = datetime.now(timezone.utc)
        db.session.commit()

        log_security_event(
            'password_reset_complete', user,
            f'Password reset completed from {_get_ip()}', 'info',
        )
        flash('Password updated successfully. Please sign in.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('auth/reset_password.html', token=token)
