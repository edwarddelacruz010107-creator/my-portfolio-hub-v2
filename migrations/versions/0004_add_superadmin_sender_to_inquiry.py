"""add_superadmin_sender_to_inquiry

Revision ID: 0004_add_superadmin_sender_to_inquiry
Revises: 0003_add_license_and_trial_columns
Create Date: 2026-06-02

Add sender metadata to Inquiry so superadmin-originated messages can be distinguished.
"""

from alembic import op
import sqlalchemy as sa

revision = '0004_add_superadmin_sender_to_inquiry'
down_revision = '0003_add_license_and_trial_columns'
branch_labels = None
depends_on = None


def upgrade():
    try:
        op.add_column('inquiry', sa.Column('sender', sa.String(length=50), nullable=False, server_default='visitor'))
    except Exception:
        pass


def downgrade():
    try:
        op.drop_column('inquiry', 'sender')
    except Exception:
        pass
