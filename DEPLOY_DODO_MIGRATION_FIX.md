# Dodo deployment migration fix

## Root cause
Migration `0055_add_dodo_payments_fields.py` referenced a nonexistent Alembic parent revision (`0054`). The actual revision ID declared by the previous migration is `0054_oauth_local_account_setup`. Alembic therefore could not resolve the migration graph during `flask db upgrade`, preventing Render from reaching Gunicorn startup.

## Fix
- Changed `down_revision` in migration 0055 to `0054_oauth_local_account_setup`.
- Updated the migration docstring to match.

## Deploy
For the canonical Render Blueprint, keep `RUN_MIGRATIONS=false` and redeploy;
`preDeployCommand` owns `db-upgrade-all`. Set `RUN_MIGRATIONS=true` only for a
self-hosted Docker deployment that has no separate pre-deploy migration phase.
Never enable both paths.
