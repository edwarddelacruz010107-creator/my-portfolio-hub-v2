# Alembic 0054 compatibility fix

The deployment traceback `KeyError: '0054'` is produced while Alembic builds its revision graph. It occurs before Redis can affect migration execution.

This release adds a no-op compatibility revision named `0054` between:

- `0054_oauth_local_account_setup`
- `0055`

It also makes migration `0055` idempotent so a retry is safe if an earlier deployment partially created Dodo columns or indexes.
