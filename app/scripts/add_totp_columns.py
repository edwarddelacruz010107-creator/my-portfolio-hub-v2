"""
scripts/add_totp_columns.py
──────────────────────────
One-shot migration script to add 2FA columns to the users table.
Run this if you prefer not to use Alembic:

    python scripts/add_totp_columns.py

This is idempotent — it checks for existing columns first.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app
from app import db

COLUMNS = [
    ("totp_secret",       "VARCHAR(64)"),
    ("totp_enabled",      "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("totp_backup_codes", "TEXT"),
]

with app.app_context():
    with db.engine.connect() as conn:
        # Check existing columns
        if 'postgresql' in str(db.engine.url):
            result = conn.execute(db.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='users'"
            ))
            existing = {row[0] for row in result}
        else:
            result = conn.execute(db.text("PRAGMA table_info(users)"))
            existing = {row[1] for row in result}

        for col_name, col_type in COLUMNS:
            if col_name not in existing:
                conn.execute(db.text(
                    f"ALTER TABLE users ADD COLUMN {col_name} {col_type}"
                ))
                conn.commit()
                print(f"  ✓ Added column: {col_name}")
            else:
                print(f"  — Column already exists: {col_name}")

    print("\nDone. Users table is 2FA-ready.")
