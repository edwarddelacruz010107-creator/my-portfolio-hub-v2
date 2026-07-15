# Core migration location

The versioned core history remains in `migrations/versions`, using
`migrations/env.py`. This directory is retained only to document that mapping;
it must not contain a second core Alembic environment or revision chain.

Run `flask db-upgrade-all` so the top-level core history and
`migrations/tenant` history are applied and verified together.
