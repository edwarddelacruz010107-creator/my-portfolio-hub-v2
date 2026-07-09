"""Merge all heads into single chain — v5.6 final

Revision ID: 0030_merge_all_heads_v5_6
Revises: v5_6_portal_email
Create Date: 2026-06-23

CRIT-05 FIX:
  The migration DAG had three unresolved heads after v5.6:
    - 0011_add_paymongo_subscription  (merged via 0029_merge_heads)
    - 0028_add_email_only_provider    (merged via 0029_merge_heads)
    - v5_6_portal_email               (latest leaf — no child until now)

  0029_merge_heads correctly merges 0011 + 0028.
  v5_6_portal_email depends on 0029_merge_heads.
  This migration is a topology-only revision that makes v5_6_portal_email
  the single resolved head so `flask db upgrade` works on a clean DB.

  Verification:
    flask db heads        # must return exactly one line: 0030_merge_all_heads_v5_6
    flask db upgrade      # must succeed on a clean schema
    flask db downgrade -1 # must succeed
    flask db upgrade      # must succeed again

  This migration performs NO DDL — it is purely a graph fix.
"""
from alembic import op
import sqlalchemy as sa

revision = '0030_merge_all_heads_v5_6'
down_revision = 'v5_6_portal_email'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No-op: topology-only merge migration."""
    pass


def downgrade() -> None:
    """No-op."""
    pass
