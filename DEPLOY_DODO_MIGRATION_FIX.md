# Dodo deployment migration fix

## Root cause
Migration `0055_add_dodo_payments_fields.py` referenced a nonexistent Alembic parent revision (`0054`). The actual revision ID declared by the previous migration is `0054_oauth_local_account_setup`. Alembic therefore could not resolve the migration graph during `flask db upgrade`, preventing Render from reaching Gunicorn startup.

## Fix
- Changed `down_revision` in migration 0055 to `0054_oauth_local_account_setup`.
- Updated the migration docstring to match.

## Deploy
Keep `RUN_MIGRATIONS=true` and redeploy. The startup sequence should proceed from `Applying incremental Alembic migrations after bootstrap...` to `Ensuring tenant-bound schema...`, then `Launching app...`.
