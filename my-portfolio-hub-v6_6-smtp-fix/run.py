"""
run.py — Portfolio CMS development entry point

Development:   flask run   OR   python run.py
Production:    gunicorn wsgi:app

HIGH-03 FIX: All CLI commands (ensure-default-tenant, init-db, create-admin,
create-superadmin, seed-sample-data, normalize-skill-visibility, db-upgrade,
run-renewal-check) are now registered in app/__init__.py via
register_cli_commands(). This makes them available regardless of entry point:
wsgi.py, gunicorn, or flask --app wsgi.py.

run.py only provides:
  1. The `app` instance used by `flask run` / `python run.py` in development.
  2. Shell context helpers for `flask shell`.
"""
import os
import socket

from app import create_app, db

config_name = os.environ.get('FLASK_ENV', 'default').lower()
if config_name not in {'development', 'production', 'testing', 'default'}:
    config_name = 'default'

app = create_app(config_name)

_original_getfqdn = socket.getfqdn

# getfqdn monkey-patch: only enabled explicitly via DISABLE_GETFQDN=1
# If your dev machine has slow reverse-DNS lookups causing startup lag, add:
#   127.0.0.1  <your-hostname>   to /etc/hosts instead.
if os.environ.get('DISABLE_GETFQDN', '').lower() in ('1', 'true', 'yes'):
    socket.getfqdn = lambda name='': name


# ── Shell context ─────────────────────────────────────────────────────────────

@app.shell_context_processor
def make_shell_context():
    """Add common objects to `flask shell` so they're available without import."""
    from app.models import User
    from app.models.portfolio  import (
    Tenant, Profile, Skill, Project, Testimonial, ActivityLog, Inquiry,
)
    from app.models.tenant_form_settings import TenantFormSettings
    return dict(
        db=db,
        User=User,
        Profile=Profile,
        Skill=Skill,
        Project=Project,
        Testimonial=Testimonial,
        ActivityLog=ActivityLog,
        Inquiry=Inquiry,
        Tenant=Tenant,
        TenantFormSettings=TenantFormSettings,
    )


# ── Dev server ────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    debug_mode = app.config.get('DEBUG', False)
    port       = int(os.environ.get('PORT', 5000))
    try:
        app.run(debug=debug_mode, host='0.0.0.0', port=port)
    finally:
        socket.getfqdn = _original_getfqdn
