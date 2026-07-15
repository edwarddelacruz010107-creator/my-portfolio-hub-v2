"""Read-only schema readiness checks and request guard.

An empty SQLite file accepts ``SELECT 1`` even when every application table is
missing. Production must fail at startup in that state; development returns a
controlled setup page until the versioned migrations have run.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SchemaState:
    ready: bool
    details: tuple[str, ...] = ()

    @property
    def summary(self) -> str:
        return "; ".join(self.details) if self.details else "ready"


_CORE_REQUIRED = frozenset({"alembic_version", "tenants", "users"})
_TENANT_REQUIRED = frozenset({
    "alembic_version_tenant", "profile", "projects", "skills", "services",
})


def inspect_schema_state(db) -> SchemaState:
    """Verify required tables, migration heads, and tenant model drift."""
    from sqlalchemy import inspect

    details: list[str] = []
    try:
        core_engine = db.engine
        tenant_engine = db.engines.get("tenant")
        if tenant_engine is None:
            return SchemaState(False, ("tenant database bind is not configured",))

        core_tables = set(inspect(core_engine).get_table_names())
        tenant_tables = set(inspect(tenant_engine).get_table_names())
        missing_core = sorted(_CORE_REQUIRED - core_tables)
        missing_tenant = sorted(_TENANT_REQUIRED - tenant_tables)
        if missing_core:
            details.append("core missing: " + ", ".join(missing_core))
        if missing_tenant:
            details.append("tenant missing: " + ", ".join(missing_tenant))
        if details:
            return SchemaState(False, tuple(details))

        from app.services.database_migrations import migration_status, tenant_schema_drift

        status = migration_status()
        for name in ("core", "tenant"):
            current = status[name]["current"]
            expected = status[name]["expected"]
            if current != expected:
                details.append(f"{name} migration head is {current!r}; expected {expected!r}")
        drift = tenant_schema_drift(tenant_engine)
        if drift:
            details.extend(drift[:20])
            if len(drift) > 20:
                details.append(f"and {len(drift) - 20} more tenant schema differences")
    except Exception as exc:
        details.append(f"schema verification error: {type(exc).__name__}: {exc}")
    return SchemaState(not details, tuple(details))


def install_request_schema_guard(app) -> None:
    """Return a controlled 503 in development while schema setup is pending."""
    from flask import jsonify, render_template, request

    allowed_prefixes = ("/static/", "/livez", "/readyz", "/health", "/favicon")

    @app.before_request
    def _require_ready_schema():
        if app.config.get("SCHEMA_READY", False):
            return None
        if request.path.startswith(allowed_prefixes):
            return None
        details = tuple(app.config.get("SCHEMA_READINESS_DETAILS") or ())
        if request.path.startswith("/api/") or request.is_json:
            return jsonify(
                status="unavailable",
                code="database_setup_required",
                message="Database migrations have not completed.",
            ), 503
        return render_template("errors/setup_needed.html", schema_details=details), 503

