"""
config.py — Portfolio CMS v5.3 — Production-Ready Configuration (PATCHED)

FIXES APPLIED:
  [CRITICAL-1] BaseConfig SESSION_COOKIE_SECURE / REMEMBER_COOKIE_SECURE
               was set True then immediately overridden to False in the same
               class body. The duplicate assignments are removed; each
               environment class owns its own value — no silent override.

  [CRITICAL-2] ProductionConfig.init_app() required PAYMONGO_SECRET_KEY and
               PAYMONGO_WEBHOOK_SECRET unconditionally, crashing startup on
               any deployment where billing is disabled (PAYMONGO_ENABLED=false).
               They are now only required when PAYMONGO_ENABLED=true.

  [HIGH-1]     SUPERADMIN_EMAIL, SUPERADMIN_USERNAME env-var support added to
               allow full environment-variable driven superadmin bootstrapping.

Environment Variables Required:
  Production (always):
    - SECRET_KEY
    - FERNET_KEY
    - CORE_DATABASE_URL
    - TENANT_DATABASE_URL

  Production (only when PAYMONGO_ENABLED=true):
    - PAYMONGO_SECRET_KEY
    - PAYMONGO_WEBHOOK_SECRET

  Optional:
    - REDIS_URL
    - SENTRY_DSN
    - SUPERADMIN_USERNAME   (default: superadmin)
    - SUPERADMIN_EMAIL      (default: superadmin@portfolio.local)
    - SUPERADMIN_PASSWORD   (auto-generated and logged if absent)
"""

import os
import logging
from datetime import timedelta
from pathlib import Path
from urllib.parse import unquote
from dotenv import load_dotenv
from sqlalchemy.pool import NullPool

basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, '.env'))


def _normalize_postgres_url(url: str) -> str:
    """Normalize postgres:// → postgresql:// (Render/Heroku/Supabase quirk)."""
    if url and url.startswith('postgres://'):
        return url.replace('postgres://', 'postgresql://', 1)
    return url


class BaseConfig:
    """Base configuration with security defaults."""

    # ─────────────────────────────────────────────────────────────────
    # SECURITY
    # ─────────────────────────────────────────────────────────────────
    SECRET_KEY = os.environ.get('SECRET_KEY') or ''

    # FIX [CRITICAL-1]: Removed duplicate SESSION_COOKIE_SECURE = True / False
    # assignments that existed in the original BaseConfig class body.
    # Each subclass now owns exactly ONE assignment. BaseConfig sets the
    # permissive default (False = works on HTTP for dev/test). Production
    # overrides to True. This prevents any "set True, then immediately set
    # False" confusion.
    SESSION_COOKIE_SECURE   = False   # overridden per env — do NOT duplicate below
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    REMEMBER_COOKIE_SECURE   = False   # overridden per env — do NOT duplicate below
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_DURATION = timedelta(days=30)

    PERMANENT_SESSION_LIFETIME = timedelta(hours=24)

    # CSRF Protection
    WTF_CSRF_ENABLED       = True
    WTF_CSRF_TIME_LIMIT    = 3600   # 1 hour
    WTF_CSRF_CHECK_DEFAULT = True
    WTF_CSRF_SSL_STRICT    = True   # overridden to False in Dev/Test

    # ─────────────────────────────────────────────────────────────────
    # DATABASE — DUAL-DB ARCHITECTURE
    # ─────────────────────────────────────────────────────────────────
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_RECORD_QUERIES      = False
    SQLALCHEMY_SLOW_QUERY_THRESHOLD = 0.5

    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle":  300,
        "pool_size":     5,
        "max_overflow":  10,
    }

    # ─────────────────────────────────────────────────────────────────
    # AUTHENTICATION & ENCRYPTION
    # ─────────────────────────────────────────────────────────────────
    FERNET_KEY = os.environ.get('FERNET_KEY') or ''

    TOTP_ISSUER              = os.environ.get('TOTP_ISSUER', 'Portfolio CMS')
    TOTP_VALID_WINDOW        = int(os.environ.get('TOTP_VALID_WINDOW', '1'))
    OTP_EXPIRATION_SECONDS   = int(os.environ.get('OTP_EXPIRATION_SECONDS', '600'))
    OTP_MAX_ATTEMPTS         = int(os.environ.get('OTP_MAX_ATTEMPTS', '5'))
    # Post-OTP-verification reset token (short-lived bridge to the Set New
    # Password form) — separate from the OTP itself, which uses the
    # SuperAdmin-configured GlobalEmailConfig.otp_expiry_minutes.
    PASSWORD_RESET_EXPIRATION_MINUTES = int(os.environ.get('PASSWORD_RESET_EXPIRATION_MINUTES', '15'))

    # Password Policy
    MIN_PASSWORD_LENGTH    = 12
    REQUIRE_UPPERCASE      = True
    REQUIRE_NUMBERS        = True
    REQUIRE_SPECIAL_CHARS  = True

    # ─────────────────────────────────────────────────────────────────
    # SUPERADMIN BOOTSTRAP (env-var driven, never hardcoded)
    # ─────────────────────────────────────────────────────────────────
    # FIX [HIGH-1]: These drive the auto-bootstrap logic in create_app().
    # Set them as Render environment variables; never hardcode values here.
    SUPERADMIN_USERNAME = os.environ.get('SUPERADMIN_USERNAME', 'superadmin')
    SUPERADMIN_EMAIL    = os.environ.get('SUPERADMIN_EMAIL', 'superadmin@portfolio.local')
    # SUPERADMIN_PASSWORD is intentionally NOT in config — it is read directly
    # from os.environ inside cli_create_superadmin() and _auto_bootstrap_superadmin()
    # so it never lands in app.config (and therefore never in debug dumps).

    # ─────────────────────────────────────────────────────────────────
    # RATE LIMITING & CACHING
    # ─────────────────────────────────────────────────────────────────
    RATELIMIT_STORAGE_URL   = os.environ.get('REDIS_URL', 'memory://')
    RATELIMIT_DEFAULT       = '100 per hour'
    RATELIMIT_HEADERS_ENABLED = True

    RATELIMIT_LOGIN          = '5 per 15 minutes'
    RATELIMIT_REGISTER       = '3 per 30 minutes'
    RATELIMIT_PASSWORD_RESET = '3 per 30 minutes'
    RATELIMIT_OTP_SEND       = '3 per 30 minutes'
    RATELIMIT_OTP_VERIFY     = '5 per 15 minutes'
    RATELIMIT_CONTACT_FORM   = '5 per hour'
    RATELIMIT_WEBHOOKS       = '200 per minute'

    CACHE_TYPE            = 'SimpleCache'
    CACHE_DEFAULT_TIMEOUT = 300
    CACHE_REDIS_URL       = os.environ.get('REDIS_URL', '')

    # ─────────────────────────────────────────────────────────────────
    # FILE UPLOADS & STORAGE
    # ─────────────────────────────────────────────────────────────────
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'}
    ALLOWED_MIME_TYPES = {
        'image/png', 'image/jpeg', 'image/gif',
        'image/webp', 'image/svg+xml',
    }

    SUPABASE_URL        = os.environ.get('SUPABASE_URL', '')
    SUPABASE_KEY        = os.environ.get('SUPABASE_SERVICE_KEY', '')
    SUPABASE_BUCKET     = os.environ.get('SUPABASE_BUCKET', 'portfolio-media')
    USE_SUPABASE_STORAGE = os.environ.get('USE_SUPABASE_STORAGE', 'false').lower() == 'true'

    # ─────────────────────────────────────────────────────────────────
    # INTEGRATIONS
    # ─────────────────────────────────────────────────────────────────
    RESEND_API_KEY    = os.environ.get('RESEND_API_KEY', '')
    RESEND_FROM_EMAIL = os.environ.get('RESEND_FROM_EMAIL', '')

    PAYMONGO_ENABLED         = os.environ.get('PAYMONGO_ENABLED', 'false').lower() == 'true'
    PAYMONGO_PUBLIC_KEY      = os.environ.get('PAYMONGO_PUBLIC_KEY', '')
    PAYMONGO_SECRET_KEY      = os.environ.get('PAYMONGO_SECRET_KEY', '')
    PAYMONGO_WEBHOOK_SECRET  = os.environ.get('PAYMONGO_WEBHOOK_SECRET', '')

    WEB3FORMS_ACCESS_KEY     = os.environ.get('WEB3FORMS_ACCESS_KEY', '')
    # ADMIN_EMAIL: destination for the default/root-tenant contact form and
    # as a fallback notification address. Read via current_app.config in
    # app/main/__init__.py, app/utils/__init__.py, and app/models/core.py —
    # must be loaded here or those lookups always return None.
    ADMIN_EMAIL               = os.environ.get('ADMIN_EMAIL', '')
    SENTRY_DSN                = os.environ.get('SENTRY_DSN', '')
    BETTERSTACK_HEARTBEAT_URL = os.environ.get('BETTERSTACK_HEARTBEAT_URL', '')
    HEARTBEAT_SECRET         = os.environ.get('HEARTBEAT_SECRET', '')

    APP_BASE_URL             = os.environ.get('APP_BASE_URL', '').rstrip('/')
    BILLING_GRACE_PERIOD_DAYS = int(os.environ.get('BILLING_GRACE_PERIOD_DAYS', '3'))
    PAYMENT_TIMEOUT_SECONDS  = int(os.environ.get('PAYMENT_TIMEOUT_SECONDS', '600'))

    # ─────────────────────────────────────────────────────────────────
    # LOGGING
    # ─────────────────────────────────────────────────────────────────
    LOG_LEVEL  = os.environ.get('LOG_LEVEL', 'INFO')
    LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    LOG_DIR    = os.path.join(basedir, 'logs')

    SEND_FILE_MAX_AGE_DEFAULT = 31536000

    @staticmethod
    def init_app(app):
        """Initialize upload directories."""
        upload_base = os.path.join(basedir, 'static', 'uploads')
        for sub in ('profiles', 'projects', 'avatars', 'billing'):
            os.makedirs(os.path.join(upload_base, sub), exist_ok=True)
        os.makedirs(BaseConfig.LOG_DIR, exist_ok=True)

        app.config['UPLOAD_FOLDER']         = upload_base
        app.config['PROFILE_UPLOAD_FOLDER'] = os.path.join(upload_base, 'profiles')
        app.config['PROJECT_UPLOAD_FOLDER'] = os.path.join(upload_base, 'projects')
        app.config['AVATAR_UPLOAD_FOLDER']  = os.path.join(upload_base, 'avatars')


class DevelopmentConfig(BaseConfig):
    """Development environment configuration."""

    DEBUG   = True
    TESTING = False

    # Permissive for HTTP dev environment
    SESSION_COOKIE_SECURE  = False
    REMEMBER_COOKIE_SECURE = False

    SEND_FILE_MAX_AGE_DEFAULT = 0

    SQLALCHEMY_ECHO          = True
    SQLALCHEMY_RECORD_QUERIES = True
    LOG_LEVEL = 'DEBUG'

    WTF_CSRF_SSL_STRICT = False
    RATELIMIT_ENABLED   = False
    CACHE_TYPE          = 'SimpleCache'

    _core_db_file = Path(basedir) / 'storage' / 'portfolio_core_dev.db'
    _core_db_file.parent.mkdir(parents=True, exist_ok=True)
    _core_uri = _normalize_postgres_url(
        os.environ.get('DEV_CORE_DATABASE_URL', '')
    ) or f"sqlite:///{_core_db_file.resolve()}".replace('\\', '/')

    _tenant_db_file = Path(basedir) / 'storage' / 'portfolio_tenant_dev.db'
    _tenant_db_file.parent.mkdir(parents=True, exist_ok=True)
    _tenant_uri = _normalize_postgres_url(
        os.environ.get('DEV_TENANT_DATABASE_URL', '')
    ) or f"sqlite:///{_tenant_db_file.resolve()}".replace('\\', '/')

    SQLALCHEMY_DATABASE_URI = _core_uri
    SQLALCHEMY_BINDS        = {'tenant': _tenant_uri}

    SQLALCHEMY_ENGINE_OPTIONS = {'pool_pre_ping': True}


class ProductionConfig(BaseConfig):
    """Production environment configuration."""

    DEBUG   = False
    TESTING = False
    PROPAGATE_EXCEPTIONS = False   # SECURITY: never let raw exceptions propagate to WSGI layer

    PREFERRED_URL_SCHEME = "https"

    # FIX [CRITICAL-1]: Only one assignment per variable, in the correct class.
    SESSION_COOKIE_SECURE  = True
    REMEMBER_COOKIE_SECURE = True
    WTF_CSRF_SSL_STRICT    = True    # FIX MED-01: enforce CSRF token origin validation on HTTPS (ensure X-Forwarded-Proto is set by NGINX/Render)
    WTF_CSRF_ENABLED = True

    SQLALCHEMY_ECHO          = False
    SQLALCHEMY_RECORD_QUERIES = False
    LOG_LEVEL = 'WARNING'

    CACHE_TYPE = 'RedisCache' if os.environ.get('REDIS_URL') else 'SimpleCache'

    SQLALCHEMY_ENGINE_OPTIONS = {
        'poolclass':   NullPool,
        'pool_pre_ping': True,
        'connect_args': {
            'sslmode':          'require',
            'connect_timeout':  10,
            'options':          '-c statement_timeout=30000',
            'application_name': 'portfolio_cms_prod',
        },
    }

    @classmethod
    def _validate_engine_options(cls, app):
        options = app.config.get("SQLALCHEMY_ENGINE_OPTIONS", {})
        if isinstance(options, str):
            raise RuntimeError("SQLALCHEMY_ENGINE_OPTIONS must be dict, not string")
        if "poolclass" in options and isinstance(options["poolclass"], str):
            raise RuntimeError("poolclass must be SQLAlchemy class, not string")

    @classmethod
    def init_app(cls, app):
        """Initialize production configuration with validation."""
        BaseConfig.init_app(app)
        cls._validate_engine_options(app)

        # ─────────────────────────────────────────────────────────────
        # VALIDATE REQUIRED ENVIRONMENT VARIABLES
        # FIX [CRITICAL-2]: PAYMONGO vars are only required when billing
        # is enabled. A deployment with PAYMONGO_ENABLED=false no longer
        # crashes at startup due to missing keys.
        # ─────────────────────────────────────────────────────────────
        always_required = [
            'SECRET_KEY',
            'FERNET_KEY',
            'CORE_DATABASE_URL',
            'TENANT_DATABASE_URL',
        ]
        missing = [v for v in always_required if not os.environ.get(v)]

        # Billing keys only required when PayMongo is explicitly enabled.
        paymongo_enabled = os.environ.get('PAYMONGO_ENABLED', 'false').lower() == 'true'
        if paymongo_enabled:
            billing_required = ['PAYMONGO_SECRET_KEY', 'PAYMONGO_WEBHOOK_SECRET']
            missing += [v for v in billing_required if not os.environ.get(v)]

        if missing:
            raise ValueError(
                f"Production environment missing required variables: {', '.join(missing)}\n"
                "Configure these in your hosting platform's environment settings."
            )

        if not os.environ.get("REDIS_URL"):
            app.logger.warning(
                "REDIS_URL not set — rate limiting and caching will use "
                "in-process fallbacks (not multi-worker safe)."
            )

        # ─────────────────────────────────────────────────────────────
        # CONFIGURE DATABASES
        # ─────────────────────────────────────────────────────────────
        core_url   = _normalize_postgres_url(os.environ['CORE_DATABASE_URL'].strip())
        tenant_url = _normalize_postgres_url(os.environ['TENANT_DATABASE_URL'].strip())

        app.config['SQLALCHEMY_DATABASE_URI'] = core_url
        app.config['SQLALCHEMY_BINDS']        = {'tenant': tenant_url}

        # ─────────────────────────────────────────────────────────────
        # CONFIGURE SENTRY
        # ─────────────────────────────────────────────────────────────
        if app.config.get('SENTRY_DSN'):
            try:
                import sentry_sdk
                from sentry_sdk.integrations.flask import FlaskIntegration
                sentry_sdk.init(
                    dsn=app.config['SENTRY_DSN'],
                    integrations=[FlaskIntegration()],
                    traces_sample_rate=0.1,
                    environment='production',
                )
            except ImportError:
                app.logger.warning('sentry-sdk not installed; skipping Sentry')

        # ─────────────────────────────────────────────────────────────
        # PRODUCTION LOGGING
        # ─────────────────────────────────────────────────────────────
        handler = logging.StreamHandler()
        handler.setLevel(logging.WARNING)
        handler.setFormatter(logging.Formatter(BaseConfig.LOG_FORMAT))
        app.logger.addHandler(handler)


class TestingConfig(BaseConfig):
    """Testing environment configuration."""

    TESTING = True
    DEBUG   = False

    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    SQLALCHEMY_BINDS        = {'tenant': 'sqlite:///:memory:'}
    SQLALCHEMY_ENGINE_OPTIONS = {}

    WTF_CSRF_ENABLED       = False
    RATELIMIT_ENABLED      = False
    SESSION_COOKIE_SECURE  = False
    REMEMBER_COOKIE_SECURE = False

    CACHE_TYPE = 'NullCache'

    SECRET_KEY  = 'test-secret-key-not-for-production'
    FERNET_KEY  = b'test-fernet-key-not-for-production-_-1234567890ab'


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION REGISTRY
# ─────────────────────────────────────────────────────────────────────────────
config = {
    'development': DevelopmentConfig,
    'production':  ProductionConfig,
    'testing':     TestingConfig,
    'default':     DevelopmentConfig,
}


def get_config(env=None):
    """Get configuration object by environment name."""
    if env is None:
        env = os.environ.get('FLASK_ENV', 'development')
    return config.get(env, config['default'])
