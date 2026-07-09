"""
0038 — Discount system activation-lifecycle wiring (v7.5 stabilization)

Adds (CORE bind, same as 0037):
  subscriptions.coupon_code (nullable String(100))
    Durable, cross-request reference to the coupon selected at
    plan-selection time. Session-based stashing
    (discount_checkout.stash_coupon) only survives within a single tenant
    browser session — it is unreadable from any activation path that
    completes out-of-session: the PayMongo webhook, superadmin manual
    payment approval, and superadmin PayMongo resync. This column is the
    fix: set once at plan selection, read by every activation path, reset
    whenever the subscription row is reused for a fresh checkout attempt.

  uq_discount_redemptions_subscription (partial unique index)
    DB-level backstop against double redemption when more than one
    activation path targets the same subscription_id (e.g. a webhook
    succeeds and a superadmin later triggers a resync on the same row).
    Partial (WHERE subscription_id IS NOT NULL) because subscription_id is
    nullable for non-subscription discount contexts. Postgres-only —
    SQLite dev environments will silently get a plain index instead of a
    partial unique one; the authoritative guard for all engines is the
    application-level check added to discount_service.redeem_discount().

SAFE: additive only. No existing columns/tables altered or dropped.

⚠️ APPLY-ORDER WARNING — same pre-existing caveat as 0036/0037
────────────────────────────────────────────────────────────
This repo has documented Alembic head divergence between this
versions/ chain and 0024_tenant_mailersend_migration (see
PHASE4_AUDIT.md / migration_cleanup.md). down_revision below chains off
0037_discount_campaigns, the most recent commit in versions/. If
`flask db upgrade` reports "Multiple head revisions are present",
resolve that pre-existing divergence with an explicit `flask db merge`
first — this migration does not attempt to fix it, to avoid conflating
an unrelated structural issue with this feature's changes.
"""

from alembic import op
import sqlalchemy as sa

revision      = '0038_discount_activation_wiring'
down_revision = '0037_discount_campaigns'
branch_labels = None
depends_on    = None


def upgrade():
    bind = op.get_bind()
    dialect = bind.dialect.name

    op.add_column(
        'subscriptions',
        sa.Column('coupon_code', sa.String(100), nullable=True),
    )

    if dialect == 'postgresql':
        op.create_index(
            'uq_discount_redemptions_subscription',
            'discount_redemptions',
            ['subscription_id'],
            unique=True,
            postgresql_where=sa.text('subscription_id IS NOT NULL'),
        )
    else:
        # SQLite/other: no partial-unique support here. Plain non-unique
        # index for query performance; app-level guard in
        # discount_service.redeem_discount() is the real safety net for
        # non-Postgres environments (e.g. local dev/tests).
        op.create_index(
            'uq_discount_redemptions_subscription',
            'discount_redemptions',
            ['subscription_id'],
        )


def downgrade():
    op.drop_index('uq_discount_redemptions_subscription', table_name='discount_redemptions')
    op.drop_column('subscriptions', 'coupon_code')
