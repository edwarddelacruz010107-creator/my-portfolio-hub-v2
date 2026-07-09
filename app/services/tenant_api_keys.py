"""
COMPATIBILITY SHIM -- Phase 3 service-layer sub-packaging.
Real implementation moved to: app/services/tenant/tenant_api_keys.py.py
Old import paths keep working: from app.services.tenant_api_keys import <anything>
Do not add logic here. Edit the real module instead.
"""
from app.services.tenant import tenant_api_keys as _moved_module
from app.services.tenant.tenant_api_keys import *  # noqa: F401,F403
globals().update({k: v for k, v in vars(_moved_module).items() if not k.startswith("__")})
del _moved_module
