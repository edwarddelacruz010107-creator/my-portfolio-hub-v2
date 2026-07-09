"""0027_contact_delivery_fields — Canonical delivery tracking + contact_email migration

Revision ID: 0027_contact_delivery_fields
Revises: 0026_fix_duplicate_indexes
Create Date: 2026-06-19

REPLACES: The earlier 0027_inquiry_delivery_fields.py which was a duplicate
          competing head at the same revision slot with the same down_revision.
          That file has been DELETED and superseded by this one.

Changes:
  inquiries table:
    1. user_agent       VARCHAR(500)  NULLABLE — submitter User-Agent string
    2. submission_id    VARCHAR(80)   NULLABLE — idempotency key from contact form
    3. provider_used    VARCHAR(30)   NULLABLE — basin | email_only | email | internal
    4. delivery_status  VARCHAR(20)   NULLABLE — delivered | failed | pending
    5. delivery_error   VARCHAR(500)  NULLABLE — error detail on failure
    Index: ix_inquiries_provider_delivery (provider_used, delivery_status)
    Index: ix_inquiries_tenant_submission_id (tenant_slug, submission_id)

  tenants table:
    6. contact_email    VARCHAR(120)  NULLABLE — contact routing override
    Index: ix_tenants_contact_email (contact_email)

Design notes:
  - ALL columns nullable, no server_default, so existing rows remain valid.
  - Idempotent: each op is guarded by _has_column / _has_index checks.
  - batch_alter_table used throughout for SQLite compatibility.
  - String lengths match the Inquiry model in app/models/core.py exactly.
  - submission_id is NOT on the model (it is used only at the route level
    for idempotency); the column is created here so the route can query it
    without hitting "no such column".

Backwards compatibility:
  - Existing inquiries: all new columns are NULL — no data loss, no backfill.
  - Downgrade: drops all columns cleanly.
"""

from alembic import op
import sqlalchemy as sa


revision      = '0027_contact_delivery_fields'
down_revision = '0026_fix_duplicate_indexes'
branch_labels = None
depends_on    = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _has_column(inspector, table: str, column: str) -> bool:
    return any(c.get('name') == column for c in inspector.get_columns(table))


def _has_index(inspector, table: str, index: str) -> bool:
    return any(i.get('name') == index for i in inspector.get_indexes(table))


# ── Upgrade ───────────────────────────────────────────────────────────────────

def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # ── inquiries ─────────────────────────────────────────────────────────────
    if inspector.has_table('inquiries'):
        with op.batch_alter_table('inquiries', schema=None) as batch:
            if not _has_column(inspector, 'inquiries', 'user_agent'):
                batch.add_column(sa.Column('user_agent', sa.String(500), nullable=True))

            if not _has_column(inspector, 'inquiries', 'submission_id'):
                batch.add_column(sa.Column('submission_id', sa.String(80), nullable=True))

            if not _has_column(inspector, 'inquiries', 'provider_used'):
                batch.add_column(sa.Column('provider_used', sa.String(30), nullable=True))

            if not _has_column(inspector, 'inquiries', 'delivery_status'):
                batch.add_column(sa.Column('delivery_status', sa.String(20), nullable=True))

            if not _has_column(inspector, 'inquiries', 'delivery_error'):
                batch.add_column(sa.Column('delivery_error', sa.String(500), nullable=True))

            if not _has_index(inspector, 'inquiries', 'ix_inquiries_provider_delivery'):
                batch.create_index(
                    'ix_inquiries_provider_delivery',
                    ['provider_used', 'delivery_status'],
                )

            if not _has_index(inspector, 'inquiries', 'ix_inquiries_tenant_submission_id'):
                batch.create_index(
                    'ix_inquiries_tenant_submission_id',
                    ['tenant_slug', 'submission_id'],
                )

    # ── tenants ───────────────────────────────────────────────────────────────
    if inspector.has_table('tenants'):
        with op.batch_alter_table('tenants', schema=None) as batch:
            if not _has_column(inspector, 'tenants', 'contact_email'):
                batch.add_column(sa.Column('contact_email', sa.String(120), nullable=True))

            if not _has_index(inspector, 'tenants', 'ix_tenants_contact_email'):
                batch.create_index('ix_tenants_contact_email', ['contact_email'])


# ── Downgrade ─────────────────────────────────────────────────────────────────

def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table('inquiries'):
        with op.batch_alter_table('inquiries', schema=None) as batch:
            if _has_index(inspector, 'inquiries', 'ix_inquiries_tenant_submission_id'):
                batch.drop_index('ix_inquiries_tenant_submission_id')
            if _has_index(inspector, 'inquiries', 'ix_inquiries_provider_delivery'):
                batch.drop_index('ix_inquiries_provider_delivery')
            for col in ('delivery_error', 'delivery_status', 'provider_used',
                        'submission_id', 'user_agent'):
                if _has_column(inspector, 'inquiries', col):
                    batch.drop_column(col)

    if inspector.has_table('tenants'):
        with op.batch_alter_table('tenants', schema=None) as batch:
            if _has_index(inspector, 'tenants', 'ix_tenants_contact_email'):
                batch.drop_index('ix_tenants_contact_email')
            if _has_column(inspector, 'tenants', 'contact_email'):
                batch.drop_column('contact_email')
