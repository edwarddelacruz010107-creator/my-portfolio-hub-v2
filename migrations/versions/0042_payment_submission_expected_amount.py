"""
0042_payment_submission_expected_amount
========================================
FIX [MED-COUPON-01]: manual-payment superadmin review UI showed only the
tenant's self-reported amount_paid with no system-computed reference to
compare it against — a reviewer had to mentally recompute
list_price - coupon_discount for every submission to catch underpayment.
Entitlement itself was never at risk (activate_subscription()/
apply_on_activation() already ignore amount_paid and re-derive price
server-side), but the human control had no computer-assisted check.

Adds two columns to payment_submissions, computed once at submission time
via discount_checkout.quote_for_context() (server-side price table +
re-validated coupon), stored so historical submissions remain auditable
even if plan prices or the campaign later change.

Revision ID: 0042_payment_submission_expected_amount
Revises:     0041_add_google_oauth_fields
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision      = '0042_payment_submission_expected_amount'
down_revision = '0041_add_google_oauth_fields'
branch_labels = None
depends_on    = None


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column_name in [c['name'] for c in inspector.get_columns(table_name)]


def upgrade() -> None:
    if not _table_exists('payment_submissions'):
        # Table itself doesn't exist yet on this DB — nothing to patch.
        # Mirrors the _table_exists guard pattern used throughout this
        # migration chain to keep it idempotent/safe on partial states.
        return

    if not _column_exists('payment_submissions', 'expected_amount'):
        op.add_column(
            'payment_submissions',
            sa.Column('expected_amount', sa.Float(), nullable=True),
        )

    if not _column_exists('payment_submissions', 'coupon_code_applied'):
        op.add_column(
            'payment_submissions',
            sa.Column('coupon_code_applied', sa.String(length=50), nullable=True),
        )


def downgrade() -> None:
    if not _table_exists('payment_submissions'):
        return
    if _column_exists('payment_submissions', 'coupon_code_applied'):
        op.drop_column('payment_submissions', 'coupon_code_applied')
    if _column_exists('payment_submissions', 'expected_amount'):
        op.drop_column('payment_submissions', 'expected_amount')
