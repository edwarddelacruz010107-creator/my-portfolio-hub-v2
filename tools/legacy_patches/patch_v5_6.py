#!/usr/bin/env python3
"""
patch_v5_6_db.py — Portfolio CMS v5.6 Database Migration
=========================================================
Adds six per-portal MailerSend columns to global_email_config
on the CORE PostgreSQL database.

Usage
-----
Run from the project root (where .env lives):

    python patch_v5_6_db.py

Or with an explicit connection string:

    python patch_v5_6_db.py --url "postgresql://user:pass@host/dbname"

Requires: psycopg2-binary  (pip install psycopg2-binary)
Safe to run multiple times — uses ADD COLUMN IF NOT EXISTS.
"""

import argparse
import os
import sys
import textwrap

# ── Column definitions ────────────────────────────────────────────────────────
NEW_COLUMNS = [
    ("admin_mailersend_api_key",      "TEXT",         "''"),
    ("admin_sender_name",             "VARCHAR(200)",  "''"),
    ("admin_sender_email",            "VARCHAR(200)",  "''"),
    ("superadmin_mailersend_api_key", "TEXT",         "''"),
    ("superadmin_sender_name",        "VARCHAR(200)",  "''"),
    ("superadmin_sender_email",       "VARCHAR(200)",  "''"),
]

TABLE = "global_email_config"

# ── Helpers ───────────────────────────────────────────────────────────────────

def load_env(path=".env"):
    """Minimal .env parser — does not require python-dotenv."""
    env = {}
    if not os.path.isfile(path):
        return env
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            val = val.strip().strip('"').strip("'")
            env[key.strip()] = val
    return env


def resolve_dsn(cli_url: str | None) -> str:
    """
    Resolution order:
      1. --url CLI argument
      2. DIRECT_CORE_DATABASE_URL  (preferred — bypasses PgBouncer)
      3. CORE_DATABASE_URL
      4. DATABASE_URL
    """
    if cli_url:
        return cli_url

    env = load_env()

    for key in ("DIRECT_CORE_DATABASE_URL", "CORE_DATABASE_URL", "DATABASE_URL"):
        val = env.get(key) or os.environ.get(key, "")
        if val and val.startswith("postgresql"):
            return val

    return ""


def column_exists(cur, table: str, column: str) -> bool:
    cur.execute(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_name = %s AND column_name = %s
        """,
        (table, column),
    )
    return cur.fetchone() is not None


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Portfolio CMS v5.6 — apply per-portal MailerSend DB migration",
    )
    parser.add_argument(
        "--url", metavar="DSN",
        help="PostgreSQL connection URL (overrides .env)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print SQL statements without executing them",
    )
    args = parser.parse_args()

    dsn = resolve_dsn(args.url)
    if not dsn:
        print(
            "ERROR: No PostgreSQL DSN found.\n"
            "  Set CORE_DATABASE_URL in .env or pass --url <dsn>",
            file=sys.stderr,
        )
        sys.exit(1)

    # Mask password in display
    try:
        from urllib.parse import urlparse, urlunparse
        parsed = urlparse(dsn)
        safe = urlunparse(parsed._replace(netloc=parsed.netloc.replace(
            f":{parsed.password}@", ":****@"
        )))
    except Exception:
        safe = dsn[:40] + "…"

    print(f"\nPortfolio CMS v5.6 — DB Migration")
    print(f"Target : {safe}")
    print(f"Table  : {TABLE}")
    print(f"Mode   : {'DRY RUN' if args.dry_run else 'LIVE'}")
    print("-" * 60)

    try:
        import psycopg2
    except ImportError:
        print(
            "ERROR: psycopg2 not installed.\n"
            "  pip install psycopg2-binary",
            file=sys.stderr,
        )
        sys.exit(1)

    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    cur = conn.cursor()

    applied = []
    skipped = []

    for col_name, col_type, col_default in NEW_COLUMNS:
        if column_exists(cur, TABLE, col_name):
            print(f"  SKIP   {col_name} (already exists)")
            skipped.append(col_name)
            continue

        sql = (
            f"ALTER TABLE {TABLE} "
            f"ADD COLUMN {col_name} {col_type} DEFAULT {col_default};"
        )

        if args.dry_run:
            print(f"  DRY    {sql}")
        else:
            cur.execute(sql)
            print(f"  ADD    {col_name}  ({col_type})")
            applied.append(col_name)

    if not args.dry_run:
        conn.commit()
        print("-" * 60)
        print(f"  Done — {len(applied)} column(s) added, {len(skipped)} skipped.")
    else:
        print("-" * 60)
        print("  Dry-run complete — no changes written.")

    cur.close()
    conn.close()

    # Verify
    if not args.dry_run and applied:
        print("\nVerifying columns…")
        conn2 = psycopg2.connect(dsn)
        cur2  = conn2.cursor()
        all_ok = True
        for col_name, _, _ in NEW_COLUMNS:
            exists = column_exists(cur2, TABLE, col_name)
            status = "✓" if exists else "✗ MISSING"
            print(f"  {status}  {col_name}")
            if not exists:
                all_ok = False
        cur2.close()
        conn2.close()
        if not all_ok:
            print("\nWARNING: Some columns are missing — check DB permissions.", file=sys.stderr)
            sys.exit(1)
        print("\nAll columns verified. Safe to deploy v5.6.")


if __name__ == "__main__":
    main()