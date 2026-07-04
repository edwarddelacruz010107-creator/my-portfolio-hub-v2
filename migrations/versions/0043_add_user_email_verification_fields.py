"""0043_add_user_email_verification_fields — add email verification fields to users

Revision ID: 0043_add_user_email_verification_fields
Revises: 0041_add_google_oauth_fields
Create Date: 2026-07-03

Adds the columns required by local signup and email verification.
"""

from alembic import op
import sqlalchemy as sa


revision = '0043_add_user_email_verification_fields'
down_revision = '0041_add_google_oauth_fields'
branch_labels = None
depends_on = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _has_column(inspector, table: str, column: str) -> bool:
    return any(c.get('name') == column for c in inspector.get_columns(table))


# ── Upgrade ───────────────────────────────────────────────────────────────────

def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table('users'):
        return

    with op.batch_alter_table('users', schema=None) as batch:
        if not _has_column(inspector, 'users', 'email_verified'):
            batch.add_column(
                sa.Column(
                    'email_verified',
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )
        if not _has_column(inspector, 'users', 'email_verification_token'):
            batch.add_column(
                sa.Column(
                    'email_verification_token',
                    sa.String(64),
                    nullable=True,
                    unique=True,
                )
            )
        if not _has_column(inspector, 'users', 'email_verification_expires'):
            batch.add_column(
                sa.Column(
                    'email_verification_expires',
                    sa.DateTime(timezone=True),
                    nullable=True,
                )
            )
        if not _has_column(inspector, 'users', 'last_login_user_agent'):
            batch.add_column(
                sa.Column(
                    'last_login_user_agent',
                    sa.String(255),
                    nullable=True,
                )
            )

    # Remove the DB server_default for email_verified after backfill.
    with op.batch_alter_table('users', schema=None) as batch:
        batch.alter_column('email_verified', server_default=None)


# ── Downgrade ─────────────────────────────────────────────────────────────────

def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table('users'):
        return

    with op.batch_alter_table('users', schema=None) as batch:
        for col in (
            'last_login_user_agent',
            'email_verification_expires',
            'email_verification_token',
            'email_verified',
        ):
            if _has_column(inspector, 'users', col):
                batch.drop_column(col)
