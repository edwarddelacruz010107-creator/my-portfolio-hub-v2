"""Compatibility bridge for deployments that referenced the short 0054 ID.

Revision ID: 0054
Revises: 0054_oauth_local_account_setup

This no-op migration intentionally preserves the legacy revision identifier
"0054" so older/newer release artifacts can share one valid Alembic chain.
"""

revision = '0054'
down_revision = '0054_oauth_local_account_setup'
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
