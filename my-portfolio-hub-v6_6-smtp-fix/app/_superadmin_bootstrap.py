"""
app/_superadmin_bootstrap.py
────────────────────────────────────────────────────────────────────────────
Auto-bootstrap superadmin on production startup.

DESIGN:
  • Called once inside create_app() → app_context block, AFTER db is
    verified reachable.
  • Reads credentials from environment variables — NEVER hardcoded.
  • Idempotent: if superadmin already exists and credentials match, does nothing.
  • If superadmin exists but SUPERADMIN_PASSWORD is set in env, updates the
    password hash (allows password rotation without shell access).
  • Logs a WARNING (never DEBUG) so the event is always visible in Render logs.
  • Does NOT log the password, token, or hash.

Environment Variables:
  SUPERADMIN_USERNAME  (default: superadmin)
  SUPERADMIN_EMAIL     (default: superadmin@portfolio.local)
  SUPERADMIN_PASSWORD  (required for bootstrap; if absent and no user exists,
                        a secure random password is generated and logged ONCE)

Security:
  • Password is hashed with werkzeug.security.generate_password_hash
    (bcrypt-backed in modern werkzeug).
  • The plain-text password is only ever printed to stdout at first-run and
    only when auto-generated (i.e. when SUPERADMIN_PASSWORD is not set).
  • After first login, rotate the password via the settings UI and remove
    SUPERADMIN_PASSWORD from your env vars.
"""

from __future__ import annotations

import logging
import os
import secrets

logger = logging.getLogger(__name__)


def auto_bootstrap_superadmin(app, db) -> None:
    """
    Called inside create_app() within an active app_context.
    Creates the superadmin user if one does not already exist, or updates
    the password hash if SUPERADMIN_PASSWORD changed in the environment.

    This is the ONLY place automatic superadmin creation happens in production.
    It fires on EVERY dyno restart — but is fully idempotent.
    """
    try:
        _run_bootstrap(app, db)
    except Exception as exc:
        # NEVER crash the app over a bootstrap failure — log loudly and continue.
        logger.error(
            "SUPERADMIN_BOOTSTRAP: failed — login will not work until resolved. "
            "Error: %s",
            exc,
            exc_info=True,
        )


def _run_bootstrap(app, db) -> None:
    from app.models import User
    from app.models.portfolio import Tenant

    username = (
        os.environ.get('SUPERADMIN_USERNAME')
        or app.config.get('SUPERADMIN_USERNAME')
        or 'superadmin'
    )
    email = (
        os.environ.get('SUPERADMIN_EMAIL')
        or app.config.get('SUPERADMIN_EMAIL')
        or 'superadmin@portfolio.local'
    )
    # Read password from environment ONLY — never from config (avoids log dumps)
    env_password = os.environ.get('SUPERADMIN_PASSWORD', '').strip()

    # ── Ensure 'default' tenant exists (required FK for User) ────────────────
    tenant = Tenant.query.filter_by(slug='default').first()
    if not tenant:
        tenant = Tenant(
            slug='default',
            company_name='Default Portfolio',
            email=email,
            status='active',
            plan='Basic',
        )
        db.session.add(tenant)
        db.session.flush()
        logger.info("SUPERADMIN_BOOTSTRAP: created missing 'default' tenant")

    # ── Look up existing superadmin ──────────────────────────────────────────
    existing: User | None = (
        User.query.filter_by(is_superadmin=True).first()
        or User.query.filter_by(username=username).first()
    )

    if existing:
        changed = False

        # Ensure flags are correct (guards against manual DB tampering)
        if not existing.is_superadmin:
            existing.is_superadmin = True
            existing.is_admin = True
            changed = True
            logger.warning(
                "SUPERADMIN_BOOTSTRAP: repaired missing is_superadmin=True "
                "on user id=%s username=%r",
                existing.id, existing.username,
            )

        # If SUPERADMIN_PASSWORD env var is set, rotate the hash.
        # This allows password recovery without Render shell access:
        # just set SUPERADMIN_PASSWORD=<new>, redeploy, then unset it.
        if env_password:
            existing.password = env_password   # hashed by the setter
            changed = True
            logger.warning(
                "SUPERADMIN_BOOTSTRAP: password updated from SUPERADMIN_PASSWORD "
                "env var for user id=%s username=%r. "
                "Remove SUPERADMIN_PASSWORD from env after confirming login.",
                existing.id, existing.username,
            )

        if changed:
            db.session.commit()

        logger.info(
            "SUPERADMIN_BOOTSTRAP: superadmin already exists (id=%s username=%r) — "
            "no creation needed.",
            existing.id, existing.username,
        )
        return

    # ── No superadmin found — create one ────────────────────────────────────
    password_was_generated = False
    if not env_password:
        # Auto-generate a secure password. Print it ONCE to logs.
        # The operator MUST capture this from Render build/deploy logs and
        # change it immediately after first login.
        env_password = secrets.token_urlsafe(20)
        password_was_generated = True

    superadmin = User(
        username=username,
        email=email,
        tenant=tenant,
        tenant_slug=tenant.slug,
        is_admin=True,
        is_superadmin=True,
    )
    superadmin.password = env_password   # hashed by the setter

    db.session.add(superadmin)
    db.session.commit()

    if password_was_generated:
        # Print to stdout so it appears in Render's deploy log.
        # This is intentional one-time disclosure — treat it like a root password.
        print("=" * 72)
        print("SUPERADMIN CREATED (auto-generated password — change immediately)")
        print(f"  Username: {username}")
        print(f"  Email:    {email}")
        print(f"  Password: {env_password}")
        print(f"  Login:    /superadmin/login")
        print("  To avoid seeing this: set SUPERADMIN_PASSWORD in Render env,")
        print("  then rotate it via the UI after first login and remove the env var.")
        print("=" * 72)
        logger.warning(
            "SUPERADMIN_BOOTSTRAP: created superadmin username=%r with "
            "AUTO-GENERATED password (printed to deploy log). "
            "Change password immediately after first login.",
            username,
        )
    else:
        logger.warning(
            "SUPERADMIN_BOOTSTRAP: created superadmin username=%r using "
            "SUPERADMIN_PASSWORD env var. "
            "Remove env var and rotate password after first login.",
            username,
        )
