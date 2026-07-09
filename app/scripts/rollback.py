#!/usr/bin/env python3
"""
scripts/rollback.py — Emergency rollback: dual-DB → single-DB

Copies all data from CORE_DATABASE_URL + TENANT_DATABASE_URL back into
DATABASE_URL (original single-DB). Run only if the dual-DB deployment needs
to be reversed.

Usage:
    export DATABASE_URL=postgresql://...          # original single DB
    export CORE_DATABASE_URL=postgresql://...
    export TENANT_DATABASE_URL=postgresql://...
    python scripts/rollback.py [--dry-run]

WARNING: This OVERWRITES data in DATABASE_URL for all migrated tables.
Ensure DATABASE_URL is a pre-migration backup, not a live prod DB.
"""

import argparse
import os
import sys

from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker

parser = argparse.ArgumentParser()
parser.add_argument('--dry-run', action='store_true')
args = parser.parse_args()
DRY_RUN = args.dry_run


def _fix(url: str) -> str:
    return url.replace('postgres://', 'postgresql://', 1) if url.startswith('postgres://') else url


DST_URL    = _fix(os.environ['DATABASE_URL'])
CORE_URL   = _fix(os.environ['CORE_DATABASE_URL'])
TENANT_URL = _fix(os.environ['TENANT_DATABASE_URL'])

dst_engine    = create_engine(DST_URL,    pool_pre_ping=True)
core_engine   = create_engine(CORE_URL,   pool_pre_ping=True)
tenant_engine = create_engine(TENANT_URL, pool_pre_ping=True)

CORE_TABLES   = ['tenants','users','subscriptions','webhook_events','payment_methods',
                 'payment_instructions','payment_submissions','platform_settings',
                 'tenant_communication_settings','password_reset_otps','global_email_config',
                 'inquiries','inquiry_replies','subscription_notifications','activity_log']

TENANT_TABLES = ['profile','skills','projects','testimonials','services','tenant_form_settings']


def copy_table(src_engine, dst_engine, table_name):
    insp = inspect(src_engine)
    if not insp.has_table(table_name):
        print(f'  ⚠  {table_name}: not in source — skipping')
        return 0

    with src_engine.connect() as src_conn:
        rows = src_conn.execute(text(f'SELECT * FROM "{table_name}"')).fetchall()
        cols = src_conn.execute(text(f'SELECT * FROM "{table_name}" LIMIT 0')).keys()
        cols = list(cols)

    print(f'  → {table_name}: {len(rows)} rows')
    if DRY_RUN or not rows:
        return 0

    with dst_engine.begin() as dst_conn:
        col_names    = ', '.join(f'"{c}"' for c in cols)
        placeholders = ', '.join(f':{c}' for c in cols)
        for row in rows:
            row_dict = dict(zip(cols, row))
            dst_conn.execute(
                text(f'INSERT INTO "{table_name}" ({col_names}) VALUES ({placeholders}) ON CONFLICT DO NOTHING'),
                row_dict,
            )

    print(f'  ✓  {table_name}: written')
    return len(rows)


def main():
    if DRY_RUN:
        print('DRY RUN — no writes')

    print('\n── Restoring from core_db ──────────────────────────────────')
    for t in CORE_TABLES:
        copy_table(core_engine, dst_engine, t)

    print('\n── Restoring from tenant_data_db ───────────────────────────')
    for t in TENANT_TABLES:
        copy_table(tenant_engine, dst_engine, t)

    print('\nRollback complete.' if not DRY_RUN else 'Dry run complete.')


if __name__ == '__main__':
    main()
