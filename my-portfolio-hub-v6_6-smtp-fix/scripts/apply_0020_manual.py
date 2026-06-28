"""
scripts/apply_0020_manual.py
============================
MANUAL SCHEMA PATCH — Run this if `flask db upgrade` skips migration 0020.

Usage:
    python scripts/apply_0020_manual.py

What it does:
  1. Adds reminder_sent_7d and reminder_sent_30d columns to `subscriptions`
  2. Creates the `subscription_notifications` table
  3. Stamps Alembic's alembic_version to 0020_renewal_notifications
     so future `flask db upgrade` calls don't re-run it.

Safe to run multiple times — all operations are guarded by existence checks.
"""

import sys
import os

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app

with app.app_context():
    from app import db
    from sqlalchemy import text, inspect

    conn   = db.engine.connect()
    insp   = inspect(db.engine)
    is_pg  = db.engine.dialect.name == 'postgresql'

    print('[0020] Checking subscriptions columns...')

    existing_cols = [c['name'] for c in insp.get_columns('subscriptions')]

    with conn.begin():
        if 'reminder_sent_7d' not in existing_cols:
            conn.execute(text(
                'ALTER TABLE subscriptions ADD COLUMN reminder_sent_7d BOOLEAN NOT NULL DEFAULT 0'
            ))
            print('[0020] Added: subscriptions.reminder_sent_7d')
        else:
            print('[0020] Skip:  subscriptions.reminder_sent_7d already exists')

        if 'reminder_sent_30d' not in existing_cols:
            conn.execute(text(
                'ALTER TABLE subscriptions ADD COLUMN reminder_sent_30d BOOLEAN NOT NULL DEFAULT 0'
            ))
            print('[0020] Added: subscriptions.reminder_sent_30d')
        else:
            print('[0020] Skip:  subscriptions.reminder_sent_30d already exists')

    print('[0020] Checking subscription_notifications table...')

    if not insp.has_table('subscription_notifications'):
        with conn.begin():
            conn.execute(text("""
                CREATE TABLE subscription_notifications (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id           INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                    subscription_id     INTEGER REFERENCES subscriptions(id) ON DELETE SET NULL,
                    notification_type   VARCHAR(50)  NOT NULL,
                    title               VARCHAR(200) NOT NULL,
                    message             TEXT         NOT NULL,
                    is_read             BOOLEAN      NOT NULL DEFAULT 0,
                    sent_via_email      BOOLEAN      NOT NULL DEFAULT 0,
                    sent_via_dashboard  BOOLEAN      NOT NULL DEFAULT 1,
                    created_at          DATETIME     DEFAULT (datetime('now')),
                    read_at             DATETIME
                )
            """))
            conn.execute(text(
                'CREATE INDEX ix_sub_notif_tenant_read ON subscription_notifications (tenant_id, is_read)'
            ))
            conn.execute(text(
                'CREATE INDEX ix_sub_notif_type ON subscription_notifications (notification_type)'
            ))
        print('[0020] Created: subscription_notifications table + indexes')
    else:
        print('[0020] Skip:   subscription_notifications already exists')

    # Stamp Alembic version so flask db upgrade sees this as applied
    print('[0020] Stamping alembic_version...')
    with conn.begin():
        existing = conn.execute(text("SELECT version_num FROM alembic_version")).fetchall()
        if existing:
            conn.execute(text(
                "UPDATE alembic_version SET version_num = '0020_renewal_notifications'"
            ))
        else:
            conn.execute(text(
                "INSERT INTO alembic_version (version_num) VALUES ('0020_renewal_notifications')"
            ))
    print('[0020] alembic_version = 0020_renewal_notifications')

    conn.close()
    print('[0020] Done. You can now run: flask run-renewal-check')
