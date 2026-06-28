# migrations/env.py
# ─────────────────────────────────────────────────────────────────────────────
# Portfolio CMS — Alembic environment for dual-database architecture
#
# Key behaviors:
#   • Uses CORE_DATABASE_URL (DIRECT_CORE_DATABASE_URL for Alembic) — never
#     DATABASE_URL alone (that was the pre-v5.0 bug).
#   • compare_type=True catches TEXT → JSONB upgrades.
#   • NullPool so Alembic never holds idle connections between DDL ops.
#   • SSL enforced for PostgreSQL; skipped for SQLite (dev/test).
#   • All models imported so Alembic sees the full metadata graph.
# ─────────────────────────────────────────────────────────────────────────────

import logging
import os
import sys
from logging.config import fileConfig

# Make sure project root is on sys.path
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from app.utils.db_config import get_database_url  # noqa: E402

from alembic import context
from sqlalchemy import engine_from_config, pool

# ── Alembic Config object ─────────────────────────────────────────────────────
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

logger = logging.getLogger('alembic.env')


def get_migration_url() -> str:
    """
    Return the CORE database URL for migrations.
    Uses DIRECT_CORE_DATABASE_URL (port 5432) when available — Supabase
    PgBouncer (port 6543) drops session-level SET commands that Alembic needs.
    """
    return get_database_url()


def _safe_url(url: str) -> str:
    """Redact password from URL for logging."""
    try:
        from urllib.parse import urlparse, urlunparse
        p = urlparse(url)
        safe = p._replace(netloc=f'{p.username}:***@{p.hostname}:{p.port}')
        return urlunparse(safe)
    except Exception:
        return '<url>'


# ── Target metadata ───────────────────────────────────────────────────────────
from app import create_app, db  # noqa: E402

# Import ALL models so SQLAlchemy metadata is fully populated.
# Alembic compares this metadata against the live DB to generate diffs.
from app.models.portfolio import (        # noqa: F401
    Profile, Skill, Project,
    Testimonial, ActivityLog, Inquiry,
    Subscription, PaymentMethod, PaymentInstruction,
    PaymentSubmission, WebhookEvent, PlatformSetting,
    TenantCommunicationSettings, GlobalEmailConfig,
    SubscriptionNotification,
)
from app.models.tenant_form_settings import TenantFormSettings  # noqa: F401

_flask_env = os.environ.get('FLASK_ENV', 'development')
_app = create_app(_flask_env)

with _app.app_context():
    from app.models import User  # noqa: F401

target_metadata = db.metadata

# ── Alembic diff options ──────────────────────────────────────────────────────
def include_object(obj, name, type_, reflected, compare_to):
    """
    Filter function for Alembic autogenerate.
    
    Excludes tables that have table.info['bind_key'] == 'tenant' from
    core migration generation. This prevents tenant-bound models from
    polluting core migrations.
    
    Tenant-bound tables should only appear in migrations/tenant/versions/
    (if using a separate tenant migration chain) or handled via the
    cli_ensure_tenant_schema command.
    """
    if type_ == 'table':
        # Exclude tables marked as tenant-bound
        if hasattr(obj, 'info') and obj.info.get('bind_key') == 'tenant':
            return False
    return True


_ALEMBIC_OPTS = dict(
    target_metadata=target_metadata,
    compare_type=True,
    compare_server_default=True,
    include_schemas=False,
    render_as_batch=False,
    include_object=include_object,
)


# ── SSL connect_args (PostgreSQL only) ───────────────────────────────────────
def _pg_connect_args(url: str) -> dict:
    """Return SSL connect_args when targeting a real PostgreSQL server."""
    if 'sqlite' in url or 'memory' in url:
        return {}
    return {
        'sslmode':         'require',
        'connect_timeout': 30,
        'options':         '-c lock_timeout=60000 -c statement_timeout=120000',
    }


# ── Offline mode ─────────────────────────────────────────────────────────────
def run_migrations_offline() -> None:
    """Generate SQL script without a live connection."""
    url = get_migration_url()
    logger.info('Alembic offline mode — core DB: %s', _safe_url(url))
    context.configure(
        url=url,
        literal_binds=True,
        dialect_opts={'paramstyle': 'named'},
        **_ALEMBIC_OPTS,
    )
    with context.begin_transaction():
        context.run_migrations()


# ── Online mode ──────────────────────────────────────────────────────────────
def run_migrations_online() -> None:
    """Connect to the core database and run migrations directly."""
    url = get_migration_url()
    logger.info('Alembic online mode — core DB: %s', _safe_url(url))

    ini_section = config.get_section(config.config_ini_section, {})
    ini_section['sqlalchemy.url'] = url

    connectable = engine_from_config(
        ini_section,
        prefix='sqlalchemy.',
        poolclass=pool.NullPool,
        connect_args=_pg_connect_args(url),
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            **_ALEMBIC_OPTS,
        )
        with context.begin_transaction():
            context.run_migrations()


# ── Dispatch ──────────────────────────────────────────────────────────────────
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
