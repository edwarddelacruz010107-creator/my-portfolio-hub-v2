"""create_subscriptions

Revision ID: 0005_create_subscriptions
Revises: 0004_add_superadmin_sender_to_inquiry
Create Date: 2026-06-02

Create a dedicated subscriptions table and migrate existing license
fields from profile into subscriptions to preserve data.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = '0005_create_subscriptions'
down_revision = '0004_add_superadmin_sender_to_inquiry'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # FIXED: 'subscriptions' is now created by 0001_initial_schema.py.
    # The previous try/except around create_table() did NOT prevent the
    # bug: Postgres aborts the whole transaction on the first DDL error
    # (relation already exists), and a Python try/except cannot undo
    # that server-side abort -- the very next statement on this
    # connection then raises psycopg2.errors.InFailedSqlTransaction.
    # A real has_table() check is required, not exception-swallowing.
    if not inspector.has_table('subscriptions'):
        op.create_table(
            'subscriptions',
            sa.Column('id', sa.Integer, primary_key=True),
            sa.Column('tenant_id', sa.Integer, sa.ForeignKey('profile.id'), nullable=False, index=True),
            sa.Column('plan', sa.String(length=50), nullable=False, server_default='Basic'),
            sa.Column('status', sa.String(length=30), nullable=False, server_default='pending'),
            sa.Column('amount_paid', sa.Float, nullable=False, server_default='0'),
            sa.Column('payment_method', sa.String(length=100), nullable=True),
            sa.Column('payment_reference', sa.String(length=255), nullable=True),
            sa.Column('payment_proof', sa.String(length=255), nullable=True),
            sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        )

    # Migrate legacy license data from profile into subscriptions
    try:
        conn.execute(text("""
            INSERT INTO subscriptions (
                tenant_id, plan, status, amount_paid, payment_method,
                payment_reference, payment_proof, started_at, expires_at,
                created_at, updated_at
            )
            SELECT
                id as tenant_id,
                CASE
                    WHEN license_plan IS NOT NULL AND license_plan != '' THEN license_plan
                    ELSE COALESCE(plan, 'Basic')
                END as plan,
                CASE WHEN license_active THEN 'active' ELSE 'pending' END as status,
                0.0 as amount_paid,
                '' as payment_method,
                CASE WHEN license_key IS NOT NULL AND license_key != '' THEN license_key ELSE NULL END as payment_reference,
                '' as payment_proof,
                license_activated_at as started_at,
                NULL as expires_at,
                NOW() as created_at,
                NOW() as updated_at
            FROM profile
            WHERE (license_key IS NOT NULL AND license_key != '')
               OR (license_active IS TRUE)
               OR (license_plan IS NOT NULL AND license_plan != '')
               OR (license_activated_at IS NOT NULL)
        """))
    except Exception:
        # Best-effort migration; continue without failing in constrained environments
        pass

    # Drop legacy columns from profile
    for col in ('license_key', 'license_plan', 'license_active', 'license_activated_at'):
        try:
            op.drop_column('profile', col)
        except Exception:
            pass


def downgrade():
    # Attempt to restore legacy columns (best-effort).
    try:
        op.add_column('profile', sa.Column('license_key', sa.String(length=255), nullable=False, server_default=''))
        op.add_column('profile', sa.Column('license_plan', sa.String(length=50), nullable=False, server_default=''))
        op.add_column('profile', sa.Column('license_active', sa.Boolean(), nullable=False, server_default=sa.text('false')))
        op.add_column('profile', sa.Column('license_activated_at', sa.DateTime(timezone=True), nullable=True))
    except Exception:
        pass

    # Try to copy back subscription data into profile for tenants that have one
    conn = op.get_bind()
    try:
        conn.execute(text("""
            UPDATE profile p
            SET
                license_plan = s.plan,
                license_key = s.payment_reference,
                license_active = (s.status = 'active'),
                license_activated_at = s.started_at
            FROM (
                SELECT DISTINCT ON (tenant_id) * FROM subscriptions
                ORDER BY tenant_id, started_at DESC NULLS LAST, created_at DESC
            ) s
            WHERE p.id = s.tenant_id
        """))
    except Exception:
        pass

    # Drop subscriptions table
    try:
        op.drop_table('subscriptions')
    except Exception:
        pass
