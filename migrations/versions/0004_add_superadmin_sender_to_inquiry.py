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
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table('inquiries'):
        return
    columns = {column['name'] for column in inspector.get_columns('inquiries')}
    if 'sender' not in columns:
        op.add_column(
            'inquiries',
            sa.Column('sender', sa.String(length=50), nullable=False, server_default='visitor'),
        )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table('inquiries'):
        columns = {column['name'] for column in inspector.get_columns('inquiries')}
        if 'sender' in columns:
            op.drop_column('inquiries', 'sender')
