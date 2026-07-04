"""Email provider registry and defaults for superadmin priority handling.

Central source of truth for valid provider names and the system default
priority order. Import from models and services to validate and repair
corrupted DB values.
"""
VALID_EMAIL_PROVIDERS = ('mailersend', 'smtp', 'resend')
DEFAULT_PROVIDER_PRIORITY = list(VALID_EMAIL_PROVIDERS)
