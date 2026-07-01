"""
app/auth/oauth.py — Google Sign-In (v1.0)

SCOPE — read before touching this file:
  This is a SECOND LOGIN METHOD for users who ALREADY have a User row,
  provisioned the normal way (SuperAdmin → Create Tenant). It is NOT a
  signup system.

  Hard guarantees enforced below:
    1. NEVER creates a Tenant, Profile, Subscription, or User row.
       A Google login only succeeds if User.query.filter_by(email=...)
       already returns a row.
    2. Reuses _authorize_and_login() from app.auth — the SAME role/tenant/
       forced-reset checks the password flow uses. No second copy of that
       logic exists to silently drift out of sync.
    3. Superadmin accounts are hard-blocked from this path, even if
       somehow reached. Superadmin stays password + TOTP only (see
       _render_login_page's allow_google gating for the UI-level block —
       this is the server-side belt-and-suspenders).
    4. Google's email_verified claim is required. An unverified Google
       email can never authenticate.
    5. Account lockout (AccountLockout) is checked before any session is
       established — Google Sign-In does not bypass brute-force/lockout
       protection.
    6. First-time link only sets auth_provider='both' — the existing
       password_hash is never touched, so password login keeps working
       for that user.

Importing this module (see bottom of app/auth/__init__.py) registers
@auth.route(...) google_login / google_callback on the shared `auth`
Blueprint — same pattern already used by app/admin/routes/__init__.py
and app/superadmin/routes/__init__.py for their route submodules.
"""
import logging

from flask import redirect, url_for, flash, request, session, current_app
from flask_login import current_user

from app import db, limiter
from app.models import User
from app.security import AccountLockout, log_security_event
from app.auth import (
    auth, _authorize_and_login, _get_ip, _is_safe_url, _DEFAULT_TENANT_SLUG,
)

logger = logging.getLogger(__name__)


def _oauth_client():
    """Return the registered Authlib google client, or None if disabled."""
    if not current_app.config.get('GOOGLE_OAUTH_ENABLED'):
        return None
    from app.extensions import oauth
    return getattr(oauth, 'google', None)


@auth.route('/google/login')
@limiter.limit('20 per minute')
@limiter.limit('100 per hour')
def google_login():
    """
    Entry point for 'Continue with Google'. Only ever linked from the
    default-tenant and tenant-admin login pages (see _render_login_page) —
    never from the superadmin portal.
    """
    if current_user.is_authenticated:
        return redirect(url_for('admin.dashboard'))

    client = _oauth_client()
    if client is None:
        flash('Google Sign-In is not available right now.', 'danger')
        return redirect(url_for('auth.login'))

    next_page = request.args.get('next')
    if next_page and _is_safe_url(next_page):
        session['_oauth_next'] = next_page
    else:
        session.pop('_oauth_next', None)

    # Preserve whatever tenant context was already in session (set by
    # auth.login / tenant.auth_login / tenant.admin_login before the
    # Google button was rendered) so the callback authorizes against the
    # correct tenant, exactly like the password flow does.
    redirect_uri = url_for('auth.google_callback', _external=True)
    return client.authorize_redirect(redirect_uri)


@auth.route('/google/callback')
@limiter.limit('20 per minute')
@limiter.limit('100 per hour')
def google_callback():
    ip = _get_ip()
    client = _oauth_client()
    if client is None:
        flash('Google Sign-In is not available right now.', 'danger')
        return redirect(url_for('auth.login'))

    try:
        token = client.authorize_access_token()
        userinfo = token.get('userinfo') or client.userinfo(token=token)
        email = (userinfo or {}).get('email', '').strip().lower()
        if not userinfo or not email:
            raise ValueError('no usable userinfo from Google')
        if not userinfo.get('email_verified'):
            flash('Your Google account email is not verified. Please sign in with your password instead.', 'danger')
            return redirect(url_for('auth.login'))

        google_sub = userinfo.get('sub')
        user = User.query.filter_by(google_id=google_sub).first() if google_sub else None
        if user is None:
            user = User.query.filter_by(email=email).first()
        # ... rest of existing logic unchanged ...

    except Exception as exc:
        db.session.rollback()          # ← critical: prevents the aborted-transaction cascade into error-page rendering
        logger.exception('Google OAuth callback failed from %s: %s', ip, exc)
        log_security_event('oauth_failed', None, f'Google callback error from {ip}: {exc}', 'warning')
        flash('Google Sign-In failed. Please try again, or use your password.', 'danger')
        return redirect(url_for('auth.login'))

    userinfo = token.get('userinfo')
    if not userinfo:
        try:
            userinfo = client.userinfo(token=token)
        except Exception as exc:
            logger.warning('Google OAuth: userinfo fetch failed from %s: %s', ip, exc)
            userinfo = None

    email = (userinfo or {}).get('email', '').strip().lower()
    if not userinfo or not email:
        log_security_event('oauth_failed', None, f'Google callback with no usable userinfo from {ip}', 'warning')
        flash('Could not verify your Google account. Please try again.', 'danger')
        return redirect(url_for('auth.login'))

    if not userinfo.get('email_verified'):
        log_security_event('oauth_unverified_email', None, f'Google login rejected (unverified email {email}) from {ip}', 'warning')
        flash('Your Google account email is not verified. Please sign in with your password instead.', 'danger')
        return redirect(url_for('auth.login'))

    google_sub = userinfo.get('sub')

    # Look up by google_id first (already linked), fall back to email
    # (first-time Google sign-in for an existing password-auth user).
    user = User.query.filter_by(google_id=google_sub).first() if google_sub else None
    if user is None:
        user = User.query.filter_by(email=email).first()

    if user is None:
        # HARD RULE: never auto-provision. Accounts are created by SuperAdmin only.
        log_security_event('oauth_no_account', None, f'Google login attempted for unknown email {email} from {ip}', 'info')
        flash(
            'No account found for that Google email. Ask your administrator to '
            'create your account first — you can link Google afterward.',
            'danger',
        )
        return redirect(url_for('auth.login'))

    # Identity-mismatch guard: this email's User row is already linked to a
    # DIFFERENT google_id than the one that just authenticated. Reject rather
    # than silently re-linking — that would let a second Google account
    # hijack access to an existing tenant admin's User row.
    if user.google_id and google_sub and user.google_id != google_sub:
        log_security_event('oauth_identity_mismatch', user, f'Google sub mismatch for {email} from {ip}', 'warning')
        flash('This account is already linked to a different Google identity. Please use your password.', 'danger')
        return redirect(url_for('auth.login'))

    # Superadmin accounts NEVER authenticate via Google, full stop.
    if user.is_superadmin:
        log_security_event('oauth_superadmin_blocked', user, f'Google login rejected for superadmin account from {ip}', 'warning')
        flash('Superadmin accounts must sign in with a password.', 'danger')
        return redirect(url_for('auth.login'))

    if AccountLockout.is_locked(user, db):
        remaining = AccountLockout.get_lockout_remaining(user, db)
        minutes = (remaining + 59) // 60
        flash(
            f'Account locked due to too many failed login attempts. '
            f'Try again in {minutes} minute(s).',
            'danger',
        )
        log_security_event('lockout_attempted', user, f'Locked-out Google login attempt from {ip}', 'warning')
        return redirect(url_for('auth.login'))

    # First-time link: attach the Google identity WITHOUT touching password_hash.
    # Password login keeps working for this user going forward.
    if not user.google_id:
        user.google_id = google_sub
        user.auth_provider = 'both'
        user.avatar_url = userinfo.get('picture') or user.avatar_url
        db.session.commit()
        log_security_event('oauth_linked', user, f'Google account linked from {ip}', 'info')

    tenant_slug = session.get('tenant_slug') or user.tenant_slug or _DEFAULT_TENANT_SLUG
    next_page = session.pop('_oauth_next', None)

    return _authorize_and_login(
        user, tenant_slug, ip,
        require_admin=True, require_superadmin=False,
        remember=False,
        next_page=next_page,
        default_next=url_for('admin.dashboard'),
        on_denied=lambda: redirect(url_for('auth.login')),
    )
