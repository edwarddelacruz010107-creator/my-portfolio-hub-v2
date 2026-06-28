"""
0036 — Administrator Plan Bootstrap (v6.7)

Introduces the ADMINISTRATOR reserved system plan for the default portfolio
tenant (slug = 'default').

Changes:
  1. tenants.plan — extend the allowed plan strings to include 'Administrator'
     (no column type change needed; it's VARCHAR(50)).
  2. DATA: set plan = 'Administrator' on the default tenant row.

This migration is SAFE:
  • Purely additive: no column types changed, no columns dropped.
  • One targeted UPDATE for tenant where slug = 'default'.
  • Downgrade restores plan = 'Basic' for the default tenant.

Down-revision: 0035_theme_catalog_extended
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# Alembic metadata
revision      = '0036_administrator_plan'
down_revision = '0035_theme_catalog_extended'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    # Set the Administrator plan on the default tenant.
    # Uses raw SQL so we don't depend on ORM layer during migration.
    op.execute(
        sa.text(
            "UPDATE tenants SET plan = 'Administrator' WHERE slug = 'default'"
        )
    )


def downgrade() -> None:
    # Revert to Basic — removes Administrator designation from default tenant.
    op.execute(
        sa.text(
            "UPDATE tenants SET plan = 'Basic' WHERE slug = 'default'"
        )
    )
