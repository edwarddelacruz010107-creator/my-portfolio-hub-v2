"""
v5.6 — Per-portal MailerSend configuration columns

Adds separate API key, sender email, and sender name columns for the
Admin Portal and Superadmin Portal to GlobalEmailConfig.

Each column falls back to the shared key/sender if not set, so no
disruption to existing deployments.

Usage:
    flask db upgrade   (chained onto 0029_merge_heads — runs automatically
    as part of the normal Alembic upgrade path, no longer a separate root)
"""

# Flask-Migrate / Alembic revision
revision = 'v5_6_portal_email'
down_revision = '0029_merge_heads'
branch_labels = None
depends_on = None


def upgrade():
    """Add per-portal email config columns."""
    from alembic import op
    import sqlalchemy as sa

    with op.batch_alter_table('global_email_config', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'admin_mailersend_api_key', sa.Text(), nullable=True, server_default=''
        ))
        batch_op.add_column(sa.Column(
            'admin_sender_name', sa.String(200), nullable=True, server_default=''
        ))
        batch_op.add_column(sa.Column(
            'admin_sender_email', sa.String(200), nullable=True, server_default=''
        ))
        batch_op.add_column(sa.Column(
            'superadmin_mailersend_api_key', sa.Text(), nullable=True, server_default=''
        ))
        batch_op.add_column(sa.Column(
            'superadmin_sender_name', sa.String(200), nullable=True, server_default=''
        ))
        batch_op.add_column(sa.Column(
            'superadmin_sender_email', sa.String(200), nullable=True, server_default=''
        ))


def downgrade():
    """Remove per-portal email config columns."""
    from alembic import op

    with op.batch_alter_table('global_email_config', schema=None) as batch_op:
        for col in (
            'admin_mailersend_api_key', 'admin_sender_name', 'admin_sender_email',
            'superadmin_mailersend_api_key', 'superadmin_sender_name', 'superadmin_sender_email',
        ):
            batch_op.drop_column(col)


# ── Raw SQL (for manual execution on PostgreSQL / SQLite) ─────────────────────
RAW_SQL_POSTGRES = """
ALTER TABLE global_email_config
    ADD COLUMN IF NOT EXISTS admin_mailersend_api_key     TEXT         DEFAULT '',
    ADD COLUMN IF NOT EXISTS admin_sender_name            VARCHAR(200) DEFAULT '',
    ADD COLUMN IF NOT EXISTS admin_sender_email           VARCHAR(200) DEFAULT '',
    ADD COLUMN IF NOT EXISTS superadmin_mailersend_api_key TEXT        DEFAULT '',
    ADD COLUMN IF NOT EXISTS superadmin_sender_name        VARCHAR(200) DEFAULT '',
    ADD COLUMN IF NOT EXISTS superadmin_sender_email       VARCHAR(200) DEFAULT '';
"""

RAW_SQL_SQLITE = """
ALTER TABLE global_email_config ADD COLUMN admin_mailersend_api_key TEXT DEFAULT '';
ALTER TABLE global_email_config ADD COLUMN admin_sender_name VARCHAR(200) DEFAULT '';
ALTER TABLE global_email_config ADD COLUMN admin_sender_email VARCHAR(200) DEFAULT '';
ALTER TABLE global_email_config ADD COLUMN superadmin_mailersend_api_key TEXT DEFAULT '';
ALTER TABLE global_email_config ADD COLUMN superadmin_sender_name VARCHAR(200) DEFAULT '';
ALTER TABLE global_email_config ADD COLUMN superadmin_sender_email VARCHAR(200) DEFAULT '';
"""
