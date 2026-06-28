"""Fix duplicate index declarations (remove redundant index=True)

This migration documents the removal of implicit index=True declarations
that were duplicated with explicit db.Index() in __table_args__.

Revision ID: 0026_fix_duplicate_indexes
Revises: 0025
Create Date: 2026-06-18

Changes:
  1. Inquiry.tenant_id — removed duplicate index=True
  2. Inquiry.tenant_slug — removed duplicate index=True
  3. SubscriptionNotification.tenant_id — removed duplicate index=True
  4. ActivityLog.tenant_slug — removed duplicate index=True
  5. ActivityLog.user_id — removed duplicate index=True

Schema Impact:
  - No database changes required (indexes already exist)
  - Model metadata corrected to match actual schema
  - Prevents "relation already exists" errors on db.create_all()

Rollback: No database operations (metadata-only fix)
"""

from alembic import op
import sqlalchemy as sa

revision = '0026_fix_duplicate_indexes'
down_revision = '0025'
branch_labels = None
depends_on = None


def upgrade():
    """
    This migration is a documentation-only fix.
    No database schema changes required.
    
    The duplicate indexes already exist in the database from previous
    migrations. This migration documents the correction in SQLAlchemy
    model definitions to align with the actual schema.
    """
    pass


def downgrade():
    """
    No rollback needed — this is a model metadata correction.
    """
    pass
