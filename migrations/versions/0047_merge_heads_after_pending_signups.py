"""Merge pending signup and platform setting migration heads.

Revision ID: 0047_merge_heads_after_pending_signups
Revises: 0046_add_pending_signups, 0044_widen_platform_setting_value
Create Date: 2026-07-05
"""

# revision identifiers, used by Alembic.
revision = '0047_merge_heads_after_pending_signups'
down_revision = ('0046_add_pending_signups', '0044_widen_platform_setting_value')
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No-op merge migration; reconciles Alembic branches only."""
    pass


def downgrade() -> None:
    """No-op merge migration; downgrade follows the merged branch history."""
    pass
