"""0028_add_email_only_provider — Add 'email_only' form provider to ENUM

Revision ID: 0028_add_email_only_provider
Revises: 0027_contact_delivery_fields
Create Date: 2026-06-19

Changes:
  1. Add 'email_only' value to form_provider_enum (PostgreSQL).
  2. Idempotent: uses ALTER TYPE ... ADD VALUE IF NOT EXISTS (PostgreSQL 9.1+).
  3. No data loss. No table recreation. No downtime.
  4. SQLite: no-op (uses simple string constraint, not real ENUM).

Rationale:
  Model TenantFormSettings defines VALID_PROVIDERS = ('basin', 'email_only',
  'web3forms', 'disabled'), but the ENUM created in 0022 only includes
  ('basin', 'web3forms', 'disabled'). This migration safely adds the missing
  'email_only' value so provider='email_only' can be saved successfully.

Backwards compatibility:
  - Existing rows with basin|web3forms|disabled are unaffected.
  - New rows can now use provider='email_only'.
  - Downgrade: ENUM cannot be modified; this is irreversible (PostgreSQL limitation).
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '0028_add_email_only_provider'
down_revision = '0027_contact_delivery_fields'
branch_labels = None
depends_on = None


def upgrade():
    """Add 'email_only' to form_provider_enum (PostgreSQL-safe, idempotent)."""
    bind = op.get_bind()
    dialect = bind.dialect.name
    
    if dialect == 'postgresql':
        # PostgreSQL: use ALTER TYPE ... ADD VALUE IF NOT EXISTS (9.1+)
        # This is idempotent — if the value already exists, it's silently ignored.
        op.execute(
            "ALTER TYPE form_provider_enum ADD VALUE IF NOT EXISTS 'email_only' AFTER 'basin'"
        )
    # SQLite: no-op (uses simple string constraint, not a real ENUM type)
    # The model still validates provider against VALID_PROVIDERS


def downgrade():
    """
    Downgrade: ENUM value removal is not supported in PostgreSQL (would require
    table recreation). This migration is effectively irreversible — once 'email_only'
    is added, it cannot be removed without dropping and recreating the type.
    
    If rollback is absolutely necessary, use raw SQL:
      DROP TYPE form_provider_enum;
    Then manually recreate it with only the original values (postgres only).
    
    For production, this migration should never be downgraded.
    """
    pass
