"""
tools/reconcile_dev_db.py
─────────────────────────────────────────────────────────────────────────────
ONE-TIME REPAIR for local dev SQLite databases that were built by
db.create_all() over time (which only ever ADDS missing tables, never adds
missing columns to tables that already exist) and were never stamped with
an Alembic revision.

Symptom this fixes:
  - "no such column: payment_submissions.expected_amount" (or any other
    "no such column" error) at runtime, even though `flask db upgrade` says
    everything is current.
  - `flask db upgrade` trying to run migration 0001 from scratch and hitting
    "table X already exists".

What it does (NON-DESTRUCTIVE — never drops or rewrites existing data):
  1. Refuses to run against anything that isn't a local SQLite dev database
     (hard safety guard — see _assert_safe_target below).
  2. Compares your live SQLite schema against the current SQLAlchemy models.
  3. Creates any table that's missing entirely.
  4. Adds any column that's missing from an existing table via
     ALTER TABLE ... ADD COLUMN (SQLite-safe: nullable columns, or NOT NULL
     columns that have a model-level default/server_default).
  5. Stamps the `alembic_version` table to `head`, so `flask db upgrade`
     behaves correctly from now on (no more "table already exists").

Usage:
    python tools/reconcile_dev_db.py            # apply changes
    python tools/reconcile_dev_db.py --dry-run  # just report, change nothing
─────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("FLASK_ENV", "development")

from sqlalchemy import inspect, text  # noqa: E402


def _assert_safe_target(db_uri: str) -> None:
    """Hard guard: this script must never touch anything but a local sqlite file."""
    if not db_uri.startswith("sqlite:///"):
        raise SystemExit(
            f"REFUSING TO RUN: resolved database is not local SQLite.\n"
            f"  URI: {db_uri}\n"
            f"This script only ever operates on a local dev SQLite file. "
            f"If FLASK_ENV isn't 'development', or DEV_CORE_DATABASE_URL is "
            f"pointed at something else, stop and check before proceeding."
        )


def _column_ddl(column, dialect) -> str:
    """Build the ADD COLUMN fragment for a single SQLAlchemy Column."""
    col_type = column.type.compile(dialect=dialect)
    parts = [f'"{column.name}"', col_type]

    if column.server_default is not None:
        default_sql = str(column.server_default.arg)
        parts.append(f"DEFAULT {default_sql}")
    elif not column.nullable:
        # SQLite requires a default for NOT NULL columns added via ALTER TABLE.
        # Use the model's Python-level default if present; otherwise fall
        # back to a type-appropriate zero-value so the ALTER doesn't fail.
        if column.default is not None and getattr(column.default, "arg", None) is not None:
            arg = column.default.arg
            if isinstance(arg, str):
                default_sql = f"'{arg}'"
            elif isinstance(arg, bool):
                default_sql = "1" if arg else "0"
            else:
                default_sql = str(arg)
        else:
            type_name = col_type.upper()
            if "INT" in type_name or "BOOL" in type_name:
                default_sql = "0"
            elif "CHAR" in type_name or "TEXT" in type_name or "CLOB" in type_name:
                default_sql = "''"
            elif "REAL" in type_name or "FLOA" in type_name or "DOUB" in type_name or "NUMERIC" in type_name:
                default_sql = "0"
            else:
                default_sql = "NULL"
        parts.append(f"DEFAULT {default_sql}")

    return " ".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Report only, change nothing")
    args = parser.parse_args()

    from app import create_app, db  # noqa: E402  (import after sys.path fix)

    app = create_app("development")

    with app.app_context():
        db_uri = app.config["SQLALCHEMY_DATABASE_URI"]
        _assert_safe_target(db_uri)
        print(f"Target database: {db_uri}\n")

        engine = db.engine
        inspector = inspect(engine)
        dialect = engine.dialect

        existing_tables = set(inspector.get_table_names())
        model_tables = db.metadata.tables

        missing_tables = []
        missing_columns = []  # (table_name, Column)

        for table_name, table in model_tables.items():
            if table_name not in existing_tables:
                missing_tables.append(table_name)
                continue
            existing_cols = {c["name"] for c in inspector.get_columns(table_name)}
            for column in table.columns:
                if column.name not in existing_cols:
                    missing_columns.append((table_name, column))

        if not missing_tables and not missing_columns:
            print("Schema already matches models. Nothing to reconcile.")
        else:
            if missing_tables:
                print(f"Missing tables ({len(missing_tables)}):")
                for t in missing_tables:
                    print(f"  - {t}")
                print()

            if missing_columns:
                print(f"Missing columns ({len(missing_columns)}):")
                for t, c in missing_columns:
                    print(f"  - {t}.{c.name}")
                print()

            if args.dry_run:
                print("(dry run — no changes made)")
            else:
                if missing_tables:
                    print("Creating missing tables...")
                    for table_name in missing_tables:
                        model_tables[table_name].create(bind=engine, checkfirst=True)
                        print(f"  created {table_name}")

                if missing_columns:
                    print("Adding missing columns...")
                    with engine.begin() as conn:
                        for table_name, column in missing_columns:
                            ddl_fragment = _column_ddl(column, dialect)
                            sql = f'ALTER TABLE "{table_name}" ADD COLUMN {ddl_fragment}'
                            conn.execute(text(sql))
                            print(f"  added {table_name}.{column.name}")

                print("\nSchema reconciled.")

        # ── Stamp Alembic to head ────────────────────────────────────────
        if args.dry_run:
            print("\n(dry run — skipping alembic stamp)")
        else:
            print("\nStamping Alembic version to head...")
            from flask_migrate import stamp
            stamp()
            print("Done. `flask db upgrade` will now work normally from here on.")


if __name__ == "__main__":
    main()
