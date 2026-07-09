"""
COMPATIBILITY SHIM -- Phase 3 service-layer sub-packaging.
Real implementation moved to: app/services/communication/forms.py.py
Old import paths keep working: from app.services.forms import <anything>
Do not add logic here. Edit the real module instead.
"""
from app.services.communication import forms as _moved_module
from app.services.communication.forms import *  # noqa: F401,F403
globals().update({k: v for k, v in vars(_moved_module).items() if not k.startswith("__")})
del _moved_module
