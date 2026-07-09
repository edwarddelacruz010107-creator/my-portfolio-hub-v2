"""0025 — Add critical performance and tenant-isolation indexes

Revision ID: 0025
Revises: 0024_tenant_mailersend_migration
Create Date: 2026-06-17

PATCH-01 (2026-06-19):
  BUG: down_revision was `None` with comment "Will be set by Alembic to current head"
  FIX: Alembic never auto-sets down_revision. None declares a new independent root,
       creating a branch fork. Correct value is '0024_tenant_mailersend_migration'.
  IMPACT: This one-line fix eliminates the '0025 → 0026 → 0027' orphaned sub-chain
          that was causing "Multiple head revisions" CommandError in flask db upgrade.

Rationale:
  - tenant_slug is the primary multi-tenant filter on every query.
    Missing indexes → full-table scans on every page load.
  - user.email + user.tenant_slug are queried together on every login.
  - subscription.tenant_id + status filtered on every request middleware.
  - All indexes use IF NOT EXISTS equivalent (Alembic op.create_index
    has no IF NOT EXISTS, so we check via reflection first).
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import reflection

# revision identifiers, used by Alembic
revision = '0025'
down_revision = '0024_tenant_mailersend_migration'   # PATCHED: was None
branch_labels = None
depends_on = None

# (table, [columns], index_name, unique)
_INDEXES = [
    # Core tenant isolation indexes
    ('user',        ['tenant_slug'],               'ix_user_tenant_slug',          False),
    ('user',        ['email', 'tenant_slug'],       'ix_user_email_tenant',         False),
    ('user',        ['username', 'tenant_slug'],    'ix_user_username_tenant',      False),
    ('profile',     ['tenant_slug'],               'ix_profile_tenant_slug',        False),
    ('project',     ['tenant_slug', 'status'],     'ix_project_tenant_status',      False),
    ('project',     ['tenant_slug', 'is_featured'],'ix_project_tenant_featured',    False),
    ('skill',       ['tenant_slug'],               'ix_skill_tenant_slug',          False),
    ('testimonial', ['tenant_slug'],               'ix_testimonial_tenant_slug',    False),
    ('activity_log',['tenant_slug'],               'ix_activity_log_tenant_slug',   False),
    ('inquiry',     ['tenant_slug'],               'ix_inquiry_tenant_slug',        False),
    # Subscription performance
    ('subscription', ['tenant_id', 'status'],      'ix_subscription_tenant_status', False),
    ('subscription', ['expires_at'],               'ix_subscription_expires_at',    False),
    # Tenant table
    ('tenants',     ['slug'],                      'ix_tenants_slug',               True),
    ('tenants',     ['status'],                    'ix_tenants_status',             False),
]


def upgrade() -> None:
    """Create missing indexes — idempotent (skips existing)."""
    bind = op.get_bind()
    inspector = reflection.Inspector.from_engine(bind)

    for table, columns, idx_name, unique in _INDEXES:
        try:
            existing = {
                idx['name']
                for idx in inspector.get_indexes(table)
            }
            if idx_name in existing:
                continue  # Already exists — skip

            op.create_index(
                idx_name,
                table,
                columns,
                unique=unique,
            )
        except Exception as exc:
            # Table may not exist yet (fresh installs run db upgrade before
            # ensure-default-tenant). Log and continue — not fatal.
            import logging
            logging.getLogger(__name__).warning(
                'Could not create index %s on %s: %s', idx_name, table, exc
            )


def downgrade() -> None:
    """Drop all indexes added by this migration."""
    for _table, _columns, idx_name, _unique in reversed(_INDEXES):
        try:
            op.drop_index(idx_name)
        except Exception:
            pass