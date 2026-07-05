"""Restrict duplicate user emails to the protected owner email only.

Revision ID: 0048_restrict_duplicate_user_emails
Revises: 0047_merge_heads_after_pending_signups
Create Date: 2026-07-05
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = '0048_restrict_duplicate_user_emails'
down_revision = '0047_merge_heads_after_pending_signups'
branch_labels = None
depends_on = None

OWNER_SHARED_EMAIL = 'delacruzedward735@gmail.com'
INDEX_NAME = 'uq_users_email_except_owner_shared'


def _has_index(inspector, table: str, name: str) -> bool:
    try:
        return any(ix.get('name') == name for ix in inspector.get_indexes(table))
    except Exception:
        return False


def _preflight_duplicates(conn) -> None:
    rows = conn.execute(sa.text("""
        SELECT lower(email) AS email_key, COUNT(*) AS n
        FROM users
        WHERE email IS NOT NULL
          AND lower(email) <> :owner_email
        GROUP BY lower(email)
        HAVING COUNT(*) > 1
    """), {"owner_email": OWNER_SHARED_EMAIL}).fetchall()
    if rows:
        details = ', '.join(f"{row.email_key} ({row.n})" for row in rows[:10])
        raise RuntimeError(
            'Cannot add duplicate-email protection: duplicate non-owner user emails exist. '
            f'Resolve these first: {details}'
        )

    owner_count = conn.execute(sa.text("""
        SELECT COUNT(*)
        FROM users
        WHERE email IS NOT NULL
          AND lower(email) = :owner_email
    """), {"owner_email": OWNER_SHARED_EMAIL}).scalar() or 0
    if owner_count > 2:
        raise RuntimeError(
            'Cannot add duplicate-email protection: the platform-owner email appears '
            f'{owner_count} times. Only the SuperAdmin and default portfolio administrator are allowed.'
        )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table('users'):
        return

    _preflight_duplicates(bind)

    if _has_index(inspector, 'users', INDEX_NAME):
        return

    dialect = bind.dialect.name
    if dialect in {'postgresql', 'sqlite'}:
        op.execute(sa.text(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {INDEX_NAME} "
            "ON users (lower(email)) "
            f"WHERE lower(email) <> '{OWNER_SHARED_EMAIL}'"
        ))
    else:
        # Application-level enforcement in app.services.auth.email_policy remains
        # the source of truth for dialects without portable partial expression indexes.
        pass


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table('users') or not _has_index(inspector, 'users', INDEX_NAME):
        return
    op.drop_index(INDEX_NAME, table_name='users')
