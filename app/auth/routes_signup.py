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
    create_pending_signup,
    issue_pending_signup_otp,
    send_pending_signup_otp,
    verify_pending_signup_otp,
    complete_pending_signup,
)

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
    raw_otp = issue_pending_signup_otp(
        pending_signup,
        ip_address=_get_ip(),
        user_agent=request.headers.get('User-Agent'),
    )
    ok = send_pending_signup_otp(pending_signup, raw_otp)
    if not ok:
        logger.error('Pending signup OTP generated but delivery failed for pending_signup_id=%s', pending_signup.id)
    return ok


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
            pending, raw_otp = create_pending_signup(
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
                                    google_enabled=_google_enabled())
        except Exception:
            logger.exception('register: failed to create pending signup for email=%s', form.email.data)
            flash('Unable to start signup. Please try again later.', 'danger')
            return render_template('auth/portal.html', active_tab='signup',
                                    next_url='', register_form=form,
                                    login_form=LoginForm(),
                                    google_enabled=_google_enabled())

        try:
            sent = send_pending_signup_otp(pending, raw_otp)
        except Exception:
            logger.exception('register: OTP issuance/dispatch failed for pending_signup_id=%s', pending.id)
            flash('Signup created but we could not send the verification email. Please try resending the code or contact support.', 'danger')
            return redirect(url_for('auth.verify_email_sent', email=pending.email))

        if sent:
            flash('Signup created. Enter the 6-digit code we emailed you.', 'success')
        else:
            flash('Signup created but the verification email could not be delivered. Please request a new code.', 'warning')
        return redirect(url_for('auth.verify_email_sent', email=pending.email))

    return render_template(
        'auth/portal.html',
        active_tab='signup',
        next_url=next_url,
        register_form=form,
        login_form=LoginForm(),
        google_enabled=_google_enabled(),
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
    form = EmailOTPForm()

    if request.method == 'POST' and form.validate_on_submit():
        pending = PendingSignup.query.filter_by(email=email).first() if email else None
        generic_err = 'Invalid code, or this link has expired.'

        if pending and not pending.email_verified:
            ok, err = verify_pending_signup_otp(pending, form.code.data.strip())
            if not ok:
                flash(err, 'danger')
                return render_template('auth/verify_email_sent.html', email=email, form=form), 400

            try:
                user = complete_pending_signup(
                    pending,
                    ip_address=_get_ip(),
                    user_agent=request.headers.get('User-Agent'),
                )
            except PendingSignupError as exc:
                flash(str(exc), 'danger')
                return render_template('auth/verify_email_sent.html', email=email, form=form), 400
            except Exception:
                logger.exception('verify_email_sent: completing pending signup failed for email=%s', email)
                flash('We verified your code but could not create your account. Please try again or contact support.', 'danger')
                return render_template('auth/verify_email_sent.html', email=email, form=form), 500

            log_security_event('email_verified', user, f'Email verified via OTP from {_get_ip()}', 'info')
            log_activity('email_verified', 'user', user.username,
                         'Verified via OTP', tenant_slug=user.tenant_slug)
            session['show_welcome_modal'] = True
            return _complete_login(user, remember=False, next_page=None,
                                    default_next=url_for('root'))

        user = User.query.filter_by(email=email).first()
        if not user or user.email_verified or user.is_superadmin:
            flash(generic_err, 'danger')
            return render_template('auth/verify_email_sent.html', email=email, form=form), 400

        try:
            verify_email_verification_otp(user, form.code.data.strip())
        except VerificationError as exc:
            flash(str(exc), 'danger')
            return render_template('auth/verify_email_sent.html', email=email, form=form), 400

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

    return render_template('auth/verify_email_sent.html', email=email, form=form)


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
    """
    Resend OTP code for email verification.
    
    v4.0 FIX: Added per-request cooldown (60 seconds) to prevent OTP spam.
    """
    import time
    email = (request.form.get('email') or '').strip().lower()
    
    if email:
        pending = PendingSignup.query.filter_by(email=email).first()
        if pending and not pending.email_verified:
            try:
                raw_otp = issue_pending_signup_otp(
                    pending,
                    ip_address=_get_ip(),
                    user_agent=request.headers.get('User-Agent'),
                )
                send_pending_signup_otp(pending, raw_otp)
            except PendingSignupError:
                pass
            except Exception:
                logger.exception('resend_verification: pending signup OTP send failed for email=%s', email)
        else:
            user = User.query.filter_by(email=email).first()
            # Do not leak whether the account exists.
            if user and not user.email_verified and not user.is_superadmin:
                # v4.0: Check per-user cooldown (60-second minimum between resends)
                try:
                    import redis
                    redis_url = request.environ.get('REDIS_URL', '')
                    if redis_url:
                        try:
                            r = redis.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2)
                            cooldown_key = f'otp_resend_cooldown:{user.id}'
                            if r.exists(cooldown_key):
                                remaining = r.ttl(cooldown_key)
                                flash(f'Please wait {remaining} seconds before requesting another code.', 'warning')
                                return redirect(url_for('auth.verify_email_sent', email=email))
                            # Set cooldown for next 60 seconds
                            r.setex(cooldown_key, 60, '1')
                        except Exception:
                            pass  # Redis unavailable, fall through
                except Exception:
                    pass
                
                try:
                    _issue_and_send_otp(user)
                except OTPRateLimitedError:
                    flash('Too many requests. Please wait a few minutes and try again.', 'warning')
                except Exception:
                    logger.exception('resend_verification: OTP send failed')
    
    flash('If that email is registered and unverified, a new code has been sent.', 'info')
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
