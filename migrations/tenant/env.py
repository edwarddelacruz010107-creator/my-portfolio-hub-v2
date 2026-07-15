"""
migrations/tenant/env.py — Alembic environment for tenant_data_db

Targets TENANT_DATABASE_URL (SQLALCHEMY_BINDS["tenant"]).
Only imports models that carry __bind_key__ = "tenant".

Usage:
    cd migrations/tenant && alembic upgrade head
"""

from __future__ import with_statement

import logging
import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

# ── Load only TENANT DATA models ────────────────────────────────────────────
from app.models.tenant_data import (
    Profile, Skill, Project, ProjectReaction, Testimonial, Service, Certificate,
    WorkExperience,
)

# Build a fresh MetaData from only tenant-bound tables so Alembic doesn't
# try to migrate core tables into tenant_data_db.
from sqlalchemy import MetaData as _MetaData
target_metadata = _MetaData()

for model_class in (
    Profile, Skill, Project, ProjectReaction, Testimonial, Service,
    Certificate, WorkExperience,
):
    # Reflect the table definitions into our isolated metadata object
    model_class.__table__.to_metadata(target_metadata)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

from app.utils.db_config import get_tenant_database_url  # noqa: E402

tenant_db_url = get_tenant_database_url()

logger = logging.getLogger('alembic.tenant')


def _pg_connect_args(url: str) -> dict:
    if 'sqlite' in url or 'memory' in url:
        return {}
    return {
        'sslmode': os.environ.get('DB_SSLMODE', 'require'),
        'connect_timeout': 30,
        'options': '-c lock_timeout=60000 -c statement_timeout=120000',
    }


_ALEMBIC_OPTS = dict(
    target_metadata=target_metadata,
    compare_type=True,
    compare_server_default=True,
    version_table='alembic_version_tenant',
)


def run_migrations_offline():
    url = tenant_db_url
    context.configure(
        url=url,
        literal_binds=True,
        dialect_opts={'paramstyle': 'named'},
        **_ALEMBIC_OPTS,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    ini_section = config.get_section(config.config_ini_section, {})
    ini_section['sqlalchemy.url'] = tenant_db_url
    connectable = engine_from_config(
        ini_section,
        prefix='sqlalchemy.',
        poolclass=pool.NullPool,
        connect_args=_pg_connect_args(tenant_db_url),
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            **_ALEMBIC_OPTS,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
