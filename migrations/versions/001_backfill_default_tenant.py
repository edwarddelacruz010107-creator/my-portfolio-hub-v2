"""backfill_default_tenant

Revision ID: 0010_backfill_default_tenant
Revises: 0009_tenant_url_refactor
Create Date: 2026-06-19

Alembic migration to:
  1. Ensure 'default' tenant_slug is set on existing Profile rows that lack it
  2. Backfill tenant_slug='default' on orphaned Skills/Projects/Testimonials/Inquiries
  3. Ensure admin User row has tenant_slug='default'

Run:
  flask db upgrade
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text, inspect

revision = '0010_backfill_default_tenant'
down_revision = '0009_tenant_url_refactor'
branch_labels = None
depends_on = None


def _table_exists(inspector, table_name):
    """Check if a table exists."""
    return inspector.has_table(table_name)


def _column_exists(inspector, table_name, column_name):
    """Check if a column exists in a table."""
    if not _table_exists(inspector, table_name):
        return False
    columns = [col['name'] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade():
    conn = op.get_bind()
    inspector = inspect(conn)

    # Ensure profile table has tenant_slug column (idempotent)
    if _table_exists(inspector, 'profile'):
        if not _column_exists(inspector, 'profile', 'tenant_slug'):
            try:
                op.add_column('profile', sa.Column(
                    'tenant_slug', sa.String(120),
                    nullable=True, server_default='default'
                ))
            except Exception as e:
                print(f"Warning: Could not add profile.tenant_slug: {e}")

        # Backfill NULL tenant_slug on profile
        try:
            conn.execute(text(
                "UPDATE profile SET tenant_slug = 'default' WHERE tenant_slug IS NULL OR tenant_slug = ''"
            ))
        except Exception as e:
            print(f"Warning: Could not backfill profile.tenant_slug: {e}")

        # Ensure at least one profile row with tenant_slug='default'
        try:
            result = conn.execute(text(
                "SELECT COUNT(*) FROM profile WHERE tenant_slug = 'default'"
            ))
            count = result.scalar()
            if count == 0:
                conn.execute(text("""
                    INSERT INTO profile (
                        name, title, subtitle, bio, bio_short, location, email,
                        phone, profile_image, resume_url, years_experience,
                        clients_count, hero_tagline, availability_status, is_available,
                        social_links, tenant_slug, plan, monthly_rate, internal_notes,
                        meta_title, meta_description, og_image, updated_at
                    ) VALUES (
                        'Portfolio Owner', 'Full Stack Developer',
                        'Building beautiful digital experiences',
                        'Welcome to my portfolio.', '', 'Remote', 'hello@example.com',
                        '', '', '', 5, 0, 'Crafting elegant web experiences.',
                        'Available for new work', true,
                        '{}', 'default', 'Basic', 0.0, '',
                        '', '', '', NOW()
                    )
                """))
                print("Inserted default tenant profile row")
        except Exception as e:
            print(f"Warning: Could not insert default profile: {e}")

    # Backfill tenant_slug on related tables
    for table in ['skills', 'projects', 'testimonials', 'inquiries', 'activity_log']:
        if _table_exists(inspector, table) and _column_exists(inspector, table, 'tenant_slug'):
            try:
                conn.execute(text(
                    f"UPDATE {table} SET tenant_slug = 'default' "
                    f"WHERE tenant_slug IS NULL OR tenant_slug = ''"
                ))
            except Exception as e:
                print(f"Warning: Skipped {table}: {e}")

    # Backfill admin user tenant_slug
    if _table_exists(inspector, 'users') and _column_exists(inspector, 'users', 'tenant_slug'):
        try:
            conn.execute(text(
                "UPDATE users SET tenant_slug = 'default' "
                "WHERE (tenant_slug IS NULL OR tenant_slug = '') "
                "AND is_superadmin = false"
            ))
        except Exception as e:
            print(f"Warning: Skipped users backfill: {e}")

    print("Migration 0010_backfill_default_tenant: complete")


def downgrade():
    # Intentionally a no-op: data backfill is non-destructive
    pass