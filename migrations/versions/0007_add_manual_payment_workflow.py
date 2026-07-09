"""add_manual_payment_workflow

Revision ID: 0007_add_manual_payment_workflow
Revises: 0006_create_tenants
Create payment instruction and submission tables for manual QR/bank payment workflow.
"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime, timezone

revision = '0007_add_manual_payment_workflow'
down_revision = '0006_create_tenants'
branch_labels = None
depends_on = None


def upgrade():
    now = datetime.now(timezone.utc)
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # FIXED: both tables are now created by 0001_initial_schema.py.
    # try/except around create_table does not protect against
    # InFailedSqlTransaction (Postgres aborts the whole transaction on
    # the DDL error; Python catching the exception doesn't undo that).
    if not inspector.has_table('payment_instructions'):
        op.create_table(
            'payment_instructions',
            sa.Column('id', sa.Integer, primary_key=True),
            sa.Column('tenant_id', sa.Integer, sa.ForeignKey('tenants.id'), nullable=True, index=True),
            sa.Column('method', sa.String(length=50), nullable=False, server_default=''),
            sa.Column('title', sa.String(length=120), nullable=False, server_default=''),
            sa.Column('description', sa.Text, nullable=True),
            sa.Column('account_name', sa.String(length=120), nullable=False, server_default=''),
            sa.Column('account_number', sa.String(length=120), nullable=False, server_default=''),
            sa.Column('bank_name', sa.String(length=120), nullable=False, server_default=''),
            sa.Column('qr_image', sa.String(length=255), nullable=False, server_default=''),
            sa.Column('is_active', sa.Boolean, nullable=False, server_default=sa.text('true')),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        )

    if not inspector.has_table('payment_submissions'):
        op.create_table(
            'payment_submissions',
            sa.Column('id', sa.Integer, primary_key=True),
            sa.Column('tenant_id', sa.Integer, sa.ForeignKey('tenants.id'), nullable=False, index=True),
            sa.Column('plan', sa.String(length=50), nullable=False, server_default='Basic'),
            sa.Column('amount_paid', sa.Float, nullable=False, server_default='0'),
            sa.Column('payment_method', sa.String(length=100), nullable=False, server_default=''),
            sa.Column('payment_reference', sa.String(length=255), nullable=False, server_default=''),
            sa.Column('payment_proof', sa.String(length=255), nullable=False, server_default=''),
            sa.Column('note', sa.Text, nullable=True),
            sa.Column('status', sa.String(length=30), nullable=False, server_default='pending'),
            sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
            sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('reviewed_by', sa.String(length=120), nullable=False, server_default=''),
            sa.Column('review_notes', sa.Text, nullable=True),
        )


def downgrade():
    try:
        op.drop_table('payment_submissions')
    except Exception:
        pass
    try:
        op.drop_table('payment_instructions')
    except Exception:
        pass
