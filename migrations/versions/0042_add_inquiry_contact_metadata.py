"""0042_add_inquiry_contact_metadata — landing contact enrichment fields

Revision ID: 0042_add_inquiry_contact_metadata
Revises: 0041_add_google_oauth_fields
Create Date: 2026-07-03

Adds landing contact metadata fields to the inquiries table so public
form submissions can capture phone/company, admin notification status,
auto-reply delivery state, and spam classification flags.
"""

from alembic import op
import sqlalchemy as sa

revision = '0042_add_inquiry_contact_metadata'
down_revision = '0041_add_google_oauth_fields'
branch_labels = None
depends_on = None


def _has_column(inspector, table: str, column: str) -> bool:
    return any(c.get('name') == column for c in inspector.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table('inquiries'):
        with op.batch_alter_table('inquiries', schema=None) as batch:
            if not _has_column(inspector, 'inquiries', 'phone'):
                batch.add_column(sa.Column('phone', sa.String(50), nullable=True))
            if not _has_column(inspector, 'inquiries', 'company'):
                batch.add_column(sa.Column('company', sa.String(200), nullable=True))
            if not _has_column(inspector, 'inquiries', 'admin_notified'):
                batch.add_column(sa.Column('admin_notified', sa.Boolean(), nullable=False, server_default='0'))
            if not _has_column(inspector, 'inquiries', 'auto_reply_sent'):
                batch.add_column(sa.Column('auto_reply_sent', sa.Boolean(), nullable=False, server_default='0'))
            if not _has_column(inspector, 'inquiries', 'spam_score'):
                batch.add_column(sa.Column('spam_score', sa.Float(), nullable=False, server_default='0'))
            if not _has_column(inspector, 'inquiries', 'is_spam'):
                batch.add_column(sa.Column('is_spam', sa.Boolean(), nullable=False, server_default='0'))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table('inquiries'):
        with op.batch_alter_table('inquiries', schema=None) as batch:
            for col in ('is_spam', 'spam_score', 'auto_reply_sent', 'admin_notified', 'company', 'phone'):
                if _has_column(inspector, 'inquiries', col):
                    batch.drop_column(col)
