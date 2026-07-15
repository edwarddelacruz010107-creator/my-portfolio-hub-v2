"""Compatibility wrapper for deterministic local development migrations.

The former tool repaired tables with direct DDL and stamped Alembic head. That
could hide migration failures, so it now delegates to the same versioned core
and tenant histories used in deployment.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("FLASK_ENV", "development")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report migration heads and tenant drift without changing schema.",
    )
    args = parser.parse_args()

    from app.services.database_migrations import (
        migration_lock,
        migration_status,
        upgrade_core_database,
        upgrade_tenant_database,
        verify_tenant_schema,
    )
    from app.utils.db_config import get_database_url, get_tenant_database_url

    for name, url in (
        ("core", get_database_url()),
        ("tenant", get_tenant_database_url()),
    ):
        if not url.startswith("sqlite:///"):
            raise SystemExit(
                f"REFUSING TO RUN: {name} target is not local SQLite. "
                "Use `flask db-upgrade-all` through the reviewed deployment path."
            )

    if args.dry_run:
        status = migration_status()
        for name in ("core", "tenant"):
            print(
                f"{name}: current={status[name]['current']!r} "
                f"expected={status[name]['expected']!r}"
            )
        verify_tenant_schema()
        print("Tenant schema matches the current model contract.")
        return

    with migration_lock():
        core_head = upgrade_core_database()
        tenant_head = upgrade_tenant_database()
    print(f"Core database reached {core_head}.")
    print(f"Tenant database reached {tenant_head}.")
    print("Local databases were reconciled only through versioned migrations.")


if __name__ == "__main__":
    main()
