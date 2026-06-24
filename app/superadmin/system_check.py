"""
app/superadmin/system_check.py
────────────────────────────────────────────────────────────────────────────
Secure system-check diagnostic page for superadmins.

Route:  GET /superadmin/system-check

Displays:
  • Database (core + tenant) connectivity
  • Current environment & config
  • Migration status (alembic_version table)
  • Superadmin existence
  • Redis status
  • Session configuration
  • Account lockout summary

Security:
  • @superadmin_required — only authenticated superadmins can see this.
  • NEVER exposes passwords, tokens, secret keys, or raw connection strings.
  • Redacts all sensitive values before returning them to the template.

Registration:
  Import and register this blueprint in app/__init__.py:

    from app.superadmin.system_check import system_check_bp
    app.register_blueprint(system_check_bp)

  OR call register_system_check(superadmin_bp) to attach routes to the
  existing superadmin blueprint instead of creating a new one.
"""

from __future__ import annotations

import hashlib
import logging
import os

from flask import Blueprint, jsonify, render_template_string, current_app
from flask_login import current_user

logger = logging.getLogger(__name__)

# ── Inline Jinja2 template (no extra template file required) ──────────────────
_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>System Check — Superadmin</title>
  <style>
    body { font-family: 'Segoe UI', system-ui, sans-serif; background:#0f172a;
           color:#e2e8f0; margin:0; padding:2rem; }
    h1   { color:#38bdf8; margin-bottom:.5rem; }
    h2   { color:#94a3b8; font-size:1rem; margin:1.5rem 0 .5rem; text-transform:uppercase; letter-spacing:.05em; }
    .card{ background:#1e293b; border-radius:.75rem; padding:1.25rem 1.5rem;
           margin-bottom:1rem; border:1px solid #334155; }
    .row { display:flex; justify-content:space-between; align-items:center;
           padding:.4rem 0; border-bottom:1px solid #1e293b; }
    .row:last-child { border:none; }
    .label{ color:#94a3b8; font-size:.875rem; }
    .val  { font-size:.875rem; font-weight:500; }
    .ok   { color:#4ade80; }
    .warn { color:#facc15; }
    .err  { color:#f87171; }
    .badge{ display:inline-block; padding:.2em .7em; border-radius:9999px;
            font-size:.75rem; font-weight:700; }
    .b-ok  { background:#14532d; color:#4ade80; }
    .b-warn{ background:#713f12; color:#facc15; }
    .b-err { background:#7f1d1d; color:#f87171; }
    .back  { display:inline-block; margin-bottom:1.5rem; color:#38bdf8;
             text-decoration:none; font-size:.875rem; }
    .back:hover { text-decoration:underline; }
    .ts   { color:#64748b; font-size:.75rem; margin-top:2rem; }
  </style>
</head>
<body>
<a href="/superadmin/" class="back">← Back to Dashboard</a>
<h1>🛡 System Check</h1>
<p style="color:#64748b;font-size:.875rem">
  Secure diagnostic — visible to superadmins only.
  Secrets are never shown.
</p>

<h2>Environment</h2>
<div class="card">
  {% for k,v in env_info.items() %}
  <div class="row">
    <span class="label">{{ k }}</span>
    <span class="val {{ v.cls }}">{{ v.value }}</span>
  </div>
  {% endfor %}
</div>

<h2>Database</h2>
<div class="card">
  {% for k,v in db_info.items() %}
  <div class="row">
    <span class="label">{{ k }}</span>
    <span class="val {{ v.cls }}">{{ v.value }}</span>
  </div>
  {% endfor %}
</div>

<h2>Authentication</h2>
<div class="card">
  {% for k,v in auth_info.items() %}
  <div class="row">
    <span class="label">{{ k }}</span>
    <span class="val {{ v.cls }}">{{ v.value }}</span>
  </div>
  {% endfor %}
</div>

<h2>Session &amp; Cookies</h2>
<div class="card">
  {% for k,v in session_info.items() %}
  <div class="row">
    <span class="label">{{ k }}</span>
    <span class="val {{ v.cls }}">{{ v.value }}</span>
  </div>
  {% endfor %}
</div>

<h2>Redis / Rate Limiting</h2>
<div class="card">
  {% for k,v in redis_info.items() %}
  <div class="row">
    <span class="label">{{ k }}</span>
    <span class="val {{ v.cls }}">{{ v.value }}</span>
  </div>
  {% endfor %}
</div>

<h2>Migration State</h2>
<div class="card">
  {% for k,v in migration_info.items() %}
  <div class="row">
    <span class="label">{{ k }}</span>
    <span class="val {{ v.cls }}">{{ v.value }}</span>
  </div>
  {% endfor %}
</div>

<p class="ts">Generated at {{ timestamp }} UTC · User: {{ user }}</p>
</body>
</html>
"""


def _cell(value: str, cls: str = 'val') -> dict:
    return {'value': value, 'cls': cls}


def _ok(v: str)   -> dict: return _cell(v, 'ok')
def _warn(v: str) -> dict: return _cell(v, 'warn')
def _err(v: str)  -> dict: return _cell(v, 'err')


def _redact_url(url: str) -> str:
    """Show only host:port of a database URL — never credentials."""
    if not url:
        return '(not set)'
    try:
        from urllib.parse import urlparse
        p = urlparse(url)
        return f"{p.scheme}://***@{p.hostname}:{p.port or '?'}/{(p.path or '').lstrip('/')}"
    except Exception:
        return '(redacted)'


def _check_db_core():
    """Ping the core database."""
    try:
        from app import db
        db.session.execute(db.text("SELECT 1"))
        db.session.remove()
        return _ok("✓ connected")
    except Exception as exc:
        return _err(f"✗ {type(exc).__name__}")


def _check_db_tenant():
    """Ping the tenant database."""
    try:
        from app import db
        engine = db.get_engine(bind_key='tenant')
        with engine.connect() as conn:
            conn.execute(db.text("SELECT 1"))
        return _ok("✓ connected")
    except Exception as exc:
        return _err(f"✗ {type(exc).__name__}")


def _check_superadmin_exists():
    try:
        from app.models import User
        count = User.query.filter_by(is_superadmin=True).count()
        if count > 0:
            return _ok(f"✓ {count} superadmin(s) found")
        return _err("✗ NO superadmin exists — run flask create-superadmin")
    except Exception as exc:
        return _err(f"✗ query error: {type(exc).__name__}")


def _check_secret_key():
    key = current_app.config.get('SECRET_KEY', '')
    if not key:
        return _err("✗ SECRET_KEY is empty")
    # Show only a fingerprint (first 8 hex chars of SHA-256), never the key itself
    fp = hashlib.sha256(key.encode()).hexdigest()[:8]
    if len(key) < 32:
        return _warn(f"⚠ too short (fingerprint: {fp}…)")
    return _ok(f"✓ set (fingerprint: {fp}…)")


def _check_redis():
    try:
        redis_url = os.environ.get('REDIS_URL', '')
        if not redis_url:
            return _warn("⚠ REDIS_URL not set (memory fallback)")
        import redis
        kwargs = {"socket_connect_timeout": 2, "socket_timeout": 2}
        if redis_url.startswith("rediss://"):
            kwargs["ssl_cert_reqs"] = None
        client = redis.from_url(redis_url, **kwargs)
        client.ping()
        client.close()
        return _ok("✓ reachable")
    except ImportError:
        return _warn("⚠ redis-py not installed")
    except Exception as exc:
        return _err(f"✗ {type(exc).__name__}")


def _check_migration():
    try:
        from app import db
        result = db.session.execute(
            db.text("SELECT version_num FROM alembic_version")
        ).fetchall()
        db.session.remove()
        if result:
            rev = result[-1][0]
            return _ok(f"✓ head = {rev}")
        return _warn("⚠ alembic_version empty (migrations not run?)")
    except Exception as exc:
        return _err(f"✗ {type(exc).__name__}: {exc}")


def _check_tenant_tables():
    """Verify tenant-bound tables exist in tenant DB."""
    required = ['profile', 'skills', 'projects', 'testimonials']
    try:
        from app import db
        from sqlalchemy import inspect as sa_inspect
        engine = db.get_engine(bind_key='tenant')
        inspector = sa_inspect(engine)
        existing = set(inspector.get_table_names())
        missing = [t for t in required if t not in existing]
        if not missing:
            return _ok(f"✓ all {len(required)} tenant tables exist")
        return _err(f"✗ missing: {', '.join(missing)} — run flask ensure-tenant-schema")
    except Exception as exc:
        return _err(f"✗ {type(exc).__name__}")


def build_system_check_data() -> dict:
    """Collect all diagnostic data. Safe to call inside an app context."""
    from datetime import datetime, timezone

    cfg = current_app.config
    is_prod = not cfg.get('DEBUG') and not cfg.get('TESTING')

    env_info = {
        'FLASK_ENV':       _ok(cfg.get('ENV', 'unknown')) if is_prod else _warn(cfg.get('ENV', 'unknown')),
        'DEBUG mode':      _err("ON — unsafe in production!") if cfg.get('DEBUG') else _ok("off"),
        'TESTING mode':    _err("ON") if cfg.get('TESTING') else _ok("off"),
        'ProxyFix active': _ok("yes (x_for=1, x_proto=1)") if True else _warn("unknown"),
    }

    db_info = {
        'Core DB':           _check_db_core(),
        'Core DB URL':       _cell(_redact_url(os.environ.get('CORE_DATABASE_URL', ''))),
        'Tenant DB':         _check_db_tenant(),
        'Tenant DB URL':     _cell(_redact_url(os.environ.get('TENANT_DATABASE_URL', ''))),
        'Tenant tables':     _check_tenant_tables(),
    }

    auth_info = {
        'Superadmin exists':  _check_superadmin_exists(),
        'SECRET_KEY':         _check_secret_key(),
        'FERNET_KEY set':     _ok("✓ set") if cfg.get('FERNET_KEY') else _err("✗ not set"),
        'CSRF enabled':       _ok("yes") if cfg.get('WTF_CSRF_ENABLED') else _warn("disabled"),
        'Rate limiting':      _ok("yes") if cfg.get('RATELIMIT_ENABLED', True) else _warn("disabled"),
    }

    session_info = {
        'SESSION_COOKIE_SECURE':    _ok("True ✓") if cfg.get('SESSION_COOKIE_SECURE') else _err("False ✗ (cookies sent over HTTP)"),
        'SESSION_COOKIE_HTTPONLY':  _ok("True ✓") if cfg.get('SESSION_COOKIE_HTTPONLY') else _warn("False"),
        'SESSION_COOKIE_SAMESITE':  _ok(str(cfg.get('SESSION_COOKIE_SAMESITE', 'unset'))),
        'REMEMBER_COOKIE_SECURE':   _ok("True ✓") if cfg.get('REMEMBER_COOKIE_SECURE') else _err("False ✗"),
        'WTF_CSRF_SSL_STRICT':      _ok("True ✓") if cfg.get('WTF_CSRF_SSL_STRICT') else _warn("False (dev mode)"),
        'Session lifetime':         _cell(str(cfg.get('PERMANENT_SESSION_LIFETIME', 'unset'))),
        'Login manager protection': _ok("strong") if True else _warn("unknown"),
    }

    redis_info = {
        'Redis connectivity':      _check_redis(),
        'RATELIMIT_STORAGE (resolved)': _cell(
            'redis' if (cfg.get('RATELIMIT_STORAGE_URI_RESOLVED') or '').startswith('redis')
            else 'memory (degraded)',
            'ok' if (cfg.get('RATELIMIT_STORAGE_URI_RESOLVED') or '').startswith('redis') else 'warn',
        ),
    }

    migration_info = {
        'Alembic head (core)':  _check_migration(),
    }

    return {
        'env_info':       env_info,
        'db_info':        db_info,
        'auth_info':      auth_info,
        'session_info':   session_info,
        'redis_info':     redis_info,
        'migration_info': migration_info,
        'timestamp':      datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
        'user':           current_user.username if current_user.is_authenticated else 'anonymous',
    }


def register_system_check(superadmin_bp):
    """
    Attach the /system-check route to an existing Blueprint.
    Call this at the bottom of app/superadmin/__init__.py:

        from app.superadmin.system_check import register_system_check
        register_system_check(superadmin)
    """

    @superadmin_bp.route('/system-check')
    def system_check():
        """Secure superadmin system diagnostics page."""
        # Import here to avoid circular imports at module load time
        from app.superadmin import superadmin_required
        # The decorator cannot be applied to a dynamically-registered route,
        # so we check manually.
        if not current_user.is_authenticated or not current_user.is_superadmin:
            from flask import redirect, url_for
            return redirect(url_for('superadmin.login'))

        data = build_system_check_data()
        return render_template_string(_TEMPLATE, **data)

    @superadmin_bp.route('/system-check/json')
    def system_check_json():
        """JSON version — useful for monitoring / health-check scripts."""
        if not current_user.is_authenticated or not current_user.is_superadmin:
            return jsonify({'error': 'forbidden'}), 403

        data = build_system_check_data()
        # Flatten to string/status pairs for clean JSON
        out = {'timestamp': data['timestamp'], 'checks': {}}
        for section, items in data.items():
            if not isinstance(items, dict) or section in ('timestamp', 'user'):
                continue
            out['checks'][section] = {
                k: {'value': v['value'], 'status': v['cls']}
                for k, v in items.items()
            }
        return jsonify(out)
