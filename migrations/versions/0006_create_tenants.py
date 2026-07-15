"""create_tenants

Revision ID: 0006_create_tenants
Revises: 0005_create_subscriptions
Create a dedicated Tenant model and add tenant_id foreign keys to tenant-scoped tables.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text
from datetime import datetime, timezone

revision = '0006_create_tenants'
down_revision = '0005_create_subscriptions'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    now = datetime.now(timezone.utc)
    inspector = sa.inspect(conn)

    # Deterministic dual-bind path.  Tenant-data tables may legitimately be
    # absent from the core database and users.tenant_id may already exist from
    # the base revision.  Guard every operation before issuing DDL so a caught
    # PostgreSQL error cannot leave the transaction aborted.
    if not inspector.has_table('tenants'):
        op.create_table(
            'tenants',
            sa.Column('id', sa.Integer, primary_key=True),
            sa.Column('slug', sa.String(length=120), nullable=False, unique=True),
            sa.Column('company_name', sa.String(length=200), nullable=False, server_default=''),
            sa.Column('email', sa.String(length=120), nullable=False, server_default=''),
            sa.Column('status', sa.String(length=50), nullable=False, server_default='active'),
            sa.Column('plan', sa.String(length=50), nullable=False, server_default='Basic'),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index('ix_tenants_slug', 'tenants', ['slug'], unique=True)
        inspector = sa.inspect(conn)

    for table in ('profile', 'users', 'projects', 'skills'):
        if not inspector.has_table(table):
            continue
        columns = {column['name'] for column in inspector.get_columns(table)}
        if 'tenant_id' not in columns:
            op.add_column(table, sa.Column('tenant_id', sa.Integer(), nullable=True))
        indexes = {index['name'] for index in sa.inspect(conn).get_indexes(table)}
        index_name = f'ix_{table}_tenant_id'
        if index_name not in indexes:
            op.create_index(index_name, table, ['tenant_id'])

    inspector = sa.inspect(conn)
    if inspector.has_table('profile'):
        conn.execute(text("UPDATE profile SET tenant_id = id WHERE tenant_id IS NULL"))

    for table in ('users', 'projects', 'skills'):
        if not inspector.has_table(table):
            continue
        columns = {column['name'] for column in inspector.get_columns(table)}
        if {'tenant_id', 'tenant_slug'} <= columns:
            conn.execute(text(f"""
                UPDATE {table}
                SET tenant_id = (
                    SELECT tenants.id FROM tenants
                    WHERE tenants.slug = {table}.tenant_slug
                    LIMIT 1
                )
                WHERE tenant_id IS NULL
            """))

    # The base revision already points subscriptions.tenant_id at tenants.id.
    # Legacy databases without that FK are upgraded through a batch operation.
    if inspector.has_table('subscriptions'):
        subscription_fks = inspector.get_foreign_keys('subscriptions')
        if not any(
            fk.get('constrained_columns') == ['tenant_id']
            and fk.get('referred_table') == 'tenants'
            for fk in subscription_fks
        ):
            with op.batch_alter_table('subscriptions') as batch:
                batch.create_foreign_key(
                    'fk_subscriptions_tenant_id', 'tenants', ['tenant_id'], ['id']
                )

    return

    # FIXED: 'tenants' is now created by 0001_initial_schema.py. The
    # unconditional create_table() here caused
    # psycopg2.errors.InFailedSqlTransaction (relation "tenants" already
    # exists) on every fresh install. Guarded with has_table() so this
    # remains a safe no-op on both fresh DBs (table from 0001) and
    # legacy DBs that predate 0001 (table didn't exist yet here).
    if not inspector.has_table('tenants'):
        op.create_table(
            'tenants',
            sa.Column('id', sa.Integer, primary_key=True),
            sa.Column('slug', sa.String(length=120), nullable=False, unique=True, index=True),
            sa.Column('company_name', sa.String(length=200), nullable=False, server_default=''),
            sa.Column('email', sa.String(length=120), nullable=False, server_default=''),
            sa.Column('status', sa.String(length=50), nullable=False, server_default='active'),
            sa.Column('plan', sa.String(length=50), nullable=False, server_default='Basic'),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        )

    # Populate tenants from existing profiles using preserved profile IDs.
    try:
        conn.execute(text(
            """
            INSERT INTO tenants (id, slug, company_name, email, status, plan, created_at, updated_at)
            SELECT
                id,
                tenant_slug,
                COALESCE(name, tenant_slug),
                COALESCE(email, ''),
                CASE WHEN is_available IS FALSE THEN 'suspended' ELSE 'active' END,
                COALESCE(plan, 'Basic'),
                :now,
                COALESCE(updated_at, :now)
            FROM profile
            """), {'now': now})
    except Exception:
        pass

    # Add tenant_id columns for tenant-scoped tables.
    op.add_column('profile', sa.Column('tenant_id', sa.Integer(), nullable=True, index=True))
    op.add_column('users', sa.Column('tenant_id', sa.Integer(), nullable=True, index=True))
    op.add_column('projects', sa.Column('tenant_id', sa.Integer(), nullable=True, index=True))
    op.add_column('skills', sa.Column('tenant_id', sa.Integer(), nullable=True, index=True))

    # Backfill profile tenant_id as identity mapping from profile.id.
    try:
        conn.execute(text("UPDATE profile SET tenant_id = id"))
    except Exception:
        pass

    # Backfill other tenant_id relationships using tenant slug.
    for table in ('users', 'projects', 'skills'):
        try:
            conn.execute(text(f"""
                UPDATE {table}
                SET tenant_id = (
                    SELECT tenants.id FROM tenants
                    WHERE tenants.slug = {table}.tenant_slug
                    LIMIT 1
                )
                WHERE tenant_id IS NULL
            """))
        except Exception:
            pass

    # Ensure profile.tenant_id is populated before adding constraints.
    try:
        op.create_foreign_key('fk_profile_tenant_id', 'profile', 'tenants', ['tenant_id'], ['id'])
        op.create_unique_constraint('uq_profile_tenant_id', 'profile', ['tenant_id'])
    except Exception:
        pass

    for table in ('users', 'projects', 'skills'):
        try:
            op.create_foreign_key(f'fk_{table}_tenant_id', table, 'tenants', ['tenant_id'], ['id'])
        except Exception:
            pass

    # Re-target subscriptions to Tenant instead of Profile. Tenant IDs mirror old Profile IDs.
    try:
        with op.batch_alter_table('subscriptions') as batch_op:
            try:
                batch_op.drop_constraint('subscriptions_tenant_id_fkey', type_='foreignkey')
            except Exception:
                pass
            batch_op.create_foreign_key('fk_subscriptions_tenant_id', 'tenants', ['tenant_id'], ['id'])
    except Exception:
        pass

    # Make profile.tenant_id required.
    try:
        with op.batch_alter_table('profile') as batch_op:
            batch_op.alter_column('tenant_id', nullable=False)
    except Exception:
        pass


def downgrade():
    # Remove Tenant relationships and schema changes.
    try:
        with op.batch_alter_table('subscriptions') as batch_op:
            try:
                batch_op.drop_constraint('fk_subscriptions_tenant_id', type_='foreignkey')
            except Exception:
                pass
            batch_op.create_foreign_key('subscriptions_tenant_id_fkey', 'profile', ['tenant_id'], ['id'])
    except Exception:
        pass

    for table in ('users', 'projects', 'skills'):
        try:
            op.drop_constraint(f'fk_{table}_tenant_id', table, type_='foreignkey')
        except Exception:
            pass

    try:
        op.drop_constraint('fk_profile_tenant_id', 'profile', type_='foreignkey')
    except Exception:
        pass
    try:
        op.drop_constraint('uq_profile_tenant_id', 'profile', type_='unique')
    except Exception:
        pass

    for table in ('profile', 'users', 'projects', 'skills'):
        try:
            op.drop_column(table, 'tenant_id')
        except Exception:
            pass

    try:
        op.drop_table('tenants')
    except Exception:
        pass
