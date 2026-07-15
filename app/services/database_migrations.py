"""Deterministic migration orchestration for the core and tenant databases.

The application has two SQLAlchemy binds but historically only the core bind
had a runnable Alembic history.  This module is the single deployment entry
point for both histories.  It deliberately does not call ``create_all`` and it
never stamps a revision that has not run.
"""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
from typing import Iterator

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from app.utils.db_config import get_database_url, get_tenant_database_url


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CORE_MIGRATIONS = PROJECT_ROOT / "migrations"
TENANT_MIGRATIONS = CORE_MIGRATIONS / "tenant"
CORE_VERSION_TABLE = "alembic_version"
TENANT_VERSION_TABLE = "alembic_version_tenant"

# Stable, application-specific signed bigint used by PostgreSQL advisory locks.
MIGRATION_LOCK_ID = 5_571_022_649_856_083_778


class MigrationVerificationError(RuntimeError):
    """Raised when a database did not reach its expected migration head."""


def _connect_args(url: str) -> dict[str, object]:
    if url.startswith("postgresql"):
        return {
            "sslmode": os.environ.get("DB_SSLMODE", "require"),
            "connect_timeout": 30,
            "options": "-c lock_timeout=60000 -c statement_timeout=120000",
        }
    return {}


def _engine(url: str) -> Engine:
    return create_engine(
        url,
        poolclass=NullPool,
        connect_args=_connect_args(url),
    )


def _config(script_location: Path) -> Config:
    config = Config(str(CORE_MIGRATIONS / "alembic.ini"))
    config.set_main_option("script_location", str(script_location))
    return config


def _expected_heads(script_location: Path) -> tuple[str, ...]:
    heads = tuple(ScriptDirectory.from_config(_config(script_location)).get_heads())
    if len(heads) != 1:
        raise MigrationVerificationError(
            f"Expected exactly one Alembic head in {script_location}, found {heads!r}."
        )
    return heads


def _current_heads(url: str, version_table: str) -> tuple[str, ...]:
    engine = _engine(url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={"version_table": version_table},
            )
            return tuple(context.get_current_heads())
    finally:
        engine.dispose()


def verify_migration_head(
    *,
    name: str,
    url: str,
    script_location: Path,
    version_table: str,
) -> str:
    expected = _expected_heads(script_location)
    current = _current_heads(url, version_table)
    if current != expected:
        raise MigrationVerificationError(
            f"{name} database migration drift: expected head {expected!r}, found {current!r}."
        )
    return expected[0]


def _tenant_models():
    from app.models.tenant_data import (
        Certificate,
        Profile,
        Project,
        ProjectReaction,
        Service,
        Skill,
        Testimonial,
        WorkExperience,
    )

    return (
        Profile,
        Skill,
        Project,
        ProjectReaction,
        Testimonial,
        Service,
        Certificate,
        WorkExperience,
    )


def tenant_schema_drift(engine: Engine) -> list[str]:
    """Return schema drift without changing the tenant database."""
    inspector = inspect(engine)
    drift: list[str] = []

    for model in _tenant_models():
        table = model.__table__
        if not inspector.has_table(table.name):
            drift.append(f"missing table {table.name}")
            continue

        actual_columns = {column["name"] for column in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name not in actual_columns:
                drift.append(f"missing column {table.name}.{column.name}")

        actual_indexes = {
            index.get("name"): (
                tuple(index.get("column_names") or ()),
                bool(index.get("unique")),
            )
            for index in inspector.get_indexes(table.name)
            if index.get("name")
        }
        for index in table.indexes:
            expected = (tuple(column.name for column in index.columns), bool(index.unique))
            actual = actual_indexes.get(index.name)
            if actual is None:
                drift.append(f"missing index {index.name}")
            elif actual != expected:
                drift.append(
                    f"index {index.name} differs: expected {expected!r}, found {actual!r}"
                )

    return drift


def verify_tenant_schema(url: str | None = None) -> None:
    engine = _engine(url or get_tenant_database_url())
    try:
        drift = tenant_schema_drift(engine)
    finally:
        engine.dispose()
    if drift:
        details = "; ".join(drift[:20])
        if len(drift) > 20:
            details += f"; and {len(drift) - 20} more"
        raise MigrationVerificationError(f"Tenant schema verification failed: {details}")


@contextmanager
def migration_lock() -> Iterator[None]:
    """Serialize deployments with a PostgreSQL advisory lock.

    SQLite development databases are already serialized by SQLite's writer
    lock, so no extra lock statement is necessary there.
    """
    engine = _engine(get_database_url())
    connection = engine.connect()
    is_postgres = connection.dialect.name == "postgresql"
    try:
        if is_postgres:
            connection.execute(
                text("SELECT pg_advisory_lock(:lock_id)"),
                {"lock_id": MIGRATION_LOCK_ID},
            )
        yield
    finally:
        if is_postgres:
            connection.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": MIGRATION_LOCK_ID},
            )
        connection.close()
        engine.dispose()


def upgrade_core_database() -> str:
    command.upgrade(_config(CORE_MIGRATIONS), "head")
    return verify_migration_head(
        name="Core",
        url=get_database_url(),
        script_location=CORE_MIGRATIONS,
        version_table=CORE_VERSION_TABLE,
    )


def upgrade_tenant_database() -> str:
    command.upgrade(_config(TENANT_MIGRATIONS), "head")
    head = verify_migration_head(
        name="Tenant",
        url=get_tenant_database_url(),
        script_location=TENANT_MIGRATIONS,
        version_table=TENANT_VERSION_TABLE,
    )
    verify_tenant_schema()
    return head


def migration_status() -> dict[str, dict[str, tuple[str, ...]]]:
    core_url = get_database_url()
    tenant_url = get_tenant_database_url()
    return {
        "core": {
            "expected": _expected_heads(CORE_MIGRATIONS),
            "current": _current_heads(core_url, CORE_VERSION_TABLE),
        },
        "tenant": {
            "expected": _expected_heads(TENANT_MIGRATIONS),
            "current": _current_heads(tenant_url, TENANT_VERSION_TABLE),
        },
    }
