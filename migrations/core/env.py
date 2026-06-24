"""
migrations/core/env.py — Alembic environment for core_db

Targets CORE_DATABASE_URL (SQLALCHEMY_DATABASE_URI / default bind).
Only imports models that live in core_db.

Usage:
    flask db upgrade  (with FLASK_MIGRATION_DB=core)
    OR
    cd migrations/core && alembic upgrade head
"""

from __future__ import with_statement

import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# ── Make the app importable ──────────────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

# ── Load only CORE models so Alembic only sees core_db tables ───────────────
from app.models.core import (
    db,
    Tenant, User,
    Subscription, WebhookEvent,
    PaymentMethod, PaymentInstruction, PaymentSubmission,
    PlatformSetting, TenantCommunicationSettings,
    PasswordResetOTP, GlobalEmailConfig,
    Inquiry, InquiryReply, SubscriptionNotification, ActivityLog,
)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = db.metadata

# Override sqlalchemy.url from environment
core_db_url = os.environ.get('CORE_DATABASE_URL', '')
if core_db_url.startswith('postgres://'):
    core_db_url = core_db_url.replace('postgres://', 'postgresql://', 1)

if core_db_url:
    config.set_main_option('sqlalchemy.url', core_db_url)


def run_migrations_offline():
    url = config.get_main_option('sqlalchemy.url')
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={'paramstyle': 'named'},
        include_schemas=False,
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
