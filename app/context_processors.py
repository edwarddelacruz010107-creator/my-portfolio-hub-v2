"""
app/context_processors.py — Template-global variables (v3.4.2)

CHANGELOG
─────────
v3.4.2 (this version) — Default Tenant Hardening
  • _load_globals(): Added explicit Priority 4 — when all three slug sources
    (g.tenant_slug, session, current_user.tenant_slug) are absent for an
    authenticated non-superadmin user, now falls back to 'default' instead of
    calling Profile.query.first() which could return any tenant's profile.

  • active_tenant_slug: now always returns 'default' for authenticated
    non-superadmin users even when slug resolution initially fails.

  • Superadmin / unauthenticated path unchanged (uses Profile.query.first()
    for cosmetic display only — no admin write actions occur on these paths).

  • Added diagnostic logger.debug() when slug resolution falls to Priority 4,
    so ops can identify misconfigured user accounts.

v3.1 (previous): g → session → current_user priority; remove Profile.first()
for non-superadmin to prevent cross-tenant leakage.
"""
import logging
from datetime import datetime, timezone

from flask import g
from flask_login import current_user

from app.models.portfolio import Project, Profile, Inquiry
try:
    from app.services.notification_service import get_unread_count as _get_notif_count, get_expiry_warning as _get_expiry_warning
    _HAS_NOTIF = True
except Exception:
    _HAS_NOTIF = False
from app import db
from app.utils import get_profile_completion
from app.tenant_security import resolve_active_tenant

logger = logging.getLogger(__name__)

_DEFAULT_TENANT_SLUG = 'default'


def _load_globals(app):
    # Skip all DB queries for non-content paths (static, health, favicon, etc.)
    from flask import request as _req
    _SKIP_PREFIXES = (
        '/static/', '/heartbeat', '/favicon.ico',
        '/robots.txt', '/sitemap.xml', '/health',
    )
    try:
        if any(_req.path.startswith(p) for p in _SKIP_PREFIXES):
            return dict(
                profile=None, project_count=0, unread_messages=0,
                unread_superadmin_messages=0, profile_completion=0,
                active_plan='Basic', plan_features={},
                now=datetime.now(timezone.utc),
                web3forms_access_key=app.config.get('WEB3FORMS_ACCESS_KEY'),
                heartbeat_state={},
                active_tenant_slug=None,
                notification_count=0,
                expiry_warning=None,
            )
    except RuntimeError:
        pass

    profile                    = None
    project_count              = 0
    unread_messages            = 0
    unread_superadmin_messages = 0
    profile_completion         = 0
    active_tenant_slug         = None

    try:
        from flask import session

        # ── Slug resolution (Priority 1→4) ───────────────────────────────────

        # Priority 1: g.tenant_slug — set by tenant_bp URL preprocessor.
        tenant_slug = getattr(g, 'tenant_slug', None)

        # Priority 2: session['tenant_slug'] — set by login / before_request.
        if not tenant_slug:
            tenant_slug = session.get('tenant_slug')

        # Priority 3: current_user.tenant_slug — authoritative DB value.
        # Covers Flask-Login cookie-restore where session may be re-created
        # without the tenant_slug key (common after server restart).
        if not tenant_slug and current_user.is_authenticated and not current_user.is_superadmin:
            tenant_slug = getattr(current_user, 'tenant_slug', None)

        # Priority 4 (FIX v3.4.2): Authenticated non-superadmin with no slug
        # from any source — default to 'default' rather than Profile.query.first().
        # Profile.query.first() returns rows in undefined order and can leak
        # another tenant's data into the template context.
        if not tenant_slug and current_user.is_authenticated and not current_user.is_superadmin:
            tenant_slug = _DEFAULT_TENANT_SLUG
            logger.debug(
                'CONTEXT: slug resolution exhausted all sources for '
                'authenticated user id=%s — falling back to %r. '
                'Check that User.tenant_slug is set and session is populated.',
                getattr(current_user, 'id', '?'), _DEFAULT_TENANT_SLUG,
            )

        # ── Profile + counters ────────────────────────────────────────────────

        if tenant_slug:
            profile = Profile.query.filter_by(tenant_slug=tenant_slug).first()
            project_count   = Project.query.filter_by(status='published', tenant_slug=tenant_slug).count()
            # Count unread: original messages not read + threads with new superadmin replies
            unread_messages = Inquiry.query.filter(
                Inquiry.tenant_slug == tenant_slug,
                db.or_(
                    Inquiry.is_read == False,
                    Inquiry.thread_unread_tenant > 0,
                )
            ).count()
            unread_superadmin_messages = Inquiry.query.filter(
                Inquiry.tenant_slug == tenant_slug,
                db.or_(
                    db.and_(Inquiry.is_read == False, Inquiry.sender == 'superadmin'),
                    Inquiry.thread_unread_tenant > 0,
                )
            ).count()
            active_tenant_slug = tenant_slug

            # If 'default' slug produced no profile, log once (fresh install).
            if profile is None and tenant_slug == _DEFAULT_TENANT_SLUG:
                logger.debug(
                    'CONTEXT: no Profile row for tenant_slug=%r — '
                    'templates will receive profile=None.',
                    _DEFAULT_TENANT_SLUG,
                )
        else:
            # Superadmin / unauthenticated root: cosmetic display only.
            # Profile.query.first() is acceptable here because no admin write
            # actions are performed on superadmin or public routes.
            profile         = Profile.query.first()
            project_count   = Project.query.filter_by(status='published').count()
            unread_messages = Inquiry.query.filter_by(is_read=False).count()
            unread_superadmin_messages = 0

        profile_completion = get_profile_completion(profile)

    except Exception:
        logger.exception('Context processor DB query failed')

    # ── Heartbeat state: SUPERADMIN ONLY (OWASP A01 — Broken Access Control) ──
    # Monitoring data (DB status, uptime, self-ping, heartbeat endpoints) is
    # ops infrastructure intel. Exposing it to tenant admins is an information
    # disclosure risk. Tenant admins receive an empty dict; the monitoring
    # widget in admin/dashboard.html will not render.
    heartbeat_state: dict = {}
    if current_user.is_authenticated and getattr(current_user, 'is_superadmin', False):
        try:
            from app.heartbeat import get_heartbeat_state
            heartbeat_state = get_heartbeat_state() or {}
        except Exception:
            pass

    return dict(
        profile=profile,
        project_count=project_count,
        unread_messages=unread_messages,
        unread_superadmin_messages=unread_superadmin_messages,
        profile_completion=profile_completion,
        active_plan=profile.effective_plan() if profile else 'Basic',
        plan_features=profile.plan_features() if profile else {},
        now=datetime.now(timezone.utc),
        web3forms_access_key=app.config.get('WEB3FORMS_ACCESS_KEY'),
        heartbeat_state=heartbeat_state,
        active_tenant_slug=active_tenant_slug,
    )


def register_context_processors(app) -> None:
    @app.context_processor
    def inject_globals() -> dict:
        if not hasattr(g, '_globals_cache'):
            g._globals_cache = _load_globals(app)
        return g._globals_cache