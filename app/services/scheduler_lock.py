"""
COMPATIBILITY SHIM -- Phase 3 service-layer sub-packaging.
Real implementation moved to: app/services/billing/scheduler_lock.py.py
Old import paths keep working: from app.services.scheduler_lock import <anything>
Do not add logic here. Edit the real module instead.
"""
from app.services.billing import scheduler_lock as _moved_module
from app.services.billing.scheduler_lock import *  # noqa: F401,F403
globals().update({k: v for k, v in vars(_moved_module).items() if not k.startswith("__")})
del _moved_module
