"""
COMPATIBILITY SHIM -- Phase 3 service-layer sub-packaging.
Real implementation moved to: app/services/billing/billing_handlers.py.py
Old import paths keep working: from app.services.billing_handlers import <anything>
Do not add logic here. Edit the real module instead.
"""
from app.services.billing import billing_handlers as _moved_module
from app.services.billing.billing_handlers import *  # noqa: F401,F403
globals().update({k: v for k, v in vars(_moved_module).items() if not k.startswith("__")})
del _moved_module
