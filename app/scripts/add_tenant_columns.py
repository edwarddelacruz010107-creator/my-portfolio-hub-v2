"""scripts/add_tenant_columns.py
──────────────────────────
Add tenant slug columns to existing User and Profile schemas.

Usage:
  python scripts/add_tenant_columns.py
"""
import os
import sys

# BUG FIX (audit 2026-07-02): script lives at app/scripts/, 2 levels
# under project root -- 2x dirname() landed on app/, not project root,
# breaking any 'from run import app' / 'from app import ...' import.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from run import app
from app import db
from app.models import User
from app.models.portfolio import (
    Profile, Skill, Project, Testimonial, Inquiry, ActivityLog,
)
from sqlalchemy import inspect


def main():
    with app.app_context():
        inspector = inspect(db.engine)

        def _add_column_raw(table_name: str, column_name: str, column_type_sql: str = 'VARCHAR(120)'):
            """Add a column using raw ALTER TABLE — safer across SQLite/Postgres dev setups."""
            if column_name in {c['name'] for c in inspector.get_columns(table_name)}:
                return False
            sql = f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type_sql}"
            try:
                db.session.execute(db.text(sql))
                db.session.commit()
                return True
            except Exception:
                db.session.rollback()
                raise

        # Add tenant_slug to profile table if missing
        profile_cols = {c['name'] for c in inspector.get_columns('profile')}
        if 'tenant_slug' not in profile_cols:
            print('Adding tenant_slug to profile table...')
            _add_column_raw('profile', 'tenant_slug', 'VARCHAR(120)')
            db.session.execute(db.text("UPDATE profile SET tenant_slug = 'default' WHERE tenant_slug IS NULL"))
            db.session.commit()
            profile_cols.add('tenant_slug')
        else:
            print('profile.tenant_slug already exists.')

        # Add missing profile columns if needed
        if 'plan' not in profile_cols:
            print('Adding plan to profile table...')
            _add_column_raw('profile', 'plan', 'VARCHAR(50)')
            db.session.commit()
            profile_cols.add('plan')
        else:
            print('profile.plan already exists.')

        if 'monthly_rate' not in profile_cols:
            print('Adding monthly_rate to profile table...')
            _add_column_raw('profile', 'monthly_rate', 'FLOAT')
            db.session.commit()
            profile_cols.add('monthly_rate')
        else:
            print('profile.monthly_rate already exists.')

        if 'internal_notes' not in profile_cols:
            print('Adding internal_notes to profile table...')
            _add_column_raw('profile', 'internal_notes', 'TEXT')
            db.session.commit()
            profile_cols.add('internal_notes')
        else:
            print('profile.internal_notes already exists.')

        # Add tenant_slug to users table if missing
        user_cols = {c['name'] for c in inspector.get_columns('users')}
        if 'tenant_slug' not in user_cols:
            print('Adding tenant_slug to users table...')
            _add_column_raw('users', 'tenant_slug', 'VARCHAR(120)')
            db.session.execute(db.text("UPDATE users SET tenant_slug = 'default' WHERE tenant_slug IS NULL"))
            db.session.commit()
        else:
            print('users.tenant_slug already exists.')

        # Add tenant_slug to skills table if missing
        skill_cols = {c['name'] for c in inspector.get_columns('skills')}
        if 'tenant_slug' not in skill_cols:
            print('Adding tenant_slug to skills table...')
            _add_column_raw('skills', 'tenant_slug', 'VARCHAR(120)')
            db.session.execute(db.text("UPDATE skills SET tenant_slug = 'default' WHERE tenant_slug IS NULL"))
            db.session.commit()
        else:
            print('skills.tenant_slug already exists.')

        # Add tenant_slug to projects table if missing
        project_cols = {c['name'] for c in inspector.get_columns('projects')}
        if 'tenant_slug' not in project_cols:
            print('Adding tenant_slug to projects table...')
            _add_column_raw('projects', 'tenant_slug', 'VARCHAR(120)')
            db.session.execute(db.text("UPDATE projects SET tenant_slug = 'default' WHERE tenant_slug IS NULL"))
            db.session.commit()
        else:
            print('projects.tenant_slug already exists.')

        # Add tenant_slug to testimonials table if missing
        testimonial_cols = {c['name'] for c in inspector.get_columns('testimonials')}
        if 'tenant_slug' not in testimonial_cols:
            print('Adding tenant_slug to testimonials table...')
            _add_column_raw('testimonials', 'tenant_slug', 'VARCHAR(120)')
            db.session.execute(db.text("UPDATE testimonials SET tenant_slug = 'default' WHERE tenant_slug IS NULL"))
            db.session.commit()
        else:
            print('testimonials.tenant_slug already exists.')

        # Add tenant_slug to inquiries table if missing
        inquiry_cols = {c['name'] for c in inspector.get_columns('inquiries')}
        if 'tenant_slug' not in inquiry_cols:
            print('Adding tenant_slug to inquiries table...')
            _add_column_raw('inquiries', 'tenant_slug', 'VARCHAR(120)')
            db.session.execute(db.text("UPDATE inquiries SET tenant_slug = 'default' WHERE tenant_slug IS NULL"))
            db.session.commit()
        else:
            print('inquiries.tenant_slug already exists.')

        # Add tenant_slug to activity_log table if missing
        activity_cols = {c['name'] for c in inspector.get_columns('activity_log')}
        if 'tenant_slug' not in activity_cols:
            print('Adding tenant_slug to activity_log table...')
            _add_column_raw('activity_log', 'tenant_slug', 'VARCHAR(120)')
            db.session.execute(db.text("UPDATE activity_log SET tenant_slug = 'default' WHERE tenant_slug IS NULL"))
            db.session.commit()
        else:
            print('activity_log.tenant_slug already exists.')

        print('Tenant column setup completed.')


if __name__ == '__main__':
    main()
