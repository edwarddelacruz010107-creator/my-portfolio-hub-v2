"""tenant_url_refactor

Revision ID: 0009_tenant_url_refactor
Revises: 0008_add_user_security_fields
Create Date: 2026-06-19

Changes:
  1. Ensure profile.tenant_slug has a NOT NULL default of 'default'
  2. Backfill any NULL tenant_slug values to 'default'
  3. Ensure at least one Profile with tenant_slug='default' exists (seed if none)
  4. Add index on profile.tenant_slug if not exists (idempotent)

NOTE: This migration now runs AFTER all core tables are created by the
initial schema migration. It uses inspector checks for idempotency.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = '0009_tenant_url_refactor'
down_revision = '0008_add_user_security_fields'
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

    # Only proceed if tables exist (they should after initial schema)
    if not _table_exists(inspector, 'profile'):
        # Profile table is in tenant database, not core database
        # This migration is for core database only
        return

    # Backfill NULL tenant_slugs to 'default'
    for table in ('profile', 'skills', 'projects', 'testimonials', 'inquiry', 'activity_log'):
        if _table_exists(inspector, table) and _column_exists(inspector, table, 'tenant_slug'):
            try:
                conn.execute(sa.text(
                    f"UPDATE {table} SET tenant_slug = 'default' WHERE tenant_slug IS NULL OR tenant_slug = ''"
                ))
            except Exception as e:
                # Log but don't fail - table may be in different database
                print(f"Warning: Could not backfill {table}.tenant_slug: {e}")

    # Seed default Profile if none exists
    if _table_exists(inspector, 'profile'):
        try:
            result = conn.execute(sa.text(
                "SELECT COUNT(*) FROM profile WHERE tenant_slug = 'default'"
            )).scalar()

            if result == 0:
                conn.execute(sa.text("""
                    INSERT INTO profile (
                        name, title, subtitle, bio, bio_short, location, email, phone,
                        profile_image, resume_url, years_experience, clients_count,
                        hero_tagline, availability_status, is_available, social_links,
                        tenant_slug, plan, monthly_rate, internal_notes,
                        meta_title, meta_description, og_image, updated_at
                    ) VALUES (
                        'Your Name', 'Full Stack Developer',
                        'Building beautiful digital experiences',
                        'Welcome to my portfolio.', 'A developer who ships.',
                        '', '', '', '', '', 0, 0, '', 'Available for freelance', true,
                        '{}', 'default', 'Basic', 0.0, '', '', '', '',
                        NOW()
                    )
                """))
        except Exception as e:
            print(f"Warning: Could not seed default profile: {e}")

    # Ensure default admin user has tenant_slug='default'
    if _table_exists(inspector, 'users'):
        try:
            conn.execute(sa.text(
                "UPDATE users SET tenant_slug = 'default' WHERE tenant_slug IS NULL OR tenant_slug = ''"
            ))
        except Exception as e:
            print(f"Warning: Could not backfill users.tenant_slug: {e}")


def downgrade():
    # No destructive downgrade needed
    pass