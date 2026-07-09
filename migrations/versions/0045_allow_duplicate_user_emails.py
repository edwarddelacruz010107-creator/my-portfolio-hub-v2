"""Allow duplicate user emails (remove unique constraint)

Revision ID: 0045_allow_duplicate_user_emails
Revises: 0044_hardening_tenant_slug_constraint
Create Date: 2026-07-04
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = '0045_allow_duplicate_user_emails'
down_revision = '0044_hardening_tenant_slug_constraint'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Drop unique constraint on users.email and add a non-unique index."""
    conn = op.get_bind()
    inspector = inspect(conn)

    # Drop any unique constraint that covers only the 'email' column.
    try:
        for uc in inspector.get_unique_constraints('users'):
            cols = uc.get('column_names') or uc.get('columns') or []
            if cols == ['email'] or (len(cols) == 1 and cols[0] == 'email'):
                try:
                    with op.batch_alter_table('users') as batch_op:
                        batch_op.drop_constraint(uc['name'], type_='unique')
                except Exception:
                    # Best-effort; continue
                    pass
    except Exception:
        # Inspector might not support get_unique_constraints on some DBs; best-effort only
        pass

    # Ensure a non-unique index exists for email lookups
    try:
        indexes = [ix['name'] for ix in inspector.get_indexes('users')]
        if 'ix_users_email' not in indexes:
            with op.batch_alter_table('users') as batch_op:
                batch_op.create_index('ix_users_email', ['email'])
    except Exception:
        pass


def downgrade() -> None:
    """Attempt to restore unique constraint on users.email.

    Note: This will fail if duplicate emails exist; downgrade is best-effort.
    """
    try:
        with op.batch_alter_table('users') as batch_op:
            batch_op.create_unique_constraint('uq_users_email', ['email'])
    except Exception:
        # If duplicates exist, creating the unique constraint will raise — leave as-is
        pass