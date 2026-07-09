"""
COMPATIBILITY SHIM -- Phase 3 service-layer sub-packaging.
Real implementation moved to: app/services/email/web3forms_service.py.py
Old import paths keep working: from app.services.web3forms_service import <anything>
Do not add logic here. Edit the real module instead.
"""
from app.services.email import web3forms_service as _moved_module
from app.services.email.web3forms_service import *  # noqa: F401,F403
globals().update({k: v for k, v in vars(_moved_module).items() if not k.startswith("__")})
del _moved_module
