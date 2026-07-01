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
from datetime import datetime, timezone

from flask import (current_app, flash, redirect, render_template, request,
                   session, url_for)
from flask_login import login_user, current_user

from app import db, limiter
from app.models import User
from app.auth import auth, _get_ip, _is_safe_url, _DEFAULT_TENANT_SLUG
from app.auth.oauth import oauth
from app.forms import RegisterForm, ResendVerificationForm
from app.security import log_security_event
from app.services.auth.registration_service import (
    register_local_user, RegistrationError,
)
from app.services.auth.verification_service import (
    verify_token, issue_verification_for, send_verification_email,
    VerificationError,
)
from app.services.auth.google_oauth_service import (
    resolve_or_create_google_user, GoogleAuthError,
)
from app.tenant_security import stamp_session_tenant

logger = logging.getLogger(__name__)


# ── /auth/register ───────────────────────────────────────────────────────────
@auth.route('/register', methods=['GET', 'POST'])
@limiter.limit('10 per minute')
@limiter.limit('30 per hour')
def register():
    if current_user.is_authenticated:
        return redirect(url_for('admin.dashboard'))

    form = RegisterForm()
    if form.validate_on_submit():
        try:
            user, raw_token = register_local_user(
                full_name=form.full_name.data,
                email=form.email.data,
                password=form.password.data,
                ip=_get_ip(),
                user_agent=request.headers.get('User-Agent'),
            )
        except RegistrationError as exc:
            flash(str(exc), 'danger')
            return render_template('auth/register.html', form=form,
                                    google_enabled=_google_enabled())

        try:
            send_verification_email(user, raw_token)
        except Exception:
            logger.exception('register: verification email dispatch failed')

        flash('Account created. Check your email to verify your address.', 'success')
        return redirect(url_for('auth.verify_email_sent', email=user.email))

    return render_template('auth/register.html', form=form,
                            google_enabled=_google_enabled())


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
@auth.route('/verify-email/sent')
def verify_email_sent():
    email = request.args.get('email', '')
    return render_template('auth/verify_email_sent.html', email=email)


# ── /auth/verify-email/resend ────────────────────────────────────────────────
@auth.route('/verify-email/resend', methods=['GET', 'POST'])
@limiter.limit('5 per hour')
def resend_verification():
    form = ResendVerificationForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        user = User.query.filter_by(email=email).first()
        # Do not leak whether the account exists.
        if user and not user.email_verified and not user.is_superadmin:
            try:
                raw = issue_verification_for(user)
                send_verification_email(user, raw)
            except Exception:
                logger.exception('resend_verification: send failed')
        flash('If that email is registered and unverified, a new link has been sent.', 'info')
        return redirect(url_for('auth.login'))
    return render_template('auth/verify_email_sent.html', email='', form=form, resend=True)


# ── /auth/google ─────────────────────────────────────────────────────────────
@auth.route('/google')
@limiter.limit('20 per minute')
def google_login():
    if not _google_enabled():
        flash('Google sign-in is not configured on this deployment.', 'warning')
        return redirect(url_for('auth.login'))

    # Preserve where the user meant to go (only same-origin paths).
    next_page = request.args.get('next')
    if _is_safe_url(next_page):
        session['_oauth_next'] = next_page

    redirect_uri = (
        current_app.config.get('GOOGLE_REDIRECT_URI')
        or url_for('auth.google_callback', _external=True)
    )
    return oauth.google.authorize_redirect(redirect_uri)


# ── /auth/google/callback ────────────────────────────────────────────────────
@auth.route('/google/callback')
@limiter.limit('20 per minute')
def google_callback():
    if not _google_enabled():
        flash('Google sign-in is not configured on this deployment.', 'warning')
        return redirect(url_for('auth.login'))

    try:
        token   = oauth.google.authorize_access_token()
        userinfo = token.get('userinfo') or oauth.google.parse_id_token(token, nonce=None)
    except Exception as exc:
        logger.warning('google_callback: authorize_access_token failed: %s', exc)
        flash('Google sign-in failed. Please try again.', 'danger')
        return redirect(url_for('auth.login'))

    ip = _get_ip()
    try:
        user = resolve_or_create_google_user(
            google_sub=str(userinfo.get('sub') or ''),
            email=userinfo.get('email') or '',
            email_verified=bool(userinfo.get('email_verified')),
            full_name=userinfo.get('name'),
            avatar_url=userinfo.get('picture'),
            ip=ip,
            user_agent=request.headers.get('User-Agent'),
        )
    except GoogleAuthError as exc:
        log_security_event('google_login_rejected', None,
                            f'{exc} from {ip}', 'warning')
        flash(str(exc), 'danger')
        return redirect(url_for('auth.login'))

    # Stamp tenant + log user in. Superadmin path is unreachable here (service refuses).
    stamp_session_tenant(user.id, user.tenant_slug or _DEFAULT_TENANT_SLUG)
    login_user(user, remember=True)
    session['totp_verified'] = False
    user.last_login    = datetime.now(timezone.utc)
    user.last_login_ip = ip
    db.session.commit()
    log_security_event('login', user, f'Google login from {ip}', 'info')

    next_page = session.pop('_oauth_next', None)
    if not _is_safe_url(next_page):
        next_page = None
    return redirect(next_page or url_for('admin.dashboard'))


# ── helpers ──────────────────────────────────────────────────────────────────
def _google_enabled() -> bool:
    return bool(current_app.extensions.get('google_oauth_configured'))
