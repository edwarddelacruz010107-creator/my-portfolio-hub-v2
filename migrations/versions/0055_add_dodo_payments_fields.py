"""add Dodo Payments subscription identifiers

Revision ID: 0055
Revises: 0054
"""
from alembic import op
import sqlalchemy as sa

revision = '0055'
down_revision = '0054'
branch_labels = None
depends_on = None


def _column_names(inspector, table_name):
    try:
        return {column["name"] for column in inspector.get_columns(table_name)}
    except Exception:
        return set()


def _index_names(inspector, table_name):
    try:
        return {index["name"] for index in inspector.get_indexes(table_name)}
    except Exception:
        return set()


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("subscriptions"):
        return

    existing_columns = _column_names(inspector, "subscriptions")
    columns = (
        ("payment_provider", sa.String(length=30)),
        ("dodo_checkout_session_id", sa.String(length=255)),
        ("dodo_customer_id", sa.String(length=255)),
        ("dodo_subscription_id", sa.String(length=255)),
        ("dodo_payment_id", sa.String(length=255)),
        ("provider_currency", sa.String(length=3)),
    )
    with op.batch_alter_table("subscriptions") as batch:
        for name, column_type in columns:
            if name not in existing_columns:
                batch.add_column(sa.Column(name, column_type, nullable=True))

    # Refresh after adding columns before creating indexes.
    inspector = sa.inspect(bind)
    existing_indexes = _index_names(inspector, "subscriptions")
    index_specs = (
        ("ix_subscriptions_payment_provider", ["payment_provider"], False),
        ("ix_subscriptions_dodo_checkout_session_id", ["dodo_checkout_session_id"], False),
        ("ix_subscriptions_dodo_customer_id", ["dodo_customer_id"], False),
        ("ix_subscriptions_dodo_subscription_id", ["dodo_subscription_id"], True),
        ("ix_subscriptions_dodo_payment_id", ["dodo_payment_id"], False),
    )
    with op.batch_alter_table("subscriptions") as batch:
        for name, columns, unique in index_specs:
            if name not in existing_indexes:
                batch.create_index(name, columns, unique=unique)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("subscriptions"):
        return

    existing_indexes = _index_names(inspector, "subscriptions")
    with op.batch_alter_table("subscriptions") as batch:
        for name in (
            "ix_subscriptions_dodo_payment_id",
            "ix_subscriptions_dodo_subscription_id",
            "ix_subscriptions_dodo_customer_id",
            "ix_subscriptions_dodo_checkout_session_id",
            "ix_subscriptions_payment_provider",
        ):
            if name in existing_indexes:
                batch.drop_index(name)

    existing_columns = _column_names(sa.inspect(bind), "subscriptions")
    with op.batch_alter_table("subscriptions") as batch:
        for name in (
            "provider_currency",
            "dodo_payment_id",
            "dodo_subscription_id",
            "dodo_customer_id",
            "dodo_checkout_session_id",
            "payment_provider",
        ):
            if name in existing_columns:
                batch.drop_column(name)
