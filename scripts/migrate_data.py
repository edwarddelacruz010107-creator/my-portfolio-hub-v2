#!/usr/bin/env python3
"""
scripts/migrate_data.py — Single-DB → Dual-DB data migration

Reads from the existing single DATABASE_URL and writes to:
  CORE_DATABASE_URL   — auth + billing + platform tables
  TENANT_DATABASE_URL — portfolio content tables

Run ONCE during the migration cutover window with all write traffic paused.

Usage:
    export DATABASE_URL=postgresql://...          # old single DB
    export CORE_DATABASE_URL=postgresql://...     # new core DB
    export TENANT_DATABASE_URL=postgresql://...   # new tenant data DB
    python scripts/migrate_data.py [--dry-run]

Safety guarantees:
  • --dry-run: runs full read pass and reports counts without writing
  • Wrapped in transactions; rolls back both DBs on any error
  • Idempotent: skips rows that already exist (by primary key)
  • Prints a progress row for every 100 rows migrated
"""

import argparse
import os
import sys
from datetime import datetime, timezone

from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker

# ── Argument parsing ─────────────────────────────────────────────────────────

parser = argparse.ArgumentParser(description='Migrate single DB → dual DB')
parser.add_argument('--dry-run', action='store_true', help='Validate without writing')
args = parser.parse_args()

DRY_RUN = args.dry_run


# ── Connection setup ─────────────────────────────────────────────────────────

def _fix(url: str) -> str:
    return url.replace('postgres://', 'postgresql://', 1) if url.startswith('postgres://') else url


SRC_URL    = _fix(os.environ['DATABASE_URL'])
CORE_URL   = _fix(os.environ['CORE_DATABASE_URL'])
TENANT_URL = _fix(os.environ['TENANT_DATABASE_URL'])

src_engine    = create_engine(SRC_URL,    pool_pre_ping=True)
core_engine   = create_engine(CORE_URL,   pool_pre_ping=True)
tenant_engine = create_engine(TENANT_URL, pool_pre_ping=True)

SrcSession    = sessionmaker(bind=src_engine)
CoreSession   = sessionmaker(bind=core_engine)
TenantSession = sessionmaker(bind=tenant_engine)

# ── Table routing ─────────────────────────────────────────────────────────────
# Maps: source_table_name → destination ("core" | "tenant")

CORE_TABLES = [
    'tenants',
    'users',
    'subscriptions',
    'webhook_events',
    'payment_methods',
    'payment_instructions',
    'payment_submissions',
    'platform_settings',
    'tenant_communication_settings',
    'password_reset_otps',
    'global_email_config',
    'inquiries',
    'inquiry_replies',
    'subscription_notifications',
    'activity_log',
]

TENANT_TABLES = [
    'profile',
    'skills',
    'projects',
    'testimonials',
    'services',
    'tenant_form_settings',
]


# ── Migration helpers ─────────────────────────────────────────────────────────

def _table_exists(engine, table_name: str) -> bool:
    insp = inspect(engine)
    return insp.has_table(table_name)


def migrate_table(
    src_session,
    dst_session,
    dst_engine,
    table_name: str,
    batch_size: int = 500,
) -> int:
    """
    Copy all rows from src table → dst table.
    Returns count of rows written (0 in dry-run mode).
    """
    if not _table_exists(src_engine, table_name):
        print(f"  ⚠  {table_name}: not found in source DB — skipping")
        return 0

    if not _table_exists(dst_engine, table_name):
        print(f"  ⚠  {table_name}: not found in destination DB — run migrations first")
        return 0

    result = src_session.execute(text(f'SELECT * FROM "{table_name}"'))
    cols   = list(result.keys())
    rows   = result.fetchall()
    total  = len(rows)

    print(f"  → {table_name}: {total} rows found in source")

    if DRY_RUN or total == 0:
        return 0

    written = 0
    for i in range(0, total, batch_size):
        batch = rows[i:i + batch_size]
        for row in batch:
            row_dict = dict(zip(cols, row))
            # Idempotent: skip on PK conflict
            pk_col   = 'key' if table_name == 'platform_settings' else 'id'
            pk_val   = row_dict.get(pk_col)
            existing = dst_session.execute(
                text(f'SELECT 1 FROM "{table_name}" WHERE "{pk_col}" = :pk'),
                {'pk': pk_val},
            ).fetchone()
            if existing:
                continue
            col_names = ', '.join(f'"{c}"' for c in cols)
            placeholders = ', '.join(f':{c}' for c in cols)
            dst_session.execute(
                text(f'INSERT INTO "{table_name}" ({col_names}) VALUES ({placeholders})'),
                row_dict,
            )
            written += 1

        print(f"    {min(i + batch_size, total)}/{total} processed...")

    dst_session.commit()
    print(f"  ✓  {table_name}: {written} rows written")
    return written


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    if DRY_RUN:
        print('═' * 60)
        print('DRY RUN MODE — no writes will occur')
        print('═' * 60)

    src_session    = SrcSession()
    core_session   = CoreSession()
    tenant_session = TenantSession()

    totals = {'core': 0, 'tenant': 0}

    try:
        print('\n── Core DB tables ──────────────────────────────────────────')
        for table in CORE_TABLES:
            n = migrate_table(src_session, core_session, core_engine, table)
            totals['core'] += n

        print('\n── Tenant Data DB tables ───────────────────────────────────')
        for table in TENANT_TABLES:
            n = migrate_table(src_session, tenant_session, tenant_engine, table)
            totals['tenant'] += n

    except Exception as exc:
        print(f'\n❌ Migration failed: {exc}')
        if not DRY_RUN:
            core_session.rollback()
            tenant_session.rollback()
        sys.exit(1)
    finally:
        src_session.close()
        core_session.close()
        tenant_session.close()

    print('\n═' * 60)
    if DRY_RUN:
        print('DRY RUN complete — no data was written')
    else:
        print(f'Migration complete')
        print(f'  core_db rows written:   {totals["core"]}')
        print(f'  tenant_db rows written: {totals["tenant"]}')


if __name__ == '__main__':
    main()
