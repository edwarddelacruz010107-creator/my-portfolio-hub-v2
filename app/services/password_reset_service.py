"""
COMPATIBILITY SHIM -- Phase 3 service-layer sub-packaging.
Real implementation moved to: app/services/auth/password_reset_service.py.py
Old import paths keep working: from app.services.password_reset_service import <anything>
Do not add logic here. Edit the real module instead.
"""
from app.services.auth import password_reset_service as _moved_module
from app.services.auth.password_reset_service import *  # noqa: F401,F403
globals().update({k: v for k, v in vars(_moved_module).items() if not k.startswith("__")})
del _moved_module
