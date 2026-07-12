"""
app/auth/routes_signup.py — Register + email verify + Google OAuth routes.

Attach these to the existing `auth` blueprint by importing this module at
the bottom of app/auth/__init__.py (see AUTH_INIT_ADDITIONS.py).

Every route here is additive; nothing here modifies the existing
/auth/login, /auth/login/2fa, /auth/logout, /auth/forgot-password, or
/auth/reset-password/<token> handlers.
"""
from __future__ import annotations

import logging

from flask import (current_app, flash, redirect, render_template, request,
                   session, url_for)
from flask_login import current_user

from app import limiter
from app.models import PendingSignup, User
from app.auth import auth, _get_ip, _is_safe_url, _DEFAULT_TENANT_SLUG, _complete_login, _handle_login
from app.forms import LoginForm, RegisterForm, EmailOTPForm
from app.security import log_security_event
from app.utils import log_activity
from app.services.auth.verification_service import (
    # Legacy token-link functions — kept for the still-live
    # /auth/verify-email/<token> route (old emails already sent may still
    # use it) but no longer issued by register()/resend below.
    verify_token, VerificationError,
    # Current OTP-based verification path (v3.10):
    issue_email_verification_otp, verify_email_verification_otp,
    send_email_verification_otp, OTPRateLimitedError,
)
from app.services.auth.complete_signup_service import (
    PendingSignupError,
    create_or_refresh_pending_signup,
    get_active_pending_signup_by_email,
    get_latest_pending_signup_by_email,
    get_pending_signup_otp_remaining_seconds,
    get_pending_signup_resend_cooldown_remaining,
    resend_pending_signup_otp,
    send_pending_signup_otp,
    verify_pending_signup_otp,
    complete_pending_signup,
)
from app.services.auth.signup_otp_email_service import get_signup_otp_ttl_minutes
from app.services.auth.email_policy import resolve_email_for_login

logger = logging.getLogger(__name__)


def _issue_and_send_otp(user: User) -> bool:
    """Issue a fresh OTP for `user` and email it.

    Returns True if the send succeeded, False if the provider reported
    a delivery failure. Raises OTPRateLimitedError when the tenant has
    hit request caps; caller decides how to surface that.
    """
    raw_otp = issue_email_verification_otp(
        user, ip_address=_get_ip(), user_agent=request.headers.get('User-Agent'),
    )
    ok = send_email_verification_otp(user, raw_otp)
    if not ok:
        logger.error('OTP generated but delivery failed for user_id=%s', user.id)
    return ok


def _issue_and_send_pending_signup_otp(pending_signup: PendingSignup) -> bool:
    """Issue a fresh OTP for a pending signup and email it."""
    ok = resend_pending_signup_otp(
        pending_signup,
        ip_address=_get_ip(),
        user_agent=request.headers.get('User-Agent'),
    )
    if not ok:
        logger.error('Pending signup OTP delivery failed for pending_signup_id=%s', pending_signup.id)
    return ok


def _render_verify_email_page(
    *,
    email: str,
    form: EmailOTPForm,
    signup_state: str = 'active',
    status_code: int = 200,
):
    """Render the verification page with backend-authoritative resend state."""
    pending = get_active_pending_signup_by_email(email) if email else None
    resend_remaining = get_pending_signup_resend_cooldown_remaining(pending)
    otp_remaining = get_pending_signup_otp_remaining_seconds(pending)
    otp_ttl_seconds = max(60, get_signup_otp_ttl_minutes() * 60)
    return (
        render_template(
            'auth/verify_email_sent.html',
            email=email,
            form=form,
            signup_state=signup_state,
            resend_cooldown_remaining=resend_remaining,
            resend_cooldown_seconds=60,
            otp_remaining_seconds=otp_remaining if pending is not None else otp_ttl_seconds,
            otp_ttl_seconds=otp_ttl_seconds,
        ),
        status_code,
    )


# ── /auth/register ───────────────────────────────────────────────────────────
@auth.route('/register', methods=['GET', 'POST'])
@limiter.limit('10 per minute')
@limiter.limit('30 per hour')
def register():
    if current_user.is_authenticated:
        return redirect(url_for('admin.dashboard'))

    next_url = request.args.get('next', '')
    if next_url and not _is_safe_url(next_url):
        next_url = ''

    # GET (e.g. an old bookmark to /auth/register) now lands on the same
    # unified portal the landing page CTAs use, instead of the old bare
    # auth/register.html, so there's a single signup surface.
    if request.method == 'GET':
        return redirect(url_for('auth.portal', tab='register', next=next_url))

    form = RegisterForm()
    if form.validate_on_submit():
        try:
            pending, raw_otp, pending_action = create_or_refresh_pending_signup(
                username=form.username.data,
                full_name=form.full_name.data,
                email=form.email.data,
                password=form.password.data,
                ip_address=_get_ip(),
                user_agent=request.headers.get('User-Agent'),
            )
        except PendingSignupError as exc:
            flash(str(exc), 'danger')
            return render_template('auth/portal.html', active_tab='signup',
                                    next_url='', register_form=form,
                                    login_form=LoginForm(),
                                    google_enabled=_google_enabled(),
                                    github_enabled=_github_enabled())
        except Exception:
            logger.exception('register: failed to create pending signup for email=%s', form.email.data)
            flash('Unable to start signup. Please try again later.', 'danger')
            return render_template('auth/portal.html', active_tab='signup',
                                    next_url='', register_form=form,
                                    login_form=LoginForm(),
                                    google_enabled=_google_enabled(),
                                    github_enabled=_github_enabled())

        if pending_action == 'cooldown' or raw_otp is None:
            remaining = get_pending_signup_resend_cooldown_remaining(pending)
            logger.info(
                'register: pending signup resend cooldown enforced pending_signup_id=%s email=%s remaining=%s',
                pending.id,
                pending.email,
                remaining,
            )
            flash(
                f'We already started signup for this email. Please wait {remaining} seconds before requesting another code.',
                'warning',
            )
            return redirect(url_for('auth.verify_email_sent', email=pending.email))

        try:
            sent = send_pending_signup_otp(pending, raw_otp)
        except Exception:
            logger.exception('register: OTP issuance/dispatch failed for pending_signup_id=%s', pending.id)
            flash('Signup saved, but we could not send the verification code. Please try Resend Code.', 'warning')
            return redirect(url_for('auth.verify_email_sent', email=pending.email))

        if sent:
            if pending_action == 'refreshed':
                flash('We already started signup for this email, so we sent a fresh verification code.', 'success')
            elif pending_action == 'replaced_expired':
                flash('Your previous signup session expired, so we started a fresh signup and emailed a new code.', 'success')
            else:
                flash('Signup created. Enter the 6-digit code we emailed you.', 'success')
        else:
            flash('Signup saved, but we could not send the verification code. Please try Resend Code.', 'warning')
        return redirect(url_for('auth.verify_email_sent', email=pending.email))

    return render_template(
        'auth/portal.html',
        active_tab='signup',
        next_url=next_url,
        register_form=form,
        login_form=LoginForm(),
        google_enabled=_google_enabled(),
        github_enabled=_github_enabled(),
    )


# ── /auth/verify-email/<token> ───────────────────────────────────────────────
@auth.route('/verify-email/<token>')
@limiter.limit('30 per hour')
def verify_email(token: str):
    try:
        user = verify_token(token)
    except VerificationError as exc:
        flash(str(exc), 'danger')
        return render_template('auth/verify_email_result.html', ok=False)
    log_security_event('email_verified', user, f'Email verified from {_get_ip()}', 'info')
    flash('Email verified. You can now sign in.', 'success')
    return render_template('auth/verify_email_result.html', ok=True)


# ── /auth/verify-email/sent ──────────────────────────────────────────────────
# Renamed in practice to "the OTP entry page" — URL kept as /sent for
# backward compatibility with the redirect register() already issues.
@auth.route('/verify-email', methods=['GET', 'POST'])
@limiter.limit('10 per minute')
def verify_email_sent():
    email = (request.args.get('email') or request.form.get('email') or '').strip().lower()
    formdata = request.form
    if request.method == 'POST':
        formdata = request.form.copy()
        code = (formdata.get('code') or '').strip()
        if not code:
            digit_values = ''.join(formdata.getlist('code_digit'))
            code = ''.join(ch for ch in digit_values if ch.isdigit())[:6]
            if code:
                formdata.setlist('code', [code])
    form = EmailOTPForm(formdata=formdata if request.method == 'POST' else None)

    if request.method == 'POST' and form.validate_on_submit():
        pending = get_active_pending_signup_by_email(email) if email else None
        generic_err = 'Invalid code, or this link has expired.'

        if pending and not pending.email_verified:
            ok, err = verify_pending_signup_otp(pending, form.code.data.strip())
            if not ok:
                flash(err, 'danger')
                return _render_verify_email_page(email=email, form=form, status_code=400)

            try:
                user = complete_pending_signup(
                    pending,
                    ip_address=_get_ip(),
                    user_agent=request.headers.get('User-Agent'),
                )
            except PendingSignupError as exc:
                flash(str(exc), 'danger')
                return _render_verify_email_page(email=email, form=form, status_code=400)
            except Exception:
                logger.exception('verify_email_sent: completing pending signup failed for email=%s', email)
                flash('We verified your code but could not create your account. Please try again or contact support.', 'danger')
                return _render_verify_email_page(email=email, form=form, status_code=500)

            log_security_event('email_verified', user, f'Email verified via OTP from {_get_ip()}', 'info')
            log_activity('email_verified', 'user', user.username,
                         'Verified via OTP', tenant_slug=user.tenant_slug)
            session['show_welcome_modal'] = True
            return _complete_login(user, remember=False, next_page=None,
                                    default_next=url_for('root'))

        user = resolve_email_for_login(email, require_superadmin=False)
        if not user or user.email_verified or user.is_superadmin:
            flash(generic_err, 'danger')
            return _render_verify_email_page(email=email, form=form, status_code=400)

        try:
            verify_email_verification_otp(user, form.code.data.strip())
        except VerificationError as exc:
            flash(str(exc), 'danger')
            return _render_verify_email_page(email=email, form=form, status_code=400)

        log_security_event('email_verified', user, f'Email verified via OTP from {_get_ip()}', 'info')
        log_activity('email_verified', 'user', user.username,
                     'Verified via OTP', tenant_slug=user.tenant_slug)

        session['show_welcome_modal'] = True
        try:
            from app.services.tenant.onboarding_service import create_default_portfolio_for
            create_default_portfolio_for(user)
        except Exception:
            logger.exception('verify_email_sent: onboarding failed for user_id=%s', user.id)
            flash('Your account was verified but we encountered an error creating your workspace. Please contact support.', 'warning')

        return _complete_login(user, remember=False, next_page=None,
                                default_next=url_for('root'))

    signup_state = 'active'
    if email:
        latest_before_cleanup = get_latest_pending_signup_by_email(email)
        pending = get_active_pending_signup_by_email(email)
        if pending is None:
            legacy_user = resolve_email_for_login(email, require_superadmin=False)
            if legacy_user and not legacy_user.email_verified and not legacy_user.is_superadmin:
                signup_state = 'legacy_user'
            elif latest_before_cleanup is not None and latest_before_cleanup.is_expired:
                signup_state = 'expired'
                flash('This signup session expired. Please create your account again.', 'warning')
            else:
                signup_state = 'missing'
                flash('No active signup session was found for this email.', 'warning')
    else:
        signup_state = 'missing'
        flash('No active signup session was found for this email.', 'warning')

    return _render_verify_email_page(email=email, form=form, signup_state=signup_state)[0]


@auth.route('/verify-email/sent', methods=['GET', 'POST'])
def verify_email_sent_alias():
    if request.method == 'GET':
        email = request.args.get('email', '').strip().lower()
        return redirect(url_for('auth.verify_email_sent', email=email), code=301)
    return verify_email_sent()


# ── /auth/verify-email/resend ────────────────────────────────────────────────
# Lives on the OTP entry page itself (email already known from the query
# string / hidden field) rather than asking the user to retype their email
# on a separate screen.
@auth.route('/verify-email/resend', methods=['POST'])
@limiter.limit('5 per hour')  # Global hourly rate limit
def resend_verification():
    """Resend the pending-signup email verification OTP.

    The resend flow intentionally uses only PendingSignup state.  It does not
    silently pretend success when the pending signup is missing/expired because
    that makes the signup page look broken and prevents the user from knowing
    they must create the account again.
    """
    email = (request.form.get('email') or '').strip().lower()
    logger.info('Signup OTP resend requested email=%s ip=%s', email or '(missing)', _get_ip())

    if not email:
        logger.info('Signup OTP resend blocked: missing email')
        flash('No active signup session was found. Please create your account again.', 'warning')
        return redirect(url_for('auth.portal', tab='register'))

    latest = get_latest_pending_signup_by_email(email)
    if latest is not None and latest.is_expired:
        logger.info('Signup OTP resend blocked: pending signup expired id=%s email=%s', latest.id, email)
        flash('This signup session expired. Please create your account again.', 'warning')
        return redirect(url_for('auth.verify_email_sent', email=email))

    pending = get_active_pending_signup_by_email(email)
    if pending is None or pending.email_verified:
        logger.info('Signup OTP resend blocked: no active pending signup email=%s found=%s', email, bool(latest))
        flash('No active signup session was found. Please create your account again.', 'warning')
        return redirect(url_for('auth.verify_email_sent', email=email))

    remaining = get_pending_signup_resend_cooldown_remaining(pending)
    logger.info('Signup OTP resend pending signup found id=%s email=%s cooldown_remaining=%s', pending.id, email, remaining)
    if remaining > 0:
        flash(f'Please wait {remaining} seconds before requesting another code.', 'warning')
        return redirect(url_for('auth.verify_email_sent', email=email))

    try:
        sent = resend_pending_signup_otp(
            pending,
            ip_address=_get_ip(),
            user_agent=request.headers.get('User-Agent'),
        )
    except PendingSignupError as exc:
        logger.info('Signup OTP resend blocked by service pending_signup_id=%s reason=%s', pending.id, str(exc))
        flash(str(exc), 'warning')
        return redirect(url_for('auth.verify_email_sent', email=email))
    except Exception:
        logger.exception('Signup OTP resend failed pending_signup_id=%s email=%s', pending.id, email)
        flash('We could not send a new verification code. Your previous code is still valid until it expires.', 'warning')
        return redirect(url_for('auth.verify_email_sent', email=email))

    if sent:
        logger.info('Signup OTP resend sent successfully pending_signup_id=%s email=%s', pending.id, email)
        flash('A fresh verification code has been emailed to you.', 'success')
    else:
        logger.error('Signup OTP resend delivery failed pending_signup_id=%s email=%s', pending.id, email)
        flash('We could not send a new verification code. Your previous code is still valid until it expires.', 'warning')
    return redirect(url_for('auth.verify_email_sent', email=email))


# NOTE: Google OAuth login/callback live in app/auth/oauth.py
# (auth.google_login / auth.google_callback at /auth/google/login and
# /auth/google/callback). This file used to define its own second copy of
# both — same endpoint names, /auth/google/callback collided with the
# oauth.py route — which is why this module could never be safely imported
# into the blueprint before. Removed rather than fixed in place: oauth.py's
# version is the complete one (account lockout checks, no ghost-user
# creation on login, superadmin hard-block). Do not re-add a Google route
# here; extend app/auth/oauth.py instead.


# ── /auth ────────────────────────────────────────────────────────────────
@auth.route('', methods=['GET'])
@auth.route('/', methods=['GET'])
def portal():
    """
    Public-facing sign-in / create-account portal (landing page CTAs point
    here). Purely presentational — the Sign In tab posts to the existing
    auth.login endpoint, the Create Account tab posts to the existing
    auth.register endpoint above. No session/tenant/role logic lives here;
    that all still happens exactly as before in auth.login / auth.register.
    """
    if current_user.is_authenticated:
        if getattr(current_user, 'is_superadmin', False):
            return redirect(url_for('superadmin.dashboard'))
        return redirect(url_for('admin.dashboard'))

    tab = request.args.get('tab', 'signin')
    if tab == 'login':
        tab = 'signin'
    elif tab == 'register':
        tab = 'signup'
    if tab not in ('signin', 'signup'):
        tab = 'signin'
    next_url = request.args.get('next', '')
    if next_url and not _is_safe_url(next_url):
        next_url = ''

    return render_template(
        'auth/portal.html',
        active_tab=tab,
        next_url=next_url,
        login_form=LoginForm(),
        register_form=RegisterForm(),
        google_enabled=_google_enabled(),
        github_enabled=_github_enabled(),
    )


# ── helpers ──────────────────────────────────────────────────────────────────
def _google_enabled() -> bool:
    # Single source of truth: same check app/auth/oauth.py's
    # _oauth_client() uses, so this file can't drift into reporting Google
    # sign-in as available when oauth.py would actually refuse it.
    if not current_app.config.get('GOOGLE_OAUTH_ENABLED'):
        return False
    from app.extensions import oauth
    return getattr(oauth, 'google', None) is not None


def _github_enabled() -> bool:
    if not current_app.config.get('GITHUB_OAUTH_ENABLED'):
        return False
    from app.extensions import oauth
    return getattr(oauth, 'github', None) is not None
