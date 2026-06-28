"""
0033 — Enterprise Storage Quota + Plan Capabilities (v6.0)

Adds:
  tenants:
    storage_used_bytes   BIGINT   — real-time storage counter, default 0
    storage_limit_bytes  BIGINT   — plan-derived limit cache (NULL = use plan default)
    subscription_state   VARCHAR  — 'trial'|'active'|'grace'|'readonly'|'suspended'
    grace_period_ends_at DATETIME — null unless in grace period

  media_uploads:          (new table)
    id, tenant_id, file_path, thumb_path, file_size, original_size,
    mime_type, category, original_name, uploaded_at, is_deleted

  plan_usage_log:         (new table — lightweight analytics)
    id, tenant_id, event_type, value, recorded_at

SAFE: additive only. No existing columns modified. No data destroyed.

Down-revision: 0032_superadmin_email_providers
"""

from alembic import op
import sqlalchemy as sa
from datetime import datetime, timezone

revision      = '0033_storage_quota_plan_caps'
down_revision = '0032_superadmin_email_providers'
branch_labels = None
depends_on    = None


def upgrade():
    # ── 1. Add storage + subscription state columns to tenants ────────────────
    with op.batch_alter_table('tenants', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'storage_used_bytes',
            sa.BigInteger(),
            nullable=False,
            server_default='0',
        ))
        batch_op.add_column(sa.Column(
            'storage_limit_bytes',
            sa.BigInteger(),
            nullable=True,
            comment='Plan-derived cache; NULL = derive from plan at runtime',
        ))
        batch_op.add_column(sa.Column(
            'subscription_state',
            sa.String(30),
            nullable=False,
            server_default='active',
            comment='trial|active|grace|readonly|suspended',
        ))
        batch_op.add_column(sa.Column(
            'grace_period_ends_at',
            sa.DateTime(timezone=True),
            nullable=True,
        ))

    # ── 2. media_uploads table ────────────────────────────────────────────────
    op.create_table(
        'media_uploads',
        sa.Column('id',            sa.Integer(),    primary_key=True),
        sa.Column('tenant_id',     sa.Integer(),    sa.ForeignKey('tenants.id', ondelete='CASCADE'),
                  nullable=False, index=True),
        sa.Column('file_path',     sa.Text(),       nullable=False),
        sa.Column('thumb_path',    sa.Text(),       nullable=True),
        sa.Column('file_size',     sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('original_size', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('mime_type',     sa.String(100),  nullable=False, server_default=''),
        sa.Column('category',      sa.String(50),   nullable=False, server_default='general'),
        sa.Column('original_name', sa.String(255),  nullable=True),
        sa.Column('uploaded_at',   sa.DateTime(timezone=True),
                  nullable=False,
                  server_default=sa.func.now()),
        sa.Column('is_deleted',    sa.Boolean(),    nullable=False, server_default='0'),
    )
    op.create_index('ix_media_uploads_tenant_deleted', 'media_uploads',
                    ['tenant_id', 'is_deleted'])
    op.create_index('ix_media_uploads_uploaded_at', 'media_uploads', ['uploaded_at'])

    # ── 3. plan_usage_log table ───────────────────────────────────────────────
    op.create_table(
        'plan_usage_log',
        sa.Column('id',          sa.Integer(),    primary_key=True),
        sa.Column('tenant_id',   sa.Integer(),    sa.ForeignKey('tenants.id', ondelete='CASCADE'),
                  nullable=False, index=True),
        sa.Column('event_type',  sa.String(60),   nullable=False),
        sa.Column('value',       sa.BigInteger(), nullable=True),
        sa.Column('meta',        sa.Text(),       nullable=True),   # JSON string
        sa.Column('recorded_at', sa.DateTime(timezone=True),
                  nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index('ix_plan_usage_log_tenant_event', 'plan_usage_log',
                    ['tenant_id', 'event_type'])
    op.create_index('ix_plan_usage_log_recorded_at', 'plan_usage_log', ['recorded_at'])


def downgrade():
    # plan_usage_log
    op.drop_index('ix_plan_usage_log_recorded_at',   table_name='plan_usage_log')
    op.drop_index('ix_plan_usage_log_tenant_event',  table_name='plan_usage_log')
    op.drop_table('plan_usage_log')

    # media_uploads
    op.drop_index('ix_media_uploads_uploaded_at',    table_name='media_uploads')
    op.drop_index('ix_media_uploads_tenant_deleted', table_name='media_uploads')
    op.drop_table('media_uploads')

    # tenants columns
    with op.batch_alter_table('tenants', schema=None) as batch_op:
        batch_op.drop_column('grace_period_ends_at')
        batch_op.drop_column('subscription_state')
        batch_op.drop_column('storage_limit_bytes')
        batch_op.drop_column('storage_used_bytes')
