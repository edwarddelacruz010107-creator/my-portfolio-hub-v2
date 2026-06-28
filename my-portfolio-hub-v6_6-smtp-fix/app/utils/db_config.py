# app/utils/db_config.py
# ─────────────────────────────────────────────────────────────────────────────
# Single source of truth for database URL resolution.
# Used by: migrations/env.py, app/__init__.py, CLI scripts.
#
# DUAL-DB ARCHITECTURE (v5.0):
#   CORE_DATABASE_URL   — tenants, users, billing, subscriptions, activity logs
#   TENANT_DATABASE_URL — profile, skills, projects, testimonials, services
#
# Migration support: DIRECT_* variants use port 5432 (bypasses PgBouncer),
#   which Alembic requires for session-level SET commands.
# ─────────────────────────────────────────────────────────────────────────────

import os
from urllib.parse import urlparse, urlunparse


def _fix_scheme(url: str) -> str:
    if url and url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


def normalize_db_url(url: str) -> str:
    if not url:
        raise ValueError("Empty database URL")

    url = _fix_scheme(url)
    parsed = urlparse(url)

    port = parsed.port or (5432 if parsed.scheme.startswith("postgres") else None)
    netloc = parsed.hostname or ""

    if parsed.username:
        auth = parsed.username
        if parsed.password:
            auth += f":{parsed.password}"
        netloc = f"{auth}@{parsed.hostname}"

    if port:
        netloc += f":{port}"

    rebuilt = parsed._replace(netloc=netloc)
    return urlunparse(rebuilt)


def get_database_url() -> str:
    """
    Core database URL for Alembic migrations and core DB access.
    
    Priority order:
      1. DIRECT_CORE_DATABASE_URL (port-5432 direct connection, for Alembic)
      2. DIRECT_DATABASE_URL      (legacy; pre-v5.0 direct-URL name)
      3. CORE_DATABASE_URL        (production dual-db — primary)
      4. DEV_CORE_DATABASE_URL    (development dual-db)
      5. DEV_DATABASE_URL         (legacy dev single-db)
      6. DATABASE_URL             (last-resort legacy fallback)
    """
    url = (
        os.getenv("DIRECT_CORE_DATABASE_URL")
        or os.getenv("DIRECT_DATABASE_URL")
        or os.getenv("CORE_DATABASE_URL")
        or os.getenv("DEV_CORE_DATABASE_URL")
        or os.getenv("DEV_DATABASE_URL")
        or os.getenv("DATABASE_URL")
    )

    if not url:
        raise RuntimeError(
            "No core database URL configured. "
            "Set CORE_DATABASE_URL (production) or DEV_DATABASE_URL (development)."
        )

    return normalize_db_url(url)


def get_tenant_database_url() -> str:
    """
    Tenant database URL (profile, skills, projects, testimonials, services).
    
    Priority order:
      1. DIRECT_TENANT_DATABASE_URL (port-5432 direct connection, for migrations)
      2. TENANT_DATABASE_URL        (production)
      3. DEV_TENANT_DATABASE_URL    (development)
      4. Falls back to core DB URL as single-DB fallback
    """
    url = (
        os.getenv("DIRECT_TENANT_DATABASE_URL")
        or os.getenv("TENANT_DATABASE_URL")
        or os.getenv("DEV_TENANT_DATABASE_URL")
    )

    if not url:
        # Single-DB fallback for dev/test environments without dual-DB
        return get_database_url()

    return normalize_db_url(url)
