"""merge orphaned 0009_add_performance_indexes branch into main chain

Revision ID: 0029_merge_heads
Revises: 0011_add_paymongo_subscription, 0028_add_email_only_provider
Create Date: 2026-06-19

This is a topology-only merge revision. It performs no DDL.

Background:
  0008_add_user_security_fields forked into two children:
    - 0009_add_performance_indexes -> 0010_security_reset
      -> 0011_add_paymongo_subscription   (orphaned branch, never merged back)
    - 0009_tenant_url_refactor -> 0010_backfill_default_tenant -> ... -> 0028

  Both branches are real, applied DDL (indexes + paymongo columns vs.
  tenant_slug backfill + the full v3.3-v5.3 billing/email history). Neither
  branch is a strict subset of the other, so this merges them as siblings
  rather than deleting either side. Verify against live schema
  (`\\d subscriptions`, `\\d profile`) before assuming any index here is
  truly redundant -- this revision intentionally does NOT drop anything.
"""
from alembic import op
import sqlalchemy as sa

revision = '0029_merge_heads'
down_revision = ('0011_add_paymongo_subscription', '0028_add_email_only_provider')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
