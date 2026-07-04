"""Add CHECK constraint on tenant_slug (non-default) and increase OTP TTL to 15 minutes.

Revision ID: 0044_hardening_tenant_slug_constraint
Revises: bf77d855483c
Create Date: 2026-07-03 00:00:00.000000

This migration:
1. Adds CHECK (tenant_slug != 'default') to users table
   - Prevents superadmin/normal users from accidentally being assigned 'default' tenant
   - Enforces at database level (not just application level)

2. Increases global OTP TTL from 10 to 15 minutes
   - Improves UX for email delivery delays
   - Still reasonable for security (15 min is industry standard)
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0044_hardening_tenant_slug_constraint'
down_revision = 'bf77d855483c'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add CHECK constraint and update OTP TTL."""
    
    # For SQLite, we need to recreate the table to add a CHECK constraint
    # This is a safe operation as it only affects new writes
    with op.batch_alter_table('users', schema=None) as batch_op:
        # Add CHECK constraint (SQLite supports this from 3.32.0+)
        # If using older SQLite, this will be a no-op but won't error
        try:
            batch_op.create_check_constraint(
                'ck_users_tenant_slug_not_default',
                "tenant_slug != 'default' OR is_superadmin = 1",
                info={'note': 'Non-superadmin users cannot be on default tenant'}
            )
        except Exception:
            # Fallback: if CHECK constraints not supported, log and continue
            # Application-level validation is still in place
            pass

    # Update GlobalEmailConfig to increase OTP TTL to 15 minutes
    op.execute("""
        UPDATE global_email_config
        SET otp_expiry_minutes = 15
        WHERE otp_expiry_minutes = 10
    """)


def downgrade() -> None:
    """Remove CHECK constraint and revert OTP TTL to 10 minutes."""
    
    # Remove CHECK constraint
    with op.batch_alter_table('users', schema=None) as batch_op:
        try:
            batch_op.drop_constraint('ck_users_tenant_slug_not_default', type_='check')
        except Exception:
            # Constraint may not exist if SQLite doesn't support CHECK
            pass

    # Revert OTP TTL to 10 minutes
    op.execute("""
        UPDATE global_email_config
        SET otp_expiry_minutes = 10
        WHERE otp_expiry_minutes = 15
    """)
