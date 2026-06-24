"""
app/__init__.py — Portfolio CMS v5.0 (MailerSend-only)

URL Structure (FIXED):
  /                             → Default tenant portfolio (NO redirect to /default)
  /superadmin/                  → Superadmin dashboard
  /superadmin/login             → Superadmin login
  /admin/                       → Admin panel (session-scoped to active tenant)
  /auth/login                   → Fallback login (no tenant context)
  /<tenant_slug>/               → Other tenants' public portfolios
  /<tenant_slug>/auth/login     → Tenant-scoped login
  /<tenant_slug>/admin/login     → Tenant-scoped admin login
  /<tenant_slug>/admin/         → Tenant admin entry point

Key fixes from v3.0:
  • Root / now renders default tenant portfolio DIRECTLY (no redirect to /default/)
  • superadmin_bp registered with explicit url_prefix='/superadmin' to prevent
    tenant_bp from intercepting /superadmin paths
  • Blueprint registration order: auth → admin → superadmin → tenant_bp (last)
  • login_manager.login_view points to 'auth.login' (safe fallback)
  • Default tenant 'default' always bootstrapped at startup in dev/test
"""

import logging
import os
import time
from pathlib import Path
from flask import Flask, render_template, g, redirect, url_for
from flask_wtf.csrf import CSRFError
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from flask_migrate import Migrate
from flask_limiter import Limiter
from flask_caching import Cache
from flask_talisman import Talisman
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    _HAS_APSCHEDULER = True
except ImportError:
    _HAS_APSCHEDULER = False
from config import config
from app.tenant_security import TenantGuard, RESERVED_SLUGS
from app.heartbeat import heartbeat_bp
from app.heartbeat.health_email import health_email_bp 

# ── Extension singletons ──────────────────────────────────────────────────────
db            = SQLAlchemy()
login_manager = LoginManager()
csrf          = CSRFProtect()
migrate       = Migrate()
cache         = Cache()
_scheduler    = None   # APScheduler instance (set in create_app)

# Limiter must be a real object at module level because blueprints use
# @limiter.limit(...) as decorators at import time.
# We construct it with key_func here, then call init_app(app) inside
# create_app() so storage_uri (from config/REDIS_URL) is applied correctly.
from app.limiter_config import create_limiter_key_func

# FIX (redis-graceful-degradation): previously this module constructed
# Limiter() with whatever RATELIMIT_STORAGE_URL/REDIS_URL pointed to, then
# create_app() mutated the PRIVATE `limiter._storage_uri` attribute and
# called init_app(). Neither step ever attempted a real connection, so a
# dead/unresolvable Redis host (e.g. a Render Redis instance that was
# deleted/renamed) was only discovered when flask-limiter's storage
# backend tried to actually talk to Redis on the first rate-limited
# request -- raising a raw redis.exceptions.ConnectionError straight
# through the request, with no fallback. We now pre-flight-check Redis
# with a short-timeout PING at app-factory time and fall back to
# memory:// (logged as a WARNING, never raised) if it's unreachable.

def _request_wants_json() -> bool:
    """Return True when the current request prefers a JSON response."""
    from flask import request as _req
    try:
        accept = _req.accept_mimetypes
        return (
            accept.best == 'application/json'
            or 'application/json' in str(accept)
            or _req.is_json
            or _req.path.startswith('/api/')
        )
    except Exception:
        return False


def resolve_limiter_storage_uri(app) -> str:
    """
    Resolve the storage backend for Flask-Limiter.

    Order of precedence: RATELIMIT_STORAGE_URL (config) -> REDIS_URL (env)
    -> memory://. If a redis:// URL is configured, PING it with a short
    timeout; on ANY failure (DNS, connection refused, auth, timeout) log
    a warning and fall back to memory:// rather than letting the app
    crash on the first request that touches a rate-limited route.
    """
    storage_uri = app.config.get(
        'RATELIMIT_STORAGE_URL', os.environ.get('REDIS_URL', 'memory://')
    ) or 'memory://'

    if not storage_uri.startswith('redis'):
        return storage_uri

    try:
        import redis as _redis
        kwargs = {"socket_connect_timeout": 2, "socket_timeout": 2}
        if storage_uri.startswith("rediss://"):
            kwargs["ssl_cert_reqs"] = None
        client = _redis.from_url(storage_uri, **kwargs)
        client.ping()
        client.close()
        return storage_uri
    except Exception as exc:
        logger.warning(
            'Redis unreachable at startup (%s) -- falling back to '
            'in-memory rate limiting. Rate limits will NOT be shared '
            'across Gunicorn workers until Redis connectivity is restored.',
            exc,
        )
        return 'memory://'


# NOTE: storage_uri is intentionally NOT passed here. flask-limiter's
# constructor argument wins over app.config['RATELIMIT_STORAGE_URI'] if
# set, which would make the pre-flight-checked, fallback-aware value we
# compute in create_app() (resolve_limiter_storage_uri) impossible to
# apply without poking the library's private _storage_uri attribute.
# Leaving it unset here means create_app() -> RATELIMIT_STORAGE_URI is
# the single source of truth, via the documented config key.
limiter = Limiter(
    key_func=create_limiter_key_func,
    default_limits=["800 per hour"],
    headers_enabled=True,
)

logger = logging.getLogger(__name__)

# Reserved slugs — single source of truth in app.tenant_security
# _SYSTEM_PREFIXES kept for backward compat (same object, different name)
_SYSTEM_PREFIXES = RESERVED_SLUGS

csp = {
    "default-src": "'self'",
    "base-uri": "'self'",
    "script-src": [
        "'self'",
        "'unsafe-inline'",
        "https://cdnjs.cloudflare.com",
        "https://cdn.jsdelivr.net",
        "https://unpkg.com",
        "https://api.web3forms.com",
        "https://code.iconify.design",
    ],
    "style-src": [
        "'self'",
        "'unsafe-inline'",
        "https://fonts.googleapis.com",
        "https://cdnjs.cloudflare.com",
    ],
    "font-src": [
        "'self'",
        "data:",
        "https://fonts.gstatic.com",
    ],
    "img-src": [
        "'self'",
        "data:",
        "blob:",
        "https://*.supabase.co",
    ],
    "connect-src": [
        "'self'",
        "https://api.web3forms.com",
        "https://api.iconify.design",
    ],
    "object-src": "'none'",
    "frame-ancestors": "'none'",
}

def _init_scheduler(app):
    """
    Start APScheduler background scheduler for daily subscription renewal checks.

    CRITICAL FIX: Only start scheduler in single-worker mode or designated process.
    In Gunicorn with multiple workers (--workers N), the scheduler would fire N
    times per interval causing duplicate renewal emails and race conditions.

    Safe startup conditions:
      - ENABLE_SCHEDULER=true  (explicit opt-in for the designated worker)
      - RENDER_INSTANCE_ID is set AND this is worker 0 (Render's primary)
      - DEBUG=True / ENV=development (local dev — always single process)
    """
    global _scheduler

    if not _HAS_APSCHEDULER:
        logger.warning('APScheduler not installed — renewal reminders disabled.')
        return
    if app.testing:
        return
    if _scheduler and _scheduler.running:
        return

    enable_scheduler = os.environ.get('ENABLE_SCHEDULER', '').lower()
    is_render = bool(os.environ.get('RENDER_INSTANCE_ID'))
    is_dev = app.config.get('DEBUG') or app.config.get('ENV') == 'development'

    if is_dev and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return

    enabled = enable_scheduler in ('true', '1', 'yes')
    disabled = enable_scheduler in ('false', '0', 'no')

    should_run = (not disabled) if is_dev else enabled

    if not should_run:
        logger.info(
            'APScheduler disabled — not designated scheduler process. '
            'Set ENABLE_SCHEDULER=true to enable.'
        )
        return

    try:
        # Acquire advisory lock to prevent duplicate scheduler instances
        # when multiple Gunicorn workers start simultaneously
        from app.services.scheduler_lock import acquire_scheduler_lock
        if not acquire_scheduler_lock(app):
            return

        from app.services.renewal_scheduler import run_renewal_check

        _scheduler = BackgroundScheduler(daemon=True)
        _scheduler.add_job(
            func=run_renewal_check,
            trigger=CronTrigger(hour=2, minute=0),
            kwargs={'app': app},
            id='renewal_check',
            replace_existing=True,
            max_instances=1,
            misfire_grace_time=3600,  # Allow up to 1h late if server was down
        )
        _scheduler.start()
        logger.info('✓ APScheduler started — renewal check runs daily at 02:00')

        import atexit
        atexit.register(
            lambda: _scheduler.shutdown(wait=False)
            if _scheduler and _scheduler.running else None
        )
    except Exception as exc:
        logger.error('APScheduler startup failed: %s', exc)

def create_app(config_name: str = 'default') -> Flask:
    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'templates'),
        static_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'static'),
    )

    app.config.from_object(config[config_name])
    config[config_name].init_app(app)

    # CRIT-01/02/03: Validate environment secrets at startup
    from app.startup_validation import validate_startup_env
    validate_startup_env(app)

    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=1,
        x_proto=1,
        x_host=1,
        x_port=1,
    )

    if not app.debug:
        Talisman(
            app,
            force_https=True,
            session_cookie_secure=True,
            strict_transport_security=True,
            content_security_policy=csp,
            content_security_policy_nonce_in=["script-src"],
        )

    logging.basicConfig(
        level=logging.DEBUG if app.debug else logging.INFO,
        format='%(asctime)s %(levelname)s [%(name)s] %(message)s',
    )

    # ── Extensions ────────────────────────────────────────────────────────────
    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    migrate.init_app(app, db, compare_type=True)
    if app.config.get("CACHE_TYPE") == "RedisCache":
        redis_url = (app.config.get("CACHE_REDIS_URL") or os.environ.get("REDIS_URL", "")).strip()
        if not redis_url:
            app.config["CACHE_TYPE"] = "SimpleCache"
        else:
            try:
                import redis as _redis
                kwargs = {"socket_connect_timeout": 2, "socket_timeout": 2}
                if redis_url.startswith("rediss://"):
                    kwargs["ssl_cert_reqs"] = None
                client = _redis.from_url(redis_url, **kwargs)
                client.ping()
                client.close()
            except Exception as exc:
                logger.warning("Redis cache unreachable at startup (%s) — using SimpleCache.", exc)
                app.config["CACHE_TYPE"] = "SimpleCache"
                app.config["CACHE_REDIS_URL"] = ""
    cache.init_app(app)

    # ── Rate Limiter — pre-flight-checked storage, never crashes the app ──────
    # FIX (redis-graceful-degradation): resolve_limiter_storage_uri() PINGs
    # Redis (2s timeout) before we commit to it. A dead/unresolvable host
    # degrades to memory:// with a logged WARNING instead of raising
    # redis.exceptions.ConnectionError out of the first limited request.
    # We use the PUBLIC storage_uri attribute (not the private
    # `_storage_uri` the previous version mutated directly) and store the
    # resolved value on app.config so /health can report it accurately.
    # FIX (config-key-mismatch): flask-limiter reads app.config via the
    # key 'RATELIMIT_STORAGE_URI' (see flask_limiter.constants.ConfigVars.
    # STORAGE_URI). This codebase previously only ever set
    # 'RATELIMIT_STORAGE_URL' (URL, not URI) in config.py, which
    # flask-limiter never reads -- the app only worked because the old
    # code separately poked the private `limiter._storage_uri` attribute
    # directly. We now set the correct, documented config key so
    # init_app() resolves storage the supported way, with no private
    # attribute access anywhere in this file.
    resolved_storage_uri = resolve_limiter_storage_uri(app)
    app.config['RATELIMIT_STORAGE_URI'] = resolved_storage_uri
    app.config['RATELIMIT_STORAGE_URI_RESOLVED'] = resolved_storage_uri  # for /health
    limiter.init_app(app)
    logger.info(
        '✓ Rate limiter initialized (storage=%s)',
        'redis' if resolved_storage_uri.startswith('redis') else 'memory (degraded — not multi-worker-safe)',
    )

    # ── APScheduler — daily renewal check ────────────────────────────────────
    _init_scheduler(app)

    # ── Email Service Initialization ──────────────────────────────────────────
    try:
        from app.services.mailersend_service import init_email_services
        init_email_services(app)
    except Exception as e:
        logger.warning('⚠️ Email service initialization failed: %s', e)


    # ── Login manager config ──────────────────────────────────────────────────
    # FIX: Points to auth.login (fallback), tenant-scoped redirects are
    # handled explicitly inside admin/before_request and route handlers.
    login_manager.login_view             = 'auth.login'
    login_manager.login_message          = 'Please log in to access the admin panel.'
    login_manager.login_message_category = 'warning'
    login_manager.session_protection     = 'strong'

    # ── User loader ───────────────────────────────────────────────────────────
    from app.models import User
    from app.models.core import Tenant  # Fixed: was importing from non-existent app.models.tenant

    @login_manager.user_loader
    def load_user(user_id: str):
        """
        v3.7 hardened user loader.
        Validates that the loaded user's tenant matches session['tenant_slug'].
        Superadmins are exempt from tenant matching.
        """
        try:
            uid = int(user_id)
        except (ValueError, TypeError):
            return None

        user = db.session.get(User, uid)
        if user is None:
            return None

        # Non-superadmin: validate tenant consistency
        if not user.is_superadmin:
            from flask import session as _session
            session_tenant = _session.get('tenant_slug')
            user_tenant    = user.tenant_slug or 'default'

            if session_tenant and session_tenant != user_tenant:
                # Log cross-tenant user load attempt
                logger.critical(
                    'USER_LOADER: session tenant=%r does not match user.tenant_slug=%r '
                    'for user_id=%s — refusing load. Possible session fixation.',
                    session_tenant, user_tenant, uid,
                )
                # Do NOT return the user — this prevents cross-tenant login
                # The TenantGuard will catch the resulting unauthenticated state
                return None

        return user

    # ── Ensure instance directory exists (for SQLite) ─────────────────────────
    instance_dir = Path(app.instance_path)
    instance_dir.mkdir(parents=True, exist_ok=True)

    with app.app_context():
        _is_production = not app.config.get('TESTING') and not app.config.get('DEBUG')
        try:
            db.session.execute(db.text("SELECT 1"))
            db.session.remove()

            if app.debug:
                _ensure_profile_columns()
                _ensure_default_tenant()

            logger.info("Database connection verified at startup")

            try:
                from app.models.core import Tenant as _Tenant
                tenant = _Tenant.query.filter_by(slug="default").first()
                if tenant:
                    app.config["TENANT_LOOKUP_MODE"] = "slug_to_id"
                    app.config["DEFAULT_TENANT_ID"] = tenant.id
                    logger.info(
                        "TENANT STARTUP: lookup_mode=%s tenant_slug=%s tenant_id=%s tenant_status=%s",
                        app.config.get("TENANT_LOOKUP_MODE"),
                        tenant.slug,
                        tenant.id,
                        tenant.status,
                    )
                else:
                    app.config["TENANT_LOOKUP_MODE"] = "slug_to_id"
                    logger.warning(
                        "TENANT STARTUP: lookup_mode=%s tenant_slug=%s resolution=not_found",
                        app.config.get("TENANT_LOOKUP_MODE"),
                        "default",
                    )
            except Exception as exc:
                app.config["TENANT_LOOKUP_MODE"] = "slug_to_id"
                logger.warning(
                    "TENANT STARTUP: lookup_mode=%s tenant_slug=%s resolution=error (%s)",
                    app.config.get("TENANT_LOOKUP_MODE"),
                    "default",
                    exc,
                )

        except Exception as exc:
            db.session.remove()
            if _is_production:
                # In production: crash fast — no point serving requests without a DB
                logger.critical(
                    "Cannot reach database at startup: %s",
                    exc,
                    exc_info=True,
                )
                raise
            else:
                # In development: warn and continue — lets you run the server
                # while the remote DB is down or you haven't set DEV_DATABASE_URL yet.
                # Set DEV_DATABASE_URL=sqlite:///instance/portfolio_dev.db in .env
                # OR set FLASK_ENV=development so the SQLite fallback is used.
                logger.warning(
                    "Cannot reach database at startup (ignored in dev/test): %s\n"
                    "  → Ensure FLASK_ENV=development in your .env file.\n"
                    "  → Add DEV_DATABASE_URL=sqlite:///instance/portfolio_dev.db "
                    "to use a local SQLite database for development.",
                    exc,
                )

    # ── Blueprints ────────────────────────────────────────────────────────────
    # CRITICAL ORDER: register system blueprints BEFORE tenant_bp.
    # tenant_bp uses url_prefix='/<tenant_slug>' which is a catch-all —
    # Flask matches routes top-down; system prefixes must come first.
    from app.auth       import auth       as auth_blueprint
    from app.admin      import admin      as admin_blueprint
    from app.superadmin import superadmin as superadmin_blueprint
    from app.main       import main       as main_blueprint
    from app.tenant     import tenant_bp
    from app.webhooks   import webhooks   as webhooks_blueprint
    # NEW-01 FIX: import superadmin_forms here (was in patch file, never applied)
    from routes.form_settings import (
        superadmin_forms as superadmin_forms_blueprint,
        admin_forms      as admin_forms_blueprint,
    )

    app.register_blueprint(auth_blueprint,              url_prefix='/auth')
    app.register_blueprint(admin_blueprint,             url_prefix='/admin')
    # Ensure legacy public routes and contact support are available.
    app.register_blueprint(main_blueprint)
    # Register webhook handlers (PayMongo, etc.)
    app.register_blueprint(webhooks_blueprint)
    # FIX: explicit url_prefix='/superadmin' ensures the blueprint's
    # url_value_preprocessor doesn't conflict with tenant_bp's /<tenant_slug>
    app.register_blueprint(superadmin_blueprint,        url_prefix='/superadmin')
    # NEW-01 FIX: register form-provider blueprints before the catch-all tenant_bp
    # superadmin_forms uses url_prefix='/superadmin' (set on the Blueprint object)
    # admin_forms uses url_prefix='/admin/settings' (set on the Blueprint object)
    app.register_blueprint(superadmin_forms_blueprint)
    app.register_blueprint(admin_forms_blueprint)
    # tenant_bp MUST be last — its /<tenant_slug> prefix is a wildcard
    app.register_blueprint(tenant_bp)

    app.register_blueprint(heartbeat_bp)
    app.register_blueprint(health_email_bp)

    # ── Custom Jinja2 filters (v3.8) ─────────────────────────────────────────
    from markupsafe import Markup, escape as _escape

    @app.template_filter('nl2br')
    def nl2br_filter(value: str) -> Markup:
        """Convert newlines to <br> tags, HTML-escaping the input first."""
        if not value:
            return Markup('')
        return Markup(_escape(value).replace('\n', Markup('<br>\n')))

    @app.template_filter('upload_url')
    def upload_url_filter(value: str | None, subfolder: str) -> str:
        if not value:
            return ''
        if isinstance(value, str) and value.startswith('http'):
            return value
        return url_for('static', filename=f'uploads/{subfolder}/{value}')

    from app.heartbeat import init_heartbeat
    init_heartbeat(app)

    # ── Root route: serve default tenant DIRECTLY ─────────────────────────────
    # FIX: Instead of redirecting to /default/ (ugly URL), we render the
    # default tenant portfolio inline at /. This is the PRIMARY portfolio.
    @app.route('/')
    def root():
        """
        Root domain handler — always renders the default tenant public portfolio.
        Admins reach /admin/ and superadmins reach /superadmin/ via nav links.
        """
        # FIX: Always render the public portfolio at '/'.
        # Authenticated admins/superadmins reach their panels via explicit
        # nav links (/admin/, /superadmin/), not an automatic redirect from '/'.
        # The original redirect broke portfolio visibility for logged-in users.
        return _render_default_portfolio()

    # ── 301 backward-compat redirect: /default → / ────────────────────────────
    @app.route('/default')
    @app.route('/default/')
    def default_redirect():
        """Permanently redirect old /default URLs to root /."""
        return redirect(url_for('root'), 301)



    # ── Context processors ────────────────────────────────────────────────────
    from app.context_processors import register_context_processors
    register_context_processors(app)

    # ── Subscription expiration middleware ─────────────────────────────────────
    from app.utils import refresh_current_subscription
    app.before_request(refresh_current_subscription)

    # ── v3.7 Tenant/Session integrity guard ──────────────────────────────────
    # Validates HMAC session signature and tenant/user consistency on every
    # authenticated request. Logs and forces re-auth on any mismatch.
    from flask_login import logout_user as _logout_user
    from flask import flash as _flash, redirect as _redirect, url_for as _url_for

    @app.before_request
    def tenant_guard():
        """Per-request tenant/session integrity check (v3.7)."""
        result = TenantGuard.validate()
        if result is not None:
            issue, _severity = result
            _logout_user()
            from flask import session as _session
            _session.clear()
            _flash(issue, 'danger')
            try:
                return _redirect(_url_for('auth.login'))
            except Exception:
                return _redirect('/')

    # ── Security headers ──────────────────────────────────────────────────────
    @app.after_request
    def set_security_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-XSS-Protection']       = '1; mode=block'
        response.headers['Referrer-Policy']        = 'strict-origin-when-cross-origin'
        response.headers['Permissions-Policy']     = 'geolocation=(), microphone=(), camera=()'

        return response
    
    # ── Health endpoint ───────────────────────────────────────────────────────
    # NOTE: /health is registered by the heartbeat blueprint (app/heartbeat/__init__.py)
    # and checks core DB, tenant DB, and Redis. Do NOT add another /health here.

    # ── Error handlers ────────────────────────────────────────────────────────
    @app.errorhandler(404)
    def not_found(e):
        return render_template('errors/404.html'), 404

    @app.errorhandler(413)
    def request_entity_too_large(e):
        return render_template('errors/413.html'), 413

    @app.errorhandler(429)
    def rate_limit_exceeded(e):
        return render_template('errors/429.html'), 429

    @app.errorhandler(500)
    def internal_error(e):
        db.session.rollback()
        # Log full traceback server-side but NEVER expose to client
        logger.exception('Internal server error: %s', e)
        if _request_wants_json():
            from flask import jsonify as _jsonify
            return _jsonify(status='error', message='An internal error occurred.'), 500
        return render_template('errors/500.html'), 500

    @app.errorhandler(Exception)
    def handle_unhandled_exception(e):
        """
        Catch-all for unhandled exceptions in production.
        Prevents raw Python tracebacks from reaching the client.
        DB session is rolled back defensively.
        """
        from werkzeug.exceptions import HTTPException
        if isinstance(e, HTTPException):
            return e   # Let Werkzeug handle standard HTTP errors normally
        db.session.rollback()
        logger.exception('Unhandled exception: %s', e)
        if _request_wants_json():
            from flask import jsonify as _jsonify
            return _jsonify(status='error', message='An unexpected error occurred.'), 500
        if not app.debug:
            return render_template('errors/500.html'), 500
        raise  # Re-raise in debug mode so Werkzeug debugger works

    register_cli_commands(app)
    
    # ── Startup Diagnostics (TASK 9) ──────────────────────────────────────────────
    # Log application startup status for Render/production debugging
    logger.info('=' * 80)
    logger.info('APPLICATION STARTUP DIAGNOSTICS')
    logger.info('=' * 80)
    logger.info(f'Environment: {app.config.get("ENV", "unknown")}')
    logger.info(f'Debug Mode: {app.debug}')
    logger.info(f'Testing Mode: {app.testing}')

    # SECURITY: warn loudly if debug mode is on in a non-dev environment
    if app.debug and not app.testing:
        env_name = app.config.get('ENV', '')
        if env_name not in ('development', 'dev', ''):
            logger.critical(
                'SECURITY WARNING: DEBUG=True in env=%r — '
                'NEVER run debug mode in production (exposes stack traces and Werkzeug console)',
                env_name,
            )

    # MailerSend check (shared + per-portal)
    try:
        from app.services.mailersend_service import get_mailersend_key
        shared_key = get_mailersend_key()
        if shared_key:
            logger.info('✓ MailerSend API (shared): Configured')
        else:
            logger.warning('⚠ MailerSend API (shared): Not configured — falling back to SMTP')

        # Per-portal key check
        import os as _os
        for _portal in ('superadmin', 'admin'):
            _env_key = _os.environ.get(f'{_portal.upper()}_MAILERSEND_API_KEY', '')
            if _env_key:
                logger.info('✓ MailerSend API (%s portal): Separate key configured', _portal)
            else:
                logger.info('  MailerSend API (%s portal): Using shared key', _portal)
    except Exception as e:
        logger.warning('⚠ MailerSend check failed: %s', e)

    # SMTP fallback check
    try:
        from app.services.email_service import _smtp_enabled, _smtp_is_configured
        if _smtp_enabled():
            if _smtp_is_configured():
                logger.info('✓ SMTP fallback: Enabled and configured')
            else:
                logger.warning('⚠ SMTP fallback: SMTP_ENABLED=true but configuration is incomplete')
        else:
            logger.info('  SMTP fallback: Disabled (SMTP_ENABLED not set)')
    except Exception as e:
        logger.warning('⚠ SMTP fallback check failed: %s', e)

    # ADMIN_EMAIL check (critical for default tenant contact delivery)
    _admin_email = _os.environ.get('ADMIN_EMAIL', '').strip()
    if _admin_email:
        logger.info('✓ ADMIN_EMAIL: Set (%s**)', _admin_email[:3])
    else:
        logger.warning(
            '⚠ ADMIN_EMAIL: Not set — default tenant contact form will attempt '
            'to resolve via admin user account (OK if admin user exists)'
        )

    # Blueprint registration
    logger.info('✓ Blueprints registered: %d', len(app.blueprints))
    for bp_name in app.blueprints:
        logger.debug('  - %s', bp_name)

    # Routes
    routes_count = sum(1 for _ in app.url_map.iter_rules())
    logger.info('✓ Routes loaded: %d routes', routes_count)
    
    logger.info('=' * 80)
    logger.info('APPLICATION STARTUP COMPLETED SUCCESSFULLY')
    logger.info('=' * 80)

    @app.errorhandler(CSRFError)
    def handle_csrf_error(e):
        from flask import request
        app.logger.error(
            f"CSRF failed: {e.description} "
            f"host={request.host} "
            f"referrer={request.referrer}"
        )

        return {
            "error": "CSRF validation failed",
            "reason": e.description
        }, 400
    
    return app


# ── Default tenant portfolio renderer ─────────────────────────────────────────

def _ensure_profile_columns():
    """
    Repair missing SQLite profile columns when the live database is out of
    sync with the current SQLAlchemy model.

    This is a safe fallback for development and local testing when a
    migration has not yet been applied. In production, prefer running
    `flask db upgrade` and keeping schema migrations in sync.
    """
    from app.models.portfolio import Profile
    from sqlalchemy import inspect

    if db.engine.dialect.name != 'sqlite':
        return

    if not inspect(db.engine).has_table(Profile.__tablename__):
        return

    expected_columns = {
        'free_trial_days': 'INTEGER NOT NULL DEFAULT 0',
        'free_trial_ends': 'DATETIME',
        # License fields removed: licensing now handled via subscriptions table
        'internal_notes': "TEXT NOT NULL DEFAULT ''",
        'meta_title': "VARCHAR(200) NOT NULL DEFAULT ''",
        'meta_description': "VARCHAR(300) NOT NULL DEFAULT ''",
        'og_image': "VARCHAR(255) NOT NULL DEFAULT ''",
    }

    added = False

    with db.engine.begin() as conn:
        import sqlite3
        from sqlalchemy.exc import OperationalError as SAOperationalError

        def execute_with_retry(statement, *args, retries=5, delay=0.1, **kwargs):
            last_exc = None
            for attempt in range(retries):
                try:
                    return conn.execute(statement, *args, **kwargs)
                except SAOperationalError as exc:
                    if isinstance(exc.orig, sqlite3.OperationalError) and 'database is locked' in str(exc.orig).lower():
                        last_exc = exc
                        time.sleep(delay * (attempt + 1))
                        continue
                    raise
            raise last_exc

        existing_columns = {
            row['name']
            for row in execute_with_retry(db.text("PRAGMA table_info(profile)")).mappings()
        }

        if 'tenant_id' not in existing_columns:
            execute_with_retry(db.text('ALTER TABLE profile ADD COLUMN tenant_id INTEGER'))
            logger.info('Added missing profile column: tenant_id')
            added = True

        if 'tenant_slug' not in existing_columns:
            execute_with_retry(
                db.text("ALTER TABLE profile ADD COLUMN tenant_slug VARCHAR(120) NOT NULL DEFAULT 'default'")
            )
            logger.info('Added missing profile column: tenant_slug')
            added = True

            if inspect(db.engine).has_table('tenants'):
                execute_with_retry(
                    db.text(
                        'UPDATE profile '
                        'SET tenant_slug = (SELECT slug FROM tenants WHERE tenants.id = profile.tenant_id) '
                        'WHERE tenant_id IS NOT NULL'
                    )
                )
                logger.info('Backfilled profile.tenant_slug from profile.tenant_id')

        if added:
            existing_columns = {
                row['name']
                for row in execute_with_retry(db.text("PRAGMA table_info(profile)")).mappings()
            }

        tenant_scoped_tables = ['users', 'projects', 'skills', 'testimonials', 'activity_log', 'inquiries']
        for table in tenant_scoped_tables:
            if inspect(db.engine).has_table(table):
                table_columns = {
                    row['name']
                    for row in execute_with_retry(db.text(f'PRAGMA table_info({table})')).mappings()
                }
                if 'tenant_id' not in table_columns:
                    execute_with_retry(db.text(f'ALTER TABLE {table} ADD COLUMN tenant_id INTEGER'))
                    logger.info('Added missing %s.tenant_id column', table)
                    added = True
                    table_columns.add('tenant_id')

                if 'tenant_slug' not in table_columns:
                    execute_with_retry(
                        db.text(
                            f"ALTER TABLE {table} ADD COLUMN tenant_slug VARCHAR(120) NOT NULL DEFAULT 'default'"
                        )
                    )
                    logger.info('Added missing %s.tenant_slug column', table)
                    added = True
                    if 'tenant_id' in table_columns and inspect(db.engine).has_table('tenants'):
                        execute_with_retry(
                            db.text(
                                'UPDATE ' + table + ' '
                                'SET tenant_slug = (SELECT slug FROM tenants WHERE tenants.id = ' + table + '.tenant_id) '
                                'WHERE tenant_id IS NOT NULL'
                            )
                        )
                        logger.info('Backfilled %s.tenant_slug from %s.tenant_id', table, table)

                if table == 'users':
                    expected_users_columns = {
                        'last_login_ip': 'VARCHAR(45)',
                        'totp_secret': 'VARCHAR(64)',
                        'totp_enabled': 'BOOLEAN NOT NULL DEFAULT 0',
                        'totp_backup_codes': 'TEXT',
                        # TOTP replay prevention additions (from migration 0010)
                        'last_totp_verified_at': 'DATETIME',
                        'last_totp_code_hash': 'VARCHAR(64)',
                        # Self-service password reset columns (from migration 0010)
                        'password_reset_token': "VARCHAR(100)",
                        'password_reset_expires': 'DATETIME',
                        'failed_login_attempts': 'INTEGER NOT NULL DEFAULT 0',
                        'last_failed_login_at': 'DATETIME',
                        'require_password_reset': 'BOOLEAN NOT NULL DEFAULT 0',
                        'last_password_changed': 'DATETIME',
                        'session_token': 'VARCHAR(255)',
                    }
                    for column_name, ddl in expected_users_columns.items():
                        if column_name not in table_columns:
                            execute_with_retry(
                                db.text(f'ALTER TABLE users ADD COLUMN "{column_name}" {ddl}')
                            )
                            logger.info('Added missing users column: %s', column_name)
                            added = True

        if inspect(db.engine).has_table('tenants'):
            rows = execute_with_retry(
                db.text('SELECT DISTINCT tenant_slug FROM profile')
            ).mappings()
            tenant_slugs = [row['tenant_slug'] or 'default' for row in rows]

            existing_tenants = {
                row['slug']
                for row in execute_with_retry(db.text('SELECT slug FROM tenants')).mappings()
            }

            for slug in sorted(set(tenant_slugs)):
                if slug in existing_tenants:
                    continue
                display_name = 'Default Portfolio' if slug == 'default' else slug.replace('-', ' ').title()
                email = 'hello@example.com' if slug == 'default' else 'hello@example.com'
                execute_with_retry(
                    db.text(
                        'INSERT INTO tenants (slug, company_name, email, status, plan, created_at, updated_at) '
                        'VALUES (:slug, :company_name, :email, :status, :plan, datetime("now"), datetime("now"))'
                    ),
                    {
                        'slug': slug,
                        'company_name': display_name,
                        'email': email,
                        'status': 'active',
                        'plan': 'Basic',
                    },
                )
                logger.info('Created missing tenant row for slug: %s', slug)

            execute_with_retry(
                db.text(
                    'UPDATE profile '
                    'SET tenant_id = (SELECT id FROM tenants WHERE tenants.slug = profile.tenant_slug) '
                    'WHERE tenant_id IS NULL'
                )
            )

            for table in tenant_scoped_tables:
                if inspect(db.engine).has_table(table):
                    execute_with_retry(
                        db.text(
                            'UPDATE ' + table + ' '
                            'SET tenant_id = (SELECT id FROM tenants WHERE tenants.slug = ' + table + '.tenant_slug) '
                            'WHERE tenant_id IS NULL'
                        )
                    )

        for column_name, ddl in expected_columns.items():
            if column_name not in existing_columns:
                conn.execute(
                    db.text(f'ALTER TABLE profile ADD COLUMN "{column_name}" {ddl}')
                )
                logger.info('Added missing profile column: %s', column_name)
                added = True

    if added:
        logger.info(
            'Profile schema repaired automatically. '
            'Run "flask db-upgrade" or "flask db upgrade" to persist migrations.'
        )


def _render_default_portfolio():
    """
    Render the 'default' tenant portfolio at root /.
    Mirrors tenant_bp.portfolio() but scoped to 'default' without requiring
    a URL slug prefix. Sets g.tenant_slug so context_processors work correctly.
    """
    from flask import render_template
    from sqlalchemy import or_
    from app.models.portfolio import Profile, Project, Skill, Testimonial, Service

    TENANT = 'default'
    g.tenant_slug = TENANT

    profile = Profile.query.filter_by(tenant_slug=TENANT).first()
    if not profile:
        # Graceful fallback: show a setup page instead of 500
        return render_template('errors/setup_needed.html'), 503

    all_projects = Project.published_for_tenant(TENANT).all()
    featured_projects = [p for p in all_projects if p.is_featured]
    other_projects = [p for p in all_projects if not p.is_featured]
    skills = (
        Skill.query
        .filter(
            Skill.tenant_slug == TENANT,
            or_(Skill.is_visible == True, Skill.is_visible.is_(None)),
        )
        .order_by(Skill.category.asc(), Skill.order.asc())
        .all()
    )
    testimonials = (
        Testimonial.query
        .filter_by(is_visible=True, tenant_slug=TENANT)
        .order_by(Testimonial.order.asc())
        .all()
    )
    services = (
        Service.query
        .filter_by(is_visible=True, tenant_slug=TENANT)
        .order_by(Service.display_order.asc(), Service.id.asc())
        .all()
    )

    skills_by_category = {}
    for skill in skills:
        skills_by_category.setdefault(skill.category, []).append(skill)

    categories = sorted({p.category for p in featured_projects + other_projects if p.category})

    stats = {
        'projects_count':   Project.query.filter_by(status='published', tenant_slug=TENANT).count(),
        'years_experience': profile.get_years_experience() if profile else 0,
        'clients_count':    profile.clients_count if profile else 0,
    }

    # Contact URL: use the root contact endpoint
    contact_url = '/contact'

    return render_template(
        'main/index.html',
        profile=profile,
        featured_projects=featured_projects,
        other_projects=other_projects,
        skills=skills,
        skills_by_category=skills_by_category,
        testimonials=testimonials,
        services=services,
        stats=stats,
        categories=categories,
        tenant_slug=TENANT,
        contact_url=contact_url,
        is_root_domain=True,  # Template flag: disables tenant-slug links
        trial_days_left=profile.trial_days_remaining() if profile else 0,
        license_status=profile.license_status() if profile else 'unlicensed',
    )



# ── Default tenant bootstrap ──────────────────────────────────────────────────

def _ensure_default_tenant():
    """
    Guarantee that a 'default' Profile row exists.
    Called at startup in dev/test. In production, run:
        flask ensure-default-tenant
    """
    from app.models.portfolio import Profile, Tenant
    from flask import current_app
    from sqlalchemy import inspect

    try:
        inspector = inspect(db.engine)
        if not inspector.has_table(Profile.__tablename__) or not inspector.has_table(Tenant.__tablename__):
            if current_app.testing or current_app.debug or db.engine.dialect.name == 'sqlite':
                db.create_all()
                logger.info('Created missing database tables at startup')
            else:
                raise RuntimeError(
                    'Database schema is missing. Run "flask db upgrade" or "flask init-db" before starting the app.'
                )

        tenant = Tenant.query.filter_by(slug='default').first()
        if not tenant:
            tenant = Tenant(
                slug='default',
                company_name='Default Portfolio',
                email='hello@example.com',
                status='active',
                plan='Basic',
            )
            db.session.add(tenant)
            db.session.flush()

        if not Profile.query.filter_by(tenant_slug='default').first():
            profile = Profile(
                tenant_id=tenant.id,
                tenant_slug=tenant.slug,
                name='Portfolio Owner',
                title='Full Stack Developer',
                subtitle='Building beautiful digital experiences',
                bio='Welcome to my portfolio.',
                email='hello@example.com',
            )
            profile.social_links = {}
            db.session.add(profile)
            db.session.commit()
            logger.info("Created default tenant Profile row")

        # Obj #2: bootstrap TenantFormSettings for the default tenant so the
        # contact form routes to the administrator's email out of the box.
        try:
            from app.services.contact_service import bootstrap_default_tenant_form_settings
            bootstrap_default_tenant_form_settings(db)
        except Exception as exc:
            logger.warning("Could not bootstrap default tenant form settings: %s", exc)
    except Exception as exc:
        db.session.rollback()
        logger.warning("Could not ensure default tenant: %s", exc)


# ── CLI commands ──────────────────────────────────────────────────────────────
# HIGH-03 FIX: ALL CLI commands are registered via register_cli_commands() so
# they are available regardless of entry point (run.py, wsgi.py, gunicorn).
# run.py previously registered several commands on its own app instance, which
# meant they were invisible when FLASK_APP=wsgi.py (used in production/Render).
import click


def register_cli_commands(app):
    """Register all Flask CLI commands on the app instance."""

    @app.cli.command('run-renewal-check')
    def cli_run_renewal_check():
        """Manually trigger the subscription renewal check job."""
        from app.services.renewal_scheduler import run_renewal_check
        click.echo('[CLI] Running renewal check...')
        run_renewal_check(app)
        click.echo('[CLI] Done.')

    @app.cli.command('ensure-default-tenant')
    def cli_ensure_default_tenant():
        """
        Create/repair the 'default' tenant and Profile record.
        Safe to run multiple times. Used as render.yaml preDeployCommand.
        """
        from app.models import User
        from app.models.portfolio import Tenant, Profile

        tenant = Tenant.query.filter_by(slug='default').first()
        if not tenant:
            tenant = Tenant(
                slug='default',
                company_name='Default Portfolio',
                email='hello@example.com',
                status='active',
                plan='Basic',
            )
            db.session.add(tenant)
            db.session.flush()

        if not Profile.query.filter_by(tenant_slug='default').first():
            profile = Profile(
                tenant_id=tenant.id,
                tenant_slug=tenant.slug,
                name='Portfolio Owner',
                title='Full Stack Developer',
                subtitle='Building beautiful digital experiences',
                bio='Welcome to my portfolio. Edit this via /admin/',
                bio_short='Full-stack developer focused on clean design.',
                location='Remote',
                email='hello@example.com',
                years_experience=5,
                experience_start_year=2019,
                clients_count=10,
                hero_tagline='Crafting elegant web experiences.',
                availability_status='Available for new work',
                is_available=True,
            )
            profile.social_links = {
                'github':   'https://github.com/yourusername',
                'linkedin': 'https://linkedin.com/in/yourusername',
            }
            db.session.add(profile)
            click.echo('✔  Created default tenant Profile')
        else:
            click.echo('✔  Default tenant Profile already exists')

        admin_user = User.query.filter_by(username='admin').first()
        if admin_user and admin_user.tenant_slug != 'default':
            admin_user.tenant     = tenant
            admin_user.tenant_slug = tenant.slug
            db.session.add(admin_user)
            click.echo('✔  Updated admin user tenant → default')

        db.session.commit()
        click.echo('   → Access admin at /admin/ after logging in at /auth/login')

        # Obj #2: Bootstrap TenantFormSettings so default portfolio contact
        # form automatically delivers to the admin's email address.
        try:
            from app.services.contact_service import bootstrap_default_tenant_form_settings
            bootstrap_default_tenant_form_settings(db)
            click.echo('✔  Default tenant form settings bootstrapped (email_only → admin email)')
        except Exception as exc:
            click.echo(f'⚠  Could not bootstrap form settings (non-fatal): {exc}')

    @app.cli.command('check-contact-config')
    def cli_check_contact_config():
        """
        Verify contact form delivery is correctly configured for all tenants.
        Run this after deployment to confirm email routing is working.

        Exit code 0 = all OK, 1 = warnings/errors found.
        """
        import os
        from app.models.portfolio import Tenant
        from app.models.tenant_form_settings import TenantFormSettings
        from app.services.contact_service import _resolve_receiver_email

        issues = []
        tenants = Tenant.query.filter_by(status='active').all()
        click.echo(f'\n📋 Contact Configuration Check — {len(tenants)} active tenant(s)\n')
        click.echo('─' * 60)

        for t in tenants:
            settings = TenantFormSettings.for_tenant(t.id)
            provider = settings.provider if settings else 'none'
            enabled  = settings.is_enabled if settings else False
            configured = settings.is_configured if settings else False
            receiver = _resolve_receiver_email(t.slug, t, settings)

            status_icon = '✓' if (enabled and configured and receiver) else '⚠'
            click.echo(f'{status_icon}  Tenant: {t.slug}')
            click.echo(f'    Provider : {provider}')
            click.echo(f'    Enabled  : {enabled}')
            click.echo(f'    Configured: {configured}')
            click.echo(f'    Receiver : {receiver[:3] + "**@" + receiver.split("@")[1] if receiver and "@" in receiver else "(NONE — fallback to inbox)"}')

            if not receiver:
                issues.append(f'  [{t.slug}] No receiver_email — submissions save to inbox only')
            if enabled and not configured:
                issues.append(f'  [{t.slug}] Provider {provider!r} enabled but not fully configured')
            click.echo()

        click.echo('─' * 60)

        # Global checks
        admin_email = os.environ.get('ADMIN_EMAIL', '')
        if admin_email:
            click.echo(f'✓  ADMIN_EMAIL env: {admin_email[:3]}**')
        else:
            click.echo('⚠  ADMIN_EMAIL env: Not set')
            issues.append('ADMIN_EMAIL env var not set — last-resort fallback unavailable')

        try:
            from app.services.mailersend_service import get_mailersend_key
            if get_mailersend_key():
                click.echo('✓  MailerSend API key: Configured')
            else:
                click.echo('⚠  MailerSend API key: Not configured')
                issues.append('MAILERSEND_API_KEY not set — email delivery will fall back to SMTP')
        except Exception as e:
            click.echo(f'⚠  MailerSend check failed: {e}')

        try:
            from app.services.email_service import _smtp_enabled, _smtp_is_configured
            if _smtp_enabled():
                if _smtp_is_configured():
                    click.echo('✓  SMTP fallback: Configured')
                else:
                    click.echo('⚠  SMTP fallback: SMTP_ENABLED=true but incomplete config')
                    issues.append('SMTP enabled but not fully configured')
            else:
                click.echo('  SMTP fallback: Disabled')
        except Exception as e:
            click.echo(f'  SMTP check failed: {e}')

        click.echo('\n' + '─' * 60)
        if issues:
            click.echo(f'\n⚠  {len(issues)} issue(s) found:\n')
            for iss in issues:
                click.echo(f'  • {iss}')
            click.echo()
            raise SystemExit(1)
        else:
            click.echo('\n✓  All contact form configurations look healthy.\n')

    @app.cli.command('init-db')
    def cli_init_db():
        """Create all tables and seed default admin + empty profile."""
        import secrets as _secrets
        from app.models import User
        from app.models.portfolio import Tenant, Profile

        if app.config["ENV"] == "development":
            with app.app_context():
                db.create_all()

        tenant = Tenant.query.filter_by(slug='default').first()
        if not tenant:
            tenant = Tenant(
                slug='default', company_name='Default Portfolio',
                email='admin@portfolio.local', status='active', plan='Basic',
            )
            db.session.add(tenant)
            db.session.flush()

        if not User.query.filter_by(username='admin').first():
            temp_password = _secrets.token_urlsafe(12)
            admin = User(
                username='admin',
                email='admin@portfolio.local',
                tenant_slug='default',
                tenant=tenant,
                is_admin=True,
            )
            admin.password = temp_password
            db.session.add(admin)
            click.echo('✔  Created admin user')
            click.echo(f'   Username: admin')
            click.echo(f'   Temporary password: {temp_password}')
            click.echo('⚠️  Change this password immediately after first login!')

        if not Profile.query.filter_by(tenant_slug='default').first():
            profile = Profile(
                tenant_id=tenant.id,
                tenant_slug=tenant.slug,
                name='Your Name',
                title='Full Stack Developer',
                subtitle='Building beautiful digital experiences',
                bio='I help clients build modern, scalable web applications.',
                bio_short='Experienced full-stack developer focused on clean design.',
                location='Remote',
                email='hello@portfolio.local',
                years_experience=5,
                experience_start_year=2019,
                clients_count=12,
                hero_tagline='Crafting elegant web experiences.',
                availability_status='Available for new work',
                is_available=True,
            )
            profile.social_links = {
                'github':   'https://github.com/yourusername',
                'linkedin': 'https://linkedin.com/in/yourusername',
            }
            db.session.add(profile)

        db.session.commit()
        click.echo('✔  Database initialised.')
        click.echo('   → Portfolio at: /')
        click.echo('   → Admin panel at: /admin/')
        click.echo('   → Login at: /auth/login')

    @app.cli.command('create-superadmin')
    def cli_create_superadmin():
        """Create or reset the superadmin account."""
        import secrets as _secrets
        import os as _os
        from app.models import User
        from app.models.portfolio import Tenant

        password = _os.environ.get('SUPERADMIN_PASSWORD', _secrets.token_urlsafe(16))
        existing = User.query.filter_by(username='superadmin').first()

        tenant = Tenant.query.filter_by(slug='default').first()
        if not tenant:
            tenant = Tenant(
                slug='default', company_name='Default Portfolio',
                email='superadmin@portfolio.local', status='active', plan='Basic',
            )
            db.session.add(tenant)
            db.session.flush()

        if existing:
            existing.password      = password
            existing.is_superadmin = True
            existing.is_admin      = True
            existing.tenant        = tenant
            existing.tenant_slug   = tenant.slug
            db.session.commit()
            click.echo('✔  Superadmin already exists — password reset:')
            click.echo(f'   Username: superadmin')
            click.echo(f'   New password: {password}')
            click.echo('   Login at: /superadmin/login')
            return

        superadmin = User(
            username='superadmin',
            email='superadmin@portfolio.local',
            tenant=tenant,
            tenant_slug=tenant.slug,
            is_admin=True,
            is_superadmin=True,
        )
        superadmin.password = password
        db.session.add(superadmin)
        db.session.commit()
        click.echo('✔  Superadmin created:')
        click.echo(f'   Username:  superadmin')
        click.echo(f'   Password:  {password}')
        click.echo('   Login URL: /superadmin/login')
        click.echo('⚠️  Change this password immediately after first login!')

    @app.cli.command('create-admin')
    def cli_create_admin():
        """Create the default admin user (if missing)."""
        import secrets as _secrets
        from app.models import User
        from app.models.portfolio import Tenant

        if User.query.filter_by(username='admin').first():
            click.echo('Admin user already exists. Use reset-admin-password to change password.')
            return
        temp_password = _secrets.token_urlsafe(12)
        tenant = Tenant.query.filter_by(slug='default').first()
        if not tenant:
            tenant = Tenant(
                slug='default', company_name='Default Portfolio',
                email='admin@portfolio.local', status='active', plan='Basic',
            )
            db.session.add(tenant)
            db.session.flush()
        u = User(
            username='admin',
            email='admin@portfolio.local',
            tenant=tenant,
            tenant_slug=tenant.slug,
            is_admin=True,
        )
        u.password = temp_password
        db.session.add(u)
        db.session.commit()
        click.echo('✔  Created admin user:')
        click.echo(f'   Username: admin')
        click.echo(f'   Temporary password: {temp_password}')
        click.echo('   Login at: /auth/login')
        click.echo('⚠️  Change this password immediately after first login!')

    @app.cli.command('seed-sample-data')
    def cli_seed_sample_data():
        """Seed sample portfolio content for the default tenant."""
        from app.models.portfolio import Tenant, Profile, Skill, Project, Testimonial

        TENANT = 'default'
        tenant = Tenant.query.filter_by(slug=TENANT).first()
        if not tenant:
            tenant = Tenant(
                slug=TENANT, company_name='Default Portfolio',
                email='hello@example.com', status='active', plan='Basic',
            )
            db.session.add(tenant)
            db.session.flush()

        if not Skill.query.filter_by(tenant_slug=TENANT).first():
            for s in [
                {'name': 'Python',     'proficiency': 95, 'category': 'Backend',  'icon': '🐍', 'color': '#306998', 'order': 1},
                {'name': 'Flask',      'proficiency': 90, 'category': 'Backend',  'icon': '⚡', 'color': '#000000', 'order': 2},
                {'name': 'JavaScript', 'proficiency': 88, 'category': 'Frontend', 'icon': '✦',  'color': '#F7DF1E', 'order': 1},
                {'name': 'PostgreSQL', 'proficiency': 78, 'category': 'Database', 'icon': '🛢️', 'color': '#336791', 'order': 1},
                {'name': 'Docker',     'proficiency': 72, 'category': 'DevOps',   'icon': '🐳', 'color': '#2496ED', 'order': 1},
            ]:
                db.session.add(Skill(tenant_id=tenant.id, tenant_slug=TENANT, **s))

        if not Project.query.filter_by(tenant_slug=TENANT).first():
            for pd in [
                {
                    'title': 'Portfolio CMS', 'category': 'Web App',
                    'description': 'A Flask-based portfolio CMS with multi-tenant support.',
                    'description_short': 'Content-managed portfolio.',
                    'status': 'published', 'is_featured': True, 'order': 0,
                },
            ]:
                project = Project(tenant_id=tenant.id, tenant_slug=TENANT, **pd)
                project.tags = []
                base = project.generate_slug()
                slug, n = base, 1
                while Project.query.filter_by(slug=slug).first():
                    slug = f'{base}-{n}'; n += 1
                project.slug = slug
                db.session.add(project)

        db.session.commit()
        click.echo('✔  Sample data seeded for default tenant.')

    @app.cli.command('normalize-skill-visibility')
    def cli_normalize_skill_visibility():
        """Set NULL skill visibility values to True."""
        from app.models.portfolio import Skill
        count = db.session.query(Skill).filter(Skill.is_visible.is_(None)).count()
        if count == 0:
            click.echo('No skills with NULL is_visible found.')
            return
        db.session.query(Skill).filter(Skill.is_visible.is_(None)).update(
            {'is_visible': True}, synchronize_session='fetch'
        )
        db.session.commit()
        click.echo(f'✔  Updated {count} skill(s): NULL is_visible → True')

    @app.cli.command('db-upgrade')
    def cli_db_upgrade():
        """Run Alembic migrations against the configured database."""
        from flask_migrate import upgrade
        try:
            upgrade()
            click.echo('✔  Database migration completed successfully.')
        except Exception as exc:
            click.echo(f'✖  Database migration failed: {exc}')
            raise

    @app.cli.command('ensure-tenant-schema')
    def cli_ensure_tenant_schema():
        """
        ROOT-CAUSE FIX for: psycopg2.errors.UndefinedTable:
        relation "profile" does not exist

        AUDIT FINDING: migrations/env.py (used by `flask db upgrade` /
        the `db-upgrade` command above) resolves its target via
        app.utils.db_config.get_database_url() -- a SINGLE URL
        (DIRECT_DATABASE_URL / DATABASE_URL), which is the CORE database.
        render.yaml's preDeployCommand runs `flask db upgrade` exactly
        once, against that single URL. Profile/Skill/Project/Testimonial/
        Service all declare __bind_key__ = 'tenant' and live on a
        PHYSICALLY SEPARATE database (TENANT_DATABASE_URL) that this
        migration chain never connects to -- so those tables are never
        created there. (migrations/core/env.py and migrations/tenant/env.py
        exist as an unfinished start on a real Flask-Migrate --multidb
        split, but have no versions/ directories and are not wired into
        alembic.ini's script_location, so they are never invoked.)

        This command is the immediate, low-risk hotfix: it creates ONLY
        the tenant-bound tables, directly against TENANT_DATABASE_URL,
        using SQLAlchemy's create_all() (idempotent -- CREATE TABLE IF
        NOT EXISTS semantics under checkfirst=True). It does NOT touch
        the core database or the existing 28-migration Alembic history,
        so it carries zero risk to already-applied core migrations.

        This is a structural workaround, not a long-term replacement for
        a real `flask db init --multidb` conversion (recommended as a
        separate, carefully-tested follow-up -- see AUDIT_REPORT).
        
        FLASK-SQLALCHEMY 3.x COMPATIBILITY FIX:
        Replaced db.engines['tenant'] with db.get_engine(bind_key='tenant')
        for Flask-SQLAlchemy 3.x compatibility. The db.engines dict is no
        longer exposed in Flask-SQLAlchemy 3.x; use get_engine() instead.
        """
        from app.models.tenant_data import (
            Profile,
            Skill,
            Project,
            Testimonial,
            Service
        )

        # Get the tenant database engine using Flask-SQLAlchemy 3.x compatible API
        tenant_engine = db.get_engine(bind_key='tenant')

        Profile.__table__.create(
            bind=tenant_engine,
            checkfirst=True
        )

        Skill.__table__.create(
            bind=tenant_engine,
            checkfirst=True
        )

        Project.__table__.create(
            bind=tenant_engine,
            checkfirst=True
        )

        Testimonial.__table__.create(
            bind=tenant_engine,
            checkfirst=True
        )

        Service.__table__.create(
            bind=tenant_engine,
            checkfirst=True
        )

        try:
            db.create_all(bind_key='tenant')
            click.echo('✔  Tenant-bound schema verified/created on TENANT_DATABASE_URL.')
        except Exception as exc:
            click.echo(f'✖  Tenant schema creation failed: {exc}')
            raise
