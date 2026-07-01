"""
scripts/add_superadmin_column.py
──────────────────────────
One-shot migration script to add the `is_superadmin` column to the users table.
Run this if your database already exists and the column is missing:

    python scripts/add_superadmin_column.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app
from app import db

with app.app_context():
    with db.engine.connect() as conn:
        if 'postgresql' in str(db.engine.url):
            result = conn.execute(db.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='users'"
            ))
            existing = {row[0] for row in result}
        else:
            result = conn.execute(db.text("PRAGMA table_info(users)"))
            existing = {row[1] for row in result}

        if 'is_superadmin' not in existing:
            conn.execute(db.text(
                "ALTER TABLE users ADD COLUMN is_superadmin BOOLEAN NOT NULL DEFAULT FALSE"
            ))
            conn.commit()
            print('  ✓ Added column: is_superadmin')
        else:
            print('  — Column already exists: is_superadmin')

    print('\nDone. User model now supports is_superadmin.')
