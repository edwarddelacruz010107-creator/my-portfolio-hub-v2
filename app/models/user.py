"""
app/models/user.py — Compatibility shim (v5.1)

The canonical User model was consolidated into app/models/core.py as part of
the dual-database architecture refactor.  This file is kept as a shim so that
any future accidental import of:

    from app.models.user import User

continues to return the correct, single ORM class registered on the shared
MetaData — rather than raising:

    sqlalchemy.exc.InvalidRequestError:
        Table 'users' is already defined for this MetaData instance.

DO NOT add class User(db.Model) here.  Canonical owner: app/models/core.py
"""

from app.models.core import User  # noqa: F401

__all__ = ['User']
