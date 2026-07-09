"""
COMPATIBILITY SHIM -- Phase 3 service-layer sub-packaging.
Real implementation moved to: app/services/billing/renewal_scheduler.py.py
Old import paths keep working: from app.services.renewal_scheduler import <anything>
Do not add logic here. Edit the real module instead.
"""
from app.services.billing import renewal_scheduler as _moved_module
from app.services.billing.renewal_scheduler import *  # noqa: F401,F403
globals().update({k: v for k, v in vars(_moved_module).items() if not k.startswith("__")})
del _moved_module
