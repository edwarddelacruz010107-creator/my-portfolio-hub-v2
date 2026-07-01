"""0041_add_google_oauth_fields — Google Sign-In columns on users

Revision ID: 0041_add_google_oauth_fields
Revises: 0040_invoices
Create Date: 2026-07-01

Context:
  Adds columns for Google OAuth as a SECOND LOGIN METHOD for existing
  users (see app/auth/oauth.py). This migration does NOT create or modify
  any tenant/user rows — purely additive schema.

Changes:
  users table:
    1. google_id      VARCHAR(255)  NULLABLE, UNIQUE  — Google 'sub' claim
    2. auth_provider  VARCHAR(20)   NOT NULL, default 'local'
    3. avatar_url     VARCHAR(500)  NULLABLE
    Index: ix_users_google_id (google_id)

Design notes:
  - google_id/avatar_url are NULLABLE with no server_default — every
    existing user row is valid immediately, no backfill needed.
  - auth_provider is NOT NULL but uses server_default='local' so the
    ALTER on existing Postgres rows does not require a table rewrite
    pass with NULLs, and matches the ORM default in app/models/core.py.
  - Idempotent: every op is guarded by _has_column / _has_index checks,
    matching 0027_contact_delivery_fields.py and every migration since.
  - batch_alter_table used throughout for SQLite (local dev) compatibility.

Backwards compatibility:
  - Existing users: auth_provider becomes 'local' (accurate — they have
    no Google identity yet), google_id/avatar_url are NULL.
  - Downgrade: drops the index then all three columns cleanly.
"""

from alembic import op
import sqlalchemy as sa


revision      = '0041_add_google_oauth_fields'
down_revision = '0040_invoices'
branch_labels = None
depends_on    = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _has_column(inspector, table: str, column: str) -> bool:
    return any(c.get('name') == column for c in inspector.get_columns(table))


def _has_index(inspector, table: str, index: str) -> bool:
    return any(i.get('name') == index for i in inspector.get_indexes(table))


# ── Upgrade ───────────────────────────────────────────────────────────────────

def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table('users'):
        return

    with op.batch_alter_table('users', schema=None) as batch:
        if not _has_column(inspector, 'users', 'google_id'):
            batch.add_column(sa.Column('google_id', sa.String(255), nullable=True))

        if not _has_column(inspector, 'users', 'auth_provider'):
            batch.add_column(
                sa.Column(
                    'auth_provider', sa.String(20), nullable=False,
                    server_default='local',
                )
            )

        if not _has_column(inspector, 'users', 'avatar_url'):
            batch.add_column(sa.Column('avatar_url', sa.String(500), nullable=True))

        if not _has_index(inspector, 'users', 'ix_users_google_id'):
            batch.create_index('ix_users_google_id', ['google_id'], unique=True)

    # Drop the server_default after backfill so future inserts rely on the
    # ORM-level default (app/models/core.py) rather than a DB-side default —
    # keeps a single source of truth, matches the rest of this codebase.
    with op.batch_alter_table('users', schema=None) as batch:
        batch.alter_column('auth_provider', server_default=None)


# ── Downgrade ─────────────────────────────────────────────────────────────────

def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table('users'):
        return

    with op.batch_alter_table('users', schema=None) as batch:
        if _has_index(inspector, 'users', 'ix_users_google_id'):
            batch.drop_index('ix_users_google_id')
        for col in ('avatar_url', 'auth_provider', 'google_id'):
            if _has_column(inspector, 'users', col):
                batch.drop_column(col)
