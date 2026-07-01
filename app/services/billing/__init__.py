"""
billing domain services -- Phase 3 sub-packaging.

This package's __init__.py doubles as the compatibility shim for the old
flat `app/services/billing.py` module (now at app/services/billing/billing.py),
since a domain package and a same-named flat module cannot coexist on disk.
Old code doing `from app.services.billing import X` keeps working unchanged.
"""
from .billing import *  # noqa: F401,F403
from . import billing as _self_module
globals().update({k: v for k, v in vars(_self_module).items() if not k.startswith("__")})
del _self_module
