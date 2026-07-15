"""add_license_and_trial_columns

Revision ID: 0003_add_license_and_trial_columns
Revises: 0002_tenant_url_refactor
Create Date: 2026-06-01

Add free trial and license metadata columns to the profile table.
"""

from alembic import op
import sqlalchemy as sa

revision = '0003_add_license_and_trial_columns'
down_revision = '7d0f3492b2b3'  # FIXED: was '0002_tenant_url_refactor' (file does not exist)
branch_labels = None
depends_on = None


def upgrade():
    # Profile is owned by the independent tenant migration history.  This
    # historical core revision remains connected for existing version tables,
    # but must not require tenant tables to exist in the core database.
    return

    try:
        op.add_column('profile', sa.Column('free_trial_days', sa.Integer(), nullable=False, server_default='0'))
    except Exception:
        pass

    try:
        op.add_column('profile', sa.Column('free_trial_ends', sa.DateTime(timezone=True), nullable=True))
    except Exception:
        pass

    try:
        op.add_column('profile', sa.Column('license_key', sa.String(length=255), nullable=False, server_default=''))
    except Exception:
        pass

    try:
        op.add_column('profile', sa.Column('license_plan', sa.String(length=50), nullable=False, server_default=''))
    except Exception:
        pass

    try:
        op.add_column('profile', sa.Column('license_active', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    except Exception:
        pass

    try:
        op.add_column('profile', sa.Column('license_activated_at', sa.DateTime(timezone=True), nullable=True))
    except Exception:
        pass

    try:
        op.add_column('profile', sa.Column('internal_notes', sa.Text(), nullable=False, server_default=''))
    except Exception:
        pass

    try:
        op.add_column('profile', sa.Column('meta_title', sa.String(length=200), nullable=False, server_default=''))
    except Exception:
        pass

    try:
        op.add_column('profile', sa.Column('meta_description', sa.String(length=300), nullable=False, server_default=''))
    except Exception:
        pass

    try:
        op.add_column('profile', sa.Column('og_image', sa.String(length=255), nullable=False, server_default=''))
    except Exception:
        pass


def downgrade():
    # No destructive downgrade provided for license/trial metadata.
    pass
