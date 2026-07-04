"""
Compatibility shim — re-export the canonical implementation from
`app.services.communication.contact_service` so older imports keep working.
Do not add logic here; edit the real module instead.
"""

from app.services.communication import contact_service as _moved_module
from app.services.communication.contact_service import *  # noqa: F401,F403
globals().update({k: v for k, v in vars(_moved_module).items() if not k.startswith("__")})
del _moved_module
