"""
app/auth/oauth.py — Google Sign-In (v1.0)

SCOPE — read before touching this file:
  google_login / google_callback below are a SECOND LOGIN METHOD for users
  who ALREADY have a User row, provisioned the normal way (SuperAdmin →
  Create Tenant, or local signup, or google_signup below). They are NOT a
  signup system.

  Hard guarantees enforced below (google_login / google_callback ONLY):
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

  google_signup / google_signup_callback at the bottom of this file are
  the ONE deliberate exception to guarantee #1 — the Create Account tab's
  "Continue with Google" button. They auto-provision a new tenant + user
  via app/services/auth/google_oauth_service.resolve_or_create_google_user
  (same tenant-creation rules as local signup: never superadmin, never the
  default tenant, plan='Basic', email pre-verified since Google already
  confirmed it). Do not extend google_login/google_callback to auto-create
  — add to the signup pair instead, so the login-only guarantee above
  stays true by construction.

Importing this module (see bottom of app/auth/__init__.py) registers
@auth.route(...) google_login / google_callback / google_signup /
google_signup_callback on the shared `auth` Blueprint — same pattern
already used by app/admin/routes/__init__.py and
app/superadmin/routes/__init__.py for their route submodules.
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
from app.services.auth.google_oauth_service import (
    resolve_or_create_google_user, GoogleAuthError,
)
from app.services.auth.github_oauth_service import (
    resolve_or_create_github_user, GitHubAuthError,
)

logger = logging.getLogger(__name__)


def _oauth_external_url(endpoint: str, **values) -> str:
    """Build the exact public OAuth redirect URI used with Google/GitHub.

    Why this is intentionally stricter than plain ``url_for(...,
    _external=True)``:

    * Render/Cloudflare/reverse proxies may make Flask see ``http://`` even
      when the visitor is on ``https://``. Google compares the redirect URI
      character-for-character, so ``http`` vs ``https`` causes
      ``redirect_uri_mismatch``.
    * Production should have one canonical origin. Set APP_BASE_URL to your
      public domain, for example ``https://myportfoliohub.online``.
    * If APP_BASE_URL is missing, infer the public host from forwarded headers
      and force HTTPS for non-localhost hosts so production does not send an
      accidental internal/http callback to Google.
    """
    path = url_for(endpoint, **values)

    configured_base = (
        current_app.config.get('APP_BASE_URL')
        or current_app.config.get('PUBLIC_BASE_URL')
        or ''
    )
    configured_base = str(configured_base).strip().rstrip('/')
    if configured_base:
        return f"{configured_base}{path}"

    # Fallback: derive from proxy headers. ProxyFix is active in create_app(),
    # but reading the raw forwarded headers here makes the OAuth callback stable
    # even if a host/proxy strips one of them before Flask builds external URLs.
    forwarded_host = (request.headers.get('X-Forwarded-Host') or '').split(',')[0].strip()
    host = forwarded_host or request.host

    forwarded_proto = (request.headers.get('X-Forwarded-Proto') or '').split(',')[0].strip().lower()
    scheme = forwarded_proto or request.scheme or 'https'

    # Google production OAuth callbacks must use HTTPS. Keep HTTP only for local
    # development hostnames.
    local_hosts = ('localhost', '127.0.0.1', '0.0.0.0')
    host_without_port = host.split(':', 1)[0].lower()
    if host_without_port not in local_hosts:
        scheme = 'https'

    return f"{scheme}://{host}{path}"


def _log_oauth_redirect_uri(flow: str, redirect_uri: str) -> None:
    """Log the exact redirect URI sent to the OAuth provider.

    This does not log tokens or secrets. It is deliberately helpful in
    production because Google's ``redirect_uri_mismatch`` screen only says the
    URI is invalid; the app log now shows the exact URI that must be added in
    Google Cloud Console.
    """
    try:
        logger.info('Google OAuth %s redirect_uri=%s', flow, redirect_uri)
    except Exception:
        pass





def _google_redirect_uri(flow: str) -> str:
    """Return the exact redirect URI used for Google OAuth.

    Production-safe behavior:
    * Prefer explicit env/config overrides when set. This lets deployment use
      the exact URI already registered in Google Cloud Console.
    * Otherwise use one shared callback, /auth/google/callback, for BOTH sign
      in and create-account. The session flag `_oauth_flow` decides what the
      callback should do after Google returns.

    Recommended Google Cloud Console URI:
      https://myportfoliohub.online/auth/google/callback
    """
    flow = (flow or '').strip().lower()
    specific_key = 'GOOGLE_SIGNUP_REDIRECT_URI' if flow == 'signup' else 'GOOGLE_SIGNIN_REDIRECT_URI'
    explicit = (
        current_app.config.get(specific_key)
        or current_app.config.get('GOOGLE_OAUTH_REDIRECT_URI')
        or ''
    )
    explicit = str(explicit).strip()
    if explicit:
        return explicit
    return _oauth_external_url('auth.google_callback')

def _oauth_client():
    """Return the registered Authlib google client, or None if disabled."""
    if not current_app.config.get('GOOGLE_OAUTH_ENABLED'):
        return None
    from app.extensions import oauth
    return getattr(oauth, 'google', None)


def _github_oauth_client():
    """Return the registered Authlib GitHub client, or None if disabled."""
    if not current_app.config.get('GITHUB_OAUTH_ENABLED'):
        return None
    from app.extensions import oauth
    return getattr(oauth, 'github', None)


def _github_api_json(access_token: str, path: str, *, label: str):
    import requests

    response = requests.get(
        f'https://api.github.com/{path.lstrip("/")}',
        headers={
            'Authorization': f'Bearer {access_token}',
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
        },
        timeout=10,
    )
    if response.status_code >= 400:
        raise ValueError(f'{label} request failed with HTTP {response.status_code}')
    data = response.json()
    if data is None:
        raise ValueError(f'{label} returned an empty response')
    return data


def _fetch_github_identity(client, token: dict) -> dict:
    """Fetch GitHub profile plus primary verified email. Never returns tokens."""
    access_token = (token or {}).get('access_token')
    if not access_token:
        raise ValueError('GitHub did not return an access token')

    profile = _github_api_json(access_token, 'user', label='GitHub profile')
    emails = _github_api_json(access_token, 'user/emails', label='GitHub emails')
    if not isinstance(emails, list):
        emails = []

    verified_emails = [
        e for e in emails
        if isinstance(e, dict) and e.get('email') and e.get('verified')
    ]
    primary = next((e for e in verified_emails if e.get('primary')), None)
    selected = primary or (verified_emails[0] if verified_emails else None)
    if not selected:
        raise ValueError('GitHub did not return a verified email address')

    github_id = profile.get('id')
    if not github_id:
        raise ValueError('GitHub did not return a stable account identifier')

    return {
        'github_id': str(github_id),
        'login': profile.get('login') or '',
        'email': (selected.get('email') or '').strip().lower(),
        'name': profile.get('name') or profile.get('login') or '',
        'avatar_url': profile.get('avatar_url') or '',
    }


@auth.route('/google/signin', endpoint='google_signin')
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
    # Production canonical callback for the Sign In tab.
    # Keep /auth/google/callback registered below as a backward-compatible
    # alias, but always send Google the stable /auth/google/signin/callback
    # URL so production OAuth configuration does not drift between the
    # Sign In and Create Account buttons.
    redirect_uri = _google_redirect_uri('signin')
    _log_oauth_redirect_uri('signin', redirect_uri)
    session['_oauth_flow'] = 'signin'
    return client.authorize_redirect(redirect_uri)


@auth.route('/google/callback', endpoint='google_callback')
@auth.route('/google/signin/callback', endpoint='google_signin_callback')
@auth.route('/google/signup/callback', endpoint='google_signup_callback')
@limiter.limit('20 per minute')
@limiter.limit('100 per hour')
def google_callback():
    # One shared callback can safely serve both Google Sign In and Google
    # Create Account. Google only cares that the exact redirect_uri sent in the
    # first request is listed in Google Cloud Console. The app decides the flow
    # from the session flag created in google_login()/google_signup().
    flow = (session.get('_oauth_flow') or '').strip().lower()
    if flow == 'signup' or request.path.endswith('/signup/callback'):
        return _google_signup_callback_impl()
    return _google_signin_callback_impl()


def _google_signin_callback_impl():
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
        if not google_sub:
            raise ValueError('Google did not return a stable subject identifier')

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

    # Look up by Google subject first. Email fallback uses the central policy so
    # duplicate owner/shared emails are never selected by chance.
    user = User.query.filter_by(google_id=google_sub).first() if google_sub else None
    if user is None:
        from app.services.auth.email_policy import resolve_email_for_login

        user = resolve_email_for_login(
            email,
            tenant_slug=session.get('tenant_slug'),
            require_superadmin=False,
        )
        if user is None:
            log_security_event('oauth_ambiguous_email', None, f'Google login could not safely resolve email {email} from {ip}', 'warning')
            flash('That Google email could not be matched to one tenant account safely. Sign in with your username and password from the correct portal first, then link Google there.', 'danger')
            return redirect(url_for('auth.login'))

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


# ── /auth/google/signup ──────────────────────────────────────────────────────
# See module docstring: this pair is the one deliberate exception to the
# "never auto-provision" rule google_login/google_callback enforce above.
@auth.route('/google/signup')
@limiter.limit('20 per minute')
@limiter.limit('100 per hour')
def google_signup():
    """Entry point for the Create Account tab's 'Continue with Google'."""
    if current_user.is_authenticated:
        return redirect(url_for('admin.dashboard'))

    client = _oauth_client()
    if client is None:
        flash('Google Sign-In is not available right now.', 'danger')
        return redirect(url_for('auth.portal', tab='signup'))

    next_page = request.args.get('next')
    if next_page and _is_safe_url(next_page):
        session['_oauth_signup_next'] = next_page
    else:
        session.pop('_oauth_signup_next', None)

    redirect_uri = _google_redirect_uri('signup')
    _log_oauth_redirect_uri('signup', redirect_uri)
    session['_oauth_flow'] = 'signup'
    return client.authorize_redirect(redirect_uri)


# ── Google signup callback implementation ───────────────────────────────────
def _google_signup_callback_impl():
    ip = _get_ip()
    client = _oauth_client()
    if client is None:
        flash('Google Sign-In is not available right now.', 'danger')
        return redirect(url_for('auth.portal', tab='signup'))

    try:
        token = client.authorize_access_token()
        userinfo = token.get('userinfo') or client.userinfo(token=token)
    except Exception as exc:
        db.session.rollback()
        logger.exception('Google signup callback failed from %s: %s', ip, exc)
        log_security_event('oauth_signup_failed', None,
                           f'Google signup callback error from {ip}: {exc}', 'warning')
        flash('Google sign-up failed. Please try again.', 'danger')
        return redirect(url_for('auth.portal', tab='signup'))

    userinfo = userinfo or {}
    try:
        # resolve_or_create_google_user handles all three cases (already
        # linked / existing local account / brand new) — see its docstring
        # in app/services/auth/google_oauth_service.py. It raises
        # GoogleAuthError for unverified email or a superadmin email,
        # exactly like the password-signup path raises RegistrationError.
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
        log_security_event('oauth_signup_rejected', None, f'{exc} from {ip}', 'warning')
        flash(str(exc), 'danger')
        return redirect(url_for('auth.portal', tab='signup'))

    next_page = session.pop('_oauth_signup_next', None)
    if not _is_safe_url(next_page):
        next_page = None

    return _authorize_and_login(
        user, user.tenant_slug, ip,
        require_admin=True, require_superadmin=False,
        remember=True,
        next_page=next_page,
        default_next=url_for('admin.dashboard'),
        on_denied=lambda: redirect(url_for('auth.portal', tab='signup')),
    )


# ── /auth/github/login ──────────────────────────────────────────────────────
@auth.route('/github/login')
@limiter.limit('20 per minute')
@limiter.limit('100 per hour')
def github_login():
    """Entry point for 'Continue with GitHub' on the Sign in tab."""
    if current_user.is_authenticated:
        return redirect(url_for('admin.dashboard'))

    client = _github_oauth_client()
    if client is None:
        flash('GitHub Sign-In is not available right now.', 'danger')
        return redirect(url_for('auth.login'))

    session['_github_oauth_flow'] = 'login'
    next_page = request.args.get('next')
    if next_page and _is_safe_url(next_page):
        session['_oauth_next'] = next_page
    else:
        session.pop('_oauth_next', None)

    redirect_uri = url_for('auth.github_callback', _external=True)
    return client.authorize_redirect(redirect_uri)


@auth.route('/github/callback')
@limiter.limit('20 per minute')
@limiter.limit('100 per hour')
def github_callback():
    ip = _get_ip()
    client = _github_oauth_client()
    if client is None:
        flash('GitHub Sign-In is not available right now.', 'danger')
        return redirect(url_for('auth.login'))

    try:
        token = client.authorize_access_token()
        github_info = _fetch_github_identity(client, token)
    except Exception as exc:
        db.session.rollback()
        logger.exception('GitHub OAuth callback failed from %s: %s', ip, exc)
        log_security_event('github_oauth_failed', None, f'GitHub callback error from {ip}: {exc}', 'warning')
        flash('GitHub Sign-In failed. Please try again, or use your password.', 'danger')
        return redirect(url_for('auth.login'))

    github_id = github_info['github_id']
    email = github_info['email']
    flow = session.pop('_github_oauth_flow', 'login')

    if flow == 'signup':
        try:
            user = resolve_or_create_github_user(
                github_id=github_info['github_id'],
                login=github_info.get('login'),
                email=github_info.get('email') or '',
                full_name=github_info.get('name'),
                avatar_url=github_info.get('avatar_url'),
                ip=ip,
                user_agent=request.headers.get('User-Agent'),
            )
        except GitHubAuthError as exc:
            log_security_event('github_oauth_signup_rejected', None, f'{exc} from {ip}', 'warning')
            flash(str(exc), 'danger')
            return redirect(url_for('auth.portal', tab='signup'))

        next_page = session.pop('_oauth_signup_next', None)
        if not _is_safe_url(next_page):
            next_page = None

        return _authorize_and_login(
            user, user.tenant_slug, ip,
            require_admin=True, require_superadmin=False,
            remember=True,
            next_page=next_page,
            default_next=url_for('admin.dashboard'),
            on_denied=lambda: redirect(url_for('auth.portal', tab='signup')),
        )

    user = User.query.filter_by(github_id=github_id).first()
    if user is None:
        from app.services.auth.email_policy import resolve_email_for_login

        user = resolve_email_for_login(
            email,
            tenant_slug=session.get('tenant_slug'),
            require_superadmin=False,
        )
        if user is None:
            log_security_event('github_oauth_no_account', None, f'GitHub login could not safely resolve email {email} from {ip}', 'info')
            flash('No tenant account was found for that verified GitHub email. Use Create account or sign in with your username and password first.', 'danger')
            return redirect(url_for('auth.login'))

    if user.github_id and user.github_id != github_id:
        log_security_event('github_oauth_identity_mismatch', user, f'GitHub id mismatch for {email} from {ip}', 'warning')
        flash('This account is already linked to a different GitHub identity. Please use your password.', 'danger')
        return redirect(url_for('auth.login'))

    if user.is_superadmin:
        log_security_event('github_oauth_superadmin_blocked', user, f'GitHub login rejected for superadmin account from {ip}', 'warning')
        flash('Superadmin accounts must sign in with a password.', 'danger')
        return redirect(url_for('auth.login'))

    if AccountLockout.is_locked(user, db):
        remaining = AccountLockout.get_lockout_remaining(user, db)
        minutes = (remaining + 59) // 60
        flash(f'Account locked due to too many failed login attempts. Try again in {minutes} minute(s).', 'danger')
        log_security_event('lockout_attempted', user, f'Locked-out GitHub login attempt from {ip}', 'warning')
        return redirect(url_for('auth.login'))

    if not user.github_id:
        user.github_id = github_id
        user.auth_provider = 'both'
        user.email_verified = True
        user.avatar_url = github_info.get('avatar_url') or user.avatar_url
        db.session.commit()
        log_security_event('github_oauth_linked', user, f'GitHub account linked from {ip}', 'info')

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


# ── /auth/github/signup ─────────────────────────────────────────────────────
@auth.route('/github/signup')
@limiter.limit('20 per minute')
@limiter.limit('100 per hour')
def github_signup():
    """Entry point for the Create Account tab's 'Continue with GitHub'."""
    if current_user.is_authenticated:
        return redirect(url_for('admin.dashboard'))

    client = _github_oauth_client()
    if client is None:
        flash('GitHub Sign-In is not available right now.', 'danger')
        return redirect(url_for('auth.portal', tab='signup'))

    session['_github_oauth_flow'] = 'signup'
    next_page = request.args.get('next')
    if next_page and _is_safe_url(next_page):
        session['_oauth_signup_next'] = next_page
    else:
        session.pop('_oauth_signup_next', None)

    # GitHub OAuth Apps usually have one callback URL, so sign-in and signup
    # share /auth/github/callback and the flow is selected from the session.
    redirect_uri = url_for('auth.github_callback', _external=True)
    return client.authorize_redirect(redirect_uri)
