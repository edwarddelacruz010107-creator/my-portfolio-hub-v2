"""Add contact_email to tenants, PasswordResetOTP table, GlobalEmailConfig table (v3.8)

Revision ID: 0018_auth_otp_web3forms
Revises: 0017_activitylog_user_fields
Create Date: 2026-06-10

Changes:
  1. tenants.contact_email  — nullable VARCHAR(120) with index
  2. password_reset_otps    — new table (all OTP-based reset flows)
  3. global_email_config    — new table (superadmin Web3Forms + OTP settings)
  4. Backfill: tenants without contact_email get tenant.email as fallback

Rollback support:
  • downgrade() drops the new tables and column cleanly.
  • Zero data loss on existing rows.
"""
from alembic import op
import sqlalchemy as sa

revision     = '0018_auth_otp_web3forms'
down_revision = '0017_activitylog_user_fields'
branch_labels = None
depends_on    = None


def upgrade():
    # ── 1. tenants.contact_email ─────────────────────────────────────────────
    with op.batch_alter_table('tenants', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('contact_email', sa.String(120), nullable=True)
        )
        batch_op.create_index('ix_tenants_contact_email', ['contact_email'])

    # Backfill: copy tenant.email into contact_email for existing rows
    op.execute(
        "UPDATE tenants SET contact_email = email WHERE contact_email IS NULL AND email != ''"
    )

    # ── 2. password_reset_otps ───────────────────────────────────────────────
    op.create_table(
        'password_reset_otps',
        sa.Column('id',          sa.Integer,                nullable=False),
        sa.Column('user_type',   sa.String(20),             nullable=False),
        sa.Column('user_id',     sa.Integer,                nullable=False),
        sa.Column('tenant_id',   sa.Integer,                nullable=True),
        sa.Column('email',       sa.String(120),            nullable=False),
        sa.Column('otp_hash',    sa.String(64),             nullable=False),
        sa.Column('attempts',    sa.Integer,   default=0,   nullable=False),
        sa.Column('expires_at',  sa.DateTime(timezone=True), nullable=False),
        sa.Column('used',        sa.Boolean,   default=False, nullable=False),
        sa.Column('created_at',  sa.DateTime(timezone=True), nullable=True),
        sa.Column('ip_address',  sa.String(45),  nullable=True),
        sa.Column('user_agent',  sa.String(300), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_otp_user_type_user_id', 'password_reset_otps',
                    ['user_type', 'user_id'])
    op.create_index('ix_otp_tenant_id',  'password_reset_otps', ['tenant_id'])
    op.create_index('ix_otp_expires_at', 'password_reset_otps', ['expires_at'])

    # ── 3. global_email_config ───────────────────────────────────────────────
    op.create_table(
        'global_email_config',
        sa.Column('id',                 sa.Integer,     nullable=False),
        sa.Column('web3forms_key',      sa.Text,        default='',    nullable=True),
        sa.Column('sender_name',        sa.String(200), default='Portfolio CMS', nullable=True),
        sa.Column('sender_email',       sa.String(200), default='',    nullable=True),
        sa.Column('otp_expiry_minutes', sa.Integer,     default=10,    nullable=False),
        sa.Column('recovery_enabled',   sa.Boolean,     default=True,  nullable=False),
        sa.Column('updated_at',         sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_by',         sa.String(120), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    # Seed singleton row with safe defaults
    op.execute(
        "INSERT INTO global_email_config (id, sender_name, otp_expiry_minutes, recovery_enabled) "
        "VALUES (1, 'Portfolio CMS', 10, 1)"
    )


def downgrade():
    # Drop in reverse order
    op.drop_table('global_email_config')

    op.drop_index('ix_otp_expires_at', table_name='password_reset_otps')
    op.drop_index('ix_otp_tenant_id',  table_name='password_reset_otps')
    op.drop_index('ix_otp_user_type_user_id', table_name='password_reset_otps')
    op.drop_table('password_reset_otps')

    with op.batch_alter_table('tenants', schema=None) as batch_op:
        batch_op.drop_index('ix_tenants_contact_email')
        batch_op.drop_column('contact_email')
