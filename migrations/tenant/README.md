# Tenant database migrations

This is the only migration history for models using `__bind_key__ = "tenant"`.
It uses the `alembic_version_tenant` version table so it remains independent
when core and tenant binds share one PostgreSQL database.

Run both histories through `flask db-upgrade-all`. Do not run `create_all()` or
stamp this history manually. `flask ensure-tenant-schema` remains as a
backward-compatible alias that runs and verifies this tenant history.
