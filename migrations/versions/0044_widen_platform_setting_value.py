"""0044_widen_platform_setting_value — widen platform_settings.value to TEXT

Revision ID: 0044_widen_platform_setting_value
Revises: bf77d855483c
Create Date: 2026-07-03

Widens platform_settings.value from VARCHAR(500) to TEXT so it can hold
JSON-encoded structured settings (e.g. per-plan Pricing CMS overrides via
PlatformSetting.set_json/get_json) without risking silent truncation.
Purely additive/widening — no data loss, backward compatible with every
existing VARCHAR(500) value.
"""

from alembic import op
import sqlalchemy as sa


revision = '0044_widen_platform_setting_value'
down_revision = 'bf77d855483c'
branch_labels = None
depends_on = None


def _has_column(inspector, table: str, column: str) -> bool:
    return any(c.get('name') == column for c in inspector.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table('platform_settings'):
        return
    if not _has_column(inspector, 'platform_settings', 'value'):
        return

    with op.batch_alter_table('platform_settings', schema=None) as batch:
        batch.alter_column(
            'value',
            existing_type=sa.String(500),
            type_=sa.Text(),
            existing_nullable=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table('platform_settings'):
        return
    if not _has_column(inspector, 'platform_settings', 'value'):
        return

    # NOTE: downgrade truncates any value > 500 chars (e.g. Pricing CMS
    # JSON blobs). Safe only if run before Pricing CMS overrides are saved,
    # or after they've been intentionally cleared.
    with op.batch_alter_table('platform_settings', schema=None) as batch:
        batch.alter_column(
            'value',
            existing_type=sa.Text(),
            type_=sa.String(500),
            existing_nullable=False,
        )
