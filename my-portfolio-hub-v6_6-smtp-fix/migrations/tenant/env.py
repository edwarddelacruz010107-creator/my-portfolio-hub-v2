"""
migrations/tenant/env.py — Alembic environment for tenant_data_db

Targets TENANT_DATABASE_URL (SQLALCHEMY_BINDS["tenant"]).
Only imports models that carry __bind_key__ = "tenant".

Usage:
    cd migrations/tenant && alembic upgrade head
"""

from __future__ import with_statement

import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool, MetaData
from alembic import context

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

# ── Load only TENANT DATA models ────────────────────────────────────────────
from app.models.tenant_data import (
    Profile, Skill, Project, Testimonial, Service,
)

# Build a fresh MetaData from only tenant-bound tables so Alembic doesn't
# try to migrate core tables into tenant_data_db.
from sqlalchemy import MetaData as _MetaData
target_metadata = _MetaData()

for model_class in (Profile, Skill, Project, Testimonial, Service):
    # Reflect the table definitions into our isolated metadata object
    model_class.__table__.to_metadata(target_metadata)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

tenant_db_url = os.environ.get('TENANT_DATABASE_URL', '')
if tenant_db_url.startswith('postgres://'):
    tenant_db_url = tenant_db_url.replace('postgres://', 'postgresql://', 1)

if tenant_db_url:
    config.set_main_option('sqlalchemy.url', tenant_db_url)


def run_migrations_offline():
    url = config.get_main_option('sqlalchemy.url')
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={'paramstyle': 'named'},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix='sqlalchemy.',
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
