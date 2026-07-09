"""
app/extensions.py — Centralized Flask extension singletons.

PHASE 1 REFACTOR (non-destructive):
This module is the single source of truth for extension *instances*.
It does NOT call .init_app() — that still happens in app/__init__.py's
create_app(), in the exact same order as before, with the exact same
config-resolution logic (Redis preflight, etc.) untouched.

Extracted verbatim from app/__init__.py with zero behavior change:
  - db, login_manager, csrf, migrate, cache, limiter singletons
  - create_limiter_key_func wiring
  - resolve_limiter_storage_uri() (Redis preflight + memory:// fallback)

BACKWARD COMPATIBILITY:
app/__init__.py re-exports everything from this module at the same
names (`db`, `login_manager`, etc.), so every existing call site doing
`from app import db` / `from app import limiter` continues to work
unmodified. 48 call sites across the codebase depend on this — do not
remove the re-export in app/__init__.py without updating all of them
first.
"""

import logging
import os

from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from flask_migrate import Migrate
from flask_limiter import Limiter
from flask_caching import Cache
from authlib.integrations.flask_client import OAuth

from app.limiter_config import create_limiter_key_func

logger = logging.getLogger(__name__)

# ── Extension singletons ──────────────────────────────────────────────────
# Instantiated here, .init_app()'d in app/__init__.py:create_app().
db            = SQLAlchemy()
login_manager = LoginManager()
csrf          = CSRFProtect()
migrate       = Migrate()
cache         = Cache()

# Google OAuth (Authlib). Registered conditionally in create_app() only when
# GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are present — see app/__init__.py.
# This is a SECOND LOGIN METHOD for existing tenant-provisioned users only.
# It does NOT create tenants, users, or subscriptions. See app/auth/oauth.py.
oauth = OAuth()

# Limiter must be a real object at module level because blueprints use
# @limiter.limit(...) as decorators at import time.
# We construct it with key_func here, then call init_app(app) inside
# create_app() so storage_uri (from config/REDIS_URL) is applied correctly.
#
# NOTE: storage_uri is intentionally NOT passed here. flask-limiter's
# constructor argument wins over app.config['RATELIMIT_STORAGE_URI'] if
# set, which would make the pre-flight-checked, fallback-aware value
# computed by resolve_limiter_storage_uri() (called in create_app())
# impossible to apply without poking the library's private _storage_uri
# attribute. Leaving it unset here means create_app() -> RATELIMIT_STORAGE_URI
# is the single source of truth, via the documented config key.
limiter = Limiter(
    key_func=create_limiter_key_func,
    default_limits=["800 per hour"],
    headers_enabled=True,
)


def resolve_limiter_storage_uri(app) -> str:
    """
    Resolve the storage backend for Flask-Limiter.

    Order of precedence: RATELIMIT_STORAGE_URL (config) -> REDIS_URL (env)
    -> memory://. If a redis:// URL is configured, PING it with a short
    timeout; on ANY failure (DNS, connection refused, auth, timeout) log
    a warning and fall back to memory:// rather than letting the app
    crash on the first request that touches a rate-limited route.

    FIX (redis-graceful-degradation): previously app/__init__.py
    constructed Limiter() with whatever RATELIMIT_STORAGE_URL/REDIS_URL
    pointed to, then create_app() mutated the PRIVATE `limiter._storage_uri`
    attribute and called init_app(). Neither step ever attempted a real
    connection, so a dead/unresolvable Redis host (e.g. a Render Redis
    instance that was deleted/renamed) was only discovered when
    flask-limiter's storage backend tried to actually talk to Redis on
    the first rate-limited request -- raising a raw
    redis.exceptions.ConnectionError straight through the request, with
    no fallback. We now pre-flight-check Redis with a short-timeout PING
    at app-factory time and fall back to memory:// (logged as a WARNING,
    never raised) if it's unreachable.
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
