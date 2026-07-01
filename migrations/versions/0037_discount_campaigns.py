"""
0037 — Superadmin Discount & Promotion Manager (v6.6)

Adds (both CORE bind — global/superadmin scope, not per-tenant data):
  discount_campaigns:
    id, name, description, code (nullable/unique), discount_type,
    value, applies_to, plan_slug, is_global, is_active, usage_limit,
    usage_count, per_tenant_limit, first_time_only, starts_at,
    expires_at, created_at, updated_at
  discount_redemptions:
    id, campaign_id (FK), tenant_id (FK), subscription_id (FK, nullable),
    amount_before, amount_discounted, amount_after, billing_cycle,
    redeemed_at

SAFE: additive only. No existing tables or columns touched. Mirrors the
Subscription model already in core_db — DiscountRedemption FKs into both
tenants and subscriptions with ON DELETE handling matching existing
SubscriptionNotification conventions (CASCADE on tenant, SET NULL on
subscription).

⚠️ APPLY-ORDER WARNING — same caveat as 0036_certificates
────────────────────────────────────────────────────────
This repo has documented Alembic head divergence (0011, 0028, 0035 —
see PHASE4_AUDIT.md). down_revision below chains off 0036_certificates,
the most recent commit in versions/. If `flask db upgrade` reports
"Multiple head revisions are present", resolve that pre-existing
divergence with an explicit `flask db merge` first — this migration does
not attempt to fix it.
"""

from alembic import op
import sqlalchemy as sa

revision      = '0037_discount_campaigns'
down_revision = '0036_certificates'
branch_labels = None
depends_on    = None


def upgrade():
    op.create_table(
        'discount_campaigns',
        sa.Column('id',                sa.Integer(),   primary_key=True),
        sa.Column('name',               sa.String(255), nullable=False),
        sa.Column('description',        sa.Text(),      nullable=True),
        sa.Column('code',               sa.String(100), nullable=True),
        sa.Column('discount_type',      sa.String(20),  nullable=False, server_default='percent'),
        sa.Column('value',              sa.Numeric(10, 2), nullable=False),
        sa.Column('applies_to',         sa.String(20),  nullable=False, server_default='all'),
        sa.Column('plan_slug',          sa.String(100), nullable=True),
        sa.Column('is_global',          sa.Boolean(),   nullable=False, server_default='0'),
        sa.Column('is_active',          sa.Boolean(),   nullable=False, server_default='1'),
        sa.Column('usage_limit',        sa.Integer(),   nullable=True),
        sa.Column('usage_count',        sa.Integer(),   nullable=False, server_default='0'),
        sa.Column('per_tenant_limit',   sa.Integer(),   nullable=True, server_default='1'),
        sa.Column('first_time_only',    sa.Boolean(),   nullable=False, server_default='0'),
        sa.Column('starts_at',          sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at',         sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at',         sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.func.now()),
        sa.Column('updated_at',         sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.func.now()),
        sa.CheckConstraint("discount_type IN ('percent', 'fixed')", name='ck_discount_campaigns_type'),
        sa.CheckConstraint("applies_to IN ('monthly', 'yearly', 'one_time', 'all')",
                            name='ck_discount_campaigns_applies_to'),
    )
    op.create_index('ix_discount_campaigns_code', 'discount_campaigns', ['code'], unique=True)
    op.create_index('ix_discount_campaigns_active_dates', 'discount_campaigns',
                     ['is_active', 'starts_at', 'expires_at'])

    op.create_table(
        'discount_redemptions',
        sa.Column('id',                 sa.Integer(), primary_key=True),
        sa.Column('campaign_id',        sa.Integer(),
                  sa.ForeignKey('discount_campaigns.id', ondelete='CASCADE'), nullable=False),
        sa.Column('tenant_id',          sa.Integer(),
                  sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('subscription_id',    sa.Integer(),
                  sa.ForeignKey('subscriptions.id', ondelete='SET NULL'), nullable=True),
        sa.Column('amount_before',      sa.Numeric(10, 2), nullable=False),
        sa.Column('amount_discounted',  sa.Numeric(10, 2), nullable=False),
        sa.Column('amount_after',       sa.Numeric(10, 2), nullable=False),
        sa.Column('billing_cycle',      sa.String(20), nullable=True),
        sa.Column('redeemed_at',        sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.func.now()),
    )
    op.create_index('ix_discount_redemptions_tenant_id', 'discount_redemptions', ['tenant_id'])
    op.create_index('ix_discount_redemptions_campaign_tenant', 'discount_redemptions',
                     ['campaign_id', 'tenant_id'])


def downgrade():
    op.drop_index('ix_discount_redemptions_campaign_tenant', table_name='discount_redemptions')
    op.drop_index('ix_discount_redemptions_tenant_id', table_name='discount_redemptions')
    op.drop_table('discount_redemptions')

    op.drop_index('ix_discount_campaigns_active_dates', table_name='discount_campaigns')
    op.drop_index('ix_discount_campaigns_code', table_name='discount_campaigns')
    op.drop_table('discount_campaigns')
