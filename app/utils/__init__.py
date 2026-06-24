from __future__ import annotations

from app.security import validate_magic_bytes
"""
app/utils/__init__.py — Shared utilities for Portfolio CMS

Provides:
  • BILLING_PLANS dict + billing price helpers  (v3.5 additions)
  • log_activity()          — write to ActivityLog model
  • log_billing_event()     — write billing events to ActivityLog
  • save_image()            — validate + save uploaded image to disk
  • delete_image()          — remove image file from disk
  • get_profile_completion()— compute profile completeness %
  • is_upload_file()        — check FileStorage has real content
  • is_paymongo_enabled()   — read PayMongo toggle from PlatformSetting
  • set_paymongo_enabled()  — write PayMongo toggle to PlatformSetting
  • send_inquiry_email()    — send contact-form email via MailerSend
  • validate_contact_payload() — validate contact form POST data
"""


import logging
import os
import uuid
from typing import Optional

from flask import current_app, request
from werkzeug.utils import secure_filename

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# BILLING PLANS  (single source of truth for tenant cards + superadmin view)
# ─────────────────────────────────────────────────────────────────────────────

YEARLY_DISCOUNT = 0.83   # ~17 % off when paying annually

BILLING_PLANS: dict[str, dict] = {
    "Basic": {
        "label":           "Basic",
        "currency_symbol": "₱",
        "price_monthly":   19.00,
        "price_yearly":    round(19.00  * 12 * YEARLY_DISCOUNT, 2),
        "duration_days":   30,
        "price":           19.00,          # legacy compat key
        "price_label":     "₱19.00/mo",
        "description":     "Essential portfolio support with basic billing and updates.",
        "features": [
            "Up to 5 portfolio projects",
            "Email-based support",
            "PayMongo checkout billing",
            "Standard portfolio analytics",
        ],
    },
    "Pro": {
        "label":           "Pro",
        "currency_symbol": "₱",
        "price_monthly":   49.00,
        "price_yearly":    round(49.00 * 12 * YEARLY_DISCOUNT, 2),
        "duration_days":   30,
        "price":           49.00,
        "price_label":     "₱49.00/mo",
        "description":     "Priority billing, subscription history, and automated renewals.",
        "features": [
            "Unlimited portfolio projects",
            "Priority support response",
            "Custom branding options",
            "Billing history dashboard",
            "Advanced analytics",
        ],
    },
    "Enterprise": {
        "label":           "Enterprise",
        "currency_symbol": "₱",
        "price_monthly":   99.00,
        "price_yearly":    round(99.00 * 12 * YEARLY_DISCOUNT, 2),
        "duration_days":   30,
        "price":           99.00,
        "price_label":     "₱99.00/mo",
        "description":     "Advanced enterprise billing with dedicated support.",
        "features": [
            "Dedicated account manager",
            "Custom integrations and onboarding",
            "Service level agreement (SLA) support",
            "White-label portfolio experience",
            "API and team access",
        ],
    },
}

_PLAN_ALIASES: dict[str, str] = {
    "basic":        "Basic",
    "pro":          "Pro",
    "professional": "Pro",
    "enterprise":   "Enterprise",
    "ent":          "Enterprise",
}


def normalize_plan_name(plan: str) -> str:
    """Return the canonical plan key ('Basic' | 'Pro' | 'Enterprise')."""
    if not plan:
        return "Basic"
    key = plan.strip().lower()
    return _PLAN_ALIASES.get(key, plan.strip().title())


def get_plan_price(plan: str, billing_cycle: str = "monthly") -> float:
    """
    Return the total amount charged for one billing period.

    monthly → price_monthly  (e.g. 49.00)
    yearly  → price_yearly   (e.g. 487.32, already discounted)
    """
    norm = normalize_plan_name(plan)
    data = BILLING_PLANS.get(norm, BILLING_PLANS["Basic"])
    if billing_cycle == "yearly":
        return float(data.get("price_yearly", data["price"] * 12 * YEARLY_DISCOUNT))
    return float(data.get("price_monthly", data["price"]))


def get_plan_price_label(plan: str, billing_cycle: str = "monthly") -> str:
    """
    Return a human-readable price string.

    Examples:
        get_plan_price_label('Pro', 'monthly') → '₱49.00/mo'
        get_plan_price_label('Pro', 'yearly')  → '₱487.32/yr (Save ~17%)'
    """
    norm = normalize_plan_name(plan)
    data = BILLING_PLANS.get(norm, BILLING_PLANS["Basic"])
    symbol = data.get("currency_symbol", "₱")
    price = get_plan_price(plan, billing_cycle)
    if billing_cycle == "yearly":
        return f"{symbol}{price:,.2f}/yr (Save ~17%)"
    return f"{symbol}{price:,.2f}/mo"


def get_yearly_savings_label(plan: str) -> str:
    """Return e.g. 'Save ₱100.68/yr' for the savings badge."""
    norm = normalize_plan_name(plan)
    data = BILLING_PLANS.get(norm, BILLING_PLANS["Basic"])
    symbol = data.get("currency_symbol", "₱")
    savings = get_plan_price(plan, "monthly") * 12 - get_plan_price(plan, "yearly")
    return f"Save {symbol}{savings:,.2f}/yr"


# ─────────────────────────────────────────────────────────────────────────────
# ACTIVITY LOGGING
# ─────────────────────────────────────────────────────────────────────────────

def log_activity(
    action: str,
    entity_type: str | None = None,
    entity_name: str | None = None,
    description: str | None = None,
    tenant_slug: str | None = None,
) -> None:
    """
    Append a row to the ActivityLog table.

    Silently swallows errors so a logging failure never breaks a request.
    v3.6: also stamps user_id and username for cross-tenant audit filtering.
    """
    try:
        from app import db
        from app.models.portfolio import ActivityLog
        from flask_login import current_user

        slug = tenant_slug
        user_id  = None
        username = None

        if slug is None:
            try:
                slug = current_user.tenant_slug if current_user.is_authenticated else None
            except Exception:
                slug = None

        try:
            if current_user.is_authenticated:
                user_id  = getattr(current_user, 'id', None)
                username = getattr(current_user, 'username', None)
        except Exception:
            pass

        ip = request.headers.get("X-Forwarded-For", request.remote_addr)
        if ip and "," in ip:
            ip = ip.split(",")[0].strip()

        entry = ActivityLog(
            tenant_slug=slug,
            user_id=user_id,
            username=username,
            action=action,
            entity_type=entity_type,
            entity_name=str(entity_name)[:200] if entity_name else None,
            description=str(description)[:500] if description else None,
            ip_address=str(ip)[:45] if ip else None,
        )
        db.session.add(entry)
        db.session.commit()
    except Exception:
        logger.exception("log_activity failed (action=%s, entity=%s)", action, entity_name)
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass


def log_billing_event(
    action: str,
    tenant_slug: str | None = None,
    description: str | None = None,
) -> None:
    """Write a billing-related event to the ActivityLog."""
    log_activity(
        action=action,
        entity_type="billing",
        entity_name=tenant_slug,
        description=description,
        tenant_slug=tenant_slug,
    )


# ─────────────────────────────────────────────────────────────────────────────
# FILE / IMAGE HELPERS
# ─────────────────────────────────────────────────────────────────────────────

_ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp"}


def is_upload_file(file_storage) -> bool:
    """
    Return True if file_storage is a real uploaded file with content.

    Guards against empty FileStorage objects that Flask creates when no
    file was selected in a form.
    """
    if file_storage is None:
        return False
    filename = getattr(file_storage, "filename", None)
    return bool(filename and filename.strip())


def _allowed_image(filename: str) -> bool:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in _ALLOWED_IMAGE_EXTENSIONS


def save_image(
    file_storage,
    subfolder: str,
    max_size: tuple[int, int] | None = None,
    allowed_extensions: set[str] | None = None,
    quality: int = 85,
) -> tuple[str | None, str | None]:
    """
    Validate and save an uploaded image.

    Args:
        file_storage:       Werkzeug FileStorage object.
        subfolder:          Subfolder under UPLOAD_FOLDER ('profiles', 'projects', …).
        max_size:           Optional (width, height) to resize to with Pillow.
        allowed_extensions: Override the default allowed set.

    Returns:
        (filename, None)  on success
        (None, error_msg) on failure
    """
    if not is_upload_file(file_storage):
        return None, "No file provided."

    allowed = allowed_extensions or _ALLOWED_IMAGE_EXTENSIONS
    original_name = secure_filename(file_storage.filename)
    ext = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else ""

    if ext not in allowed:
        return None, f"File type '.{ext}' is not allowed. Accepted: {', '.join(sorted(allowed))}."""

    # HIGH-08: Magic-byte validation before any disk write
    file_storage.stream.seek(0)
    file_bytes = file_storage.stream.read(32)
    file_storage.stream.seek(0)
    ok, magic_err = validate_magic_bytes(file_bytes, ext)
    if not ok:
        return None, f"File content validation failed: {magic_err}"


    unique_name = f"{uuid.uuid4().hex}.{ext}"

    try:
        upload_folder = current_app.config.get("UPLOAD_FOLDER", "static/uploads")
        dest_dir = os.path.join(upload_folder, subfolder)
        os.makedirs(dest_dir, exist_ok=True)
        dest_path = os.path.join(dest_dir, unique_name)

        if max_size:
            try:
                from PIL import Image as PILImage
                img = PILImage.open(file_storage.stream)
                img.thumbnail(max_size, PILImage.LANCZOS)
                img.save(dest_path, quality=quality)
            except ImportError:
                # Pillow not installed — save as-is
                file_storage.stream.seek(0)
                file_storage.save(dest_path)
        else:
            file_storage.save(dest_path)

        return unique_name, None

    except Exception as exc:
        logger.exception("save_image failed: %s", exc)
        return None, "Failed to save image. Please try again."


def delete_image(filename: str | None, subfolder: str) -> None:
    """
    Delete an image file from disk. Silently ignores missing files.
    """
    if not filename:
        return
    try:
        upload_folder = current_app.config.get("UPLOAD_FOLDER", "static/uploads")
        path = os.path.join(upload_folder, subfolder, filename)
        if os.path.isfile(path):
            os.remove(path)
    except Exception:
        logger.exception("delete_image failed: subfolder=%s filename=%s", subfolder, filename)


# ─────────────────────────────────────────────────────────────────────────────
# PROFILE COMPLETION
# ─────────────────────────────────────────────────────────────────────────────

def get_profile_completion(profile) -> int:
    """
    Return an integer 0–100 representing how complete the profile is.

    Each filled field contributes equally to the total.
    """
    if profile is None:
        return 0

    fields = [
        profile.name,
        profile.title,
        profile.bio,
        profile.email,
        profile.location,
        profile.profile_image,
        getattr(profile, "phone", None),
        getattr(profile, "resume_url", None),
        getattr(profile, "subtitle", None),
        getattr(profile, "years_experience", None),
    ]

    filled = sum(1 for f in fields if f and str(f).strip())
    return int(filled / len(fields) * 100)


# ─────────────────────────────────────────────────────────────────────────────
# PAYMONGO TOGGLE  (persisted in PlatformSetting)
# ─────────────────────────────────────────────────────────────────────────────

_PAYMONGO_KEY = "paymongo_enabled"


def is_paymongo_enabled() -> bool:
    """Return True if the superadmin has enabled PayMongo checkout."""
    try:
        from app.models.portfolio import PlatformSetting
        return PlatformSetting.get_bool(_PAYMONGO_KEY, default=False) or False
    except Exception:
        logger.exception("is_paymongo_enabled check failed")
        return False


def set_paymongo_enabled(value: bool) -> None:
    """Persist the PayMongo enabled toggle to PlatformSetting."""
    try:
        from app import db
        from app.models.portfolio import PlatformSetting
        PlatformSetting.set_bool(_PAYMONGO_KEY, value)
        db.session.commit()
    except Exception:
        logger.exception("set_paymongo_enabled failed")
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# EMAIL
# ─────────────────────────────────────────────────────────────────────────────

def send_inquiry_email(inquiry, comm_settings=None) -> None:
    """
    Send a contact-form notification email via MailerSend.

    v5.0: Flask-Mail / SMTP removed. All notification emails now route
    through MailerSend. The comm_settings parameter is retained for
    signature compatibility but SMTP fields are no longer used.

    The destination is resolved from:
      1. comm_settings.admin_email  (per-tenant superadmin configuration)
      2. ADMIN_EMAIL config value   (global fallback)
    Silently skips if no destination is configured.

    Args:
        inquiry:       Inquiry model instance
        comm_settings: TenantCommunicationSettings | None  (admin_email read if set)
    """
    try:
        from app.services.mailersend_service import send_system_notification

        # Resolve notification destination — per-tenant admin_email first.
        admin_email = ''
        if comm_settings:
            admin_email = (comm_settings.admin_email or '').strip()
        if not admin_email and getattr(inquiry, 'tenant_slug', None) in (None, '', 'default'):
            admin_email = (current_app.config.get('ADMIN_EMAIL') or '').strip()
        if not admin_email:
            logger.debug("send_inquiry_email: no admin_email configured, skipping.")
            return

        subject = f"New inquiry from {inquiry.name}: {getattr(inquiry, 'subject', 'Contact Form')}"
        body = (
            f"Name:    {inquiry.name}\n"
            f"Email:   {inquiry.email}\n"
            f"Subject: {getattr(inquiry, 'subject', '')}\n\n"
            f"{inquiry.message}\n"
        )

        send_system_notification(admin_email, subject, body)
        logger.info("send_inquiry_email: dispatched via MailerSend to %s", admin_email)

    except Exception:
        logger.exception("send_inquiry_email failed — inquiry still saved to DB")


# ─────────────────────────────────────────────────────────────────────────────
# CONTACT FORM VALIDATION
# ─────────────────────────────────────────────────────────────────────────────

def validate_contact_payload() -> tuple[list[str], str, str, str, str]:
    """
    Parse and validate the contact form POST payload.

    Returns:
        (errors, name, email, subject, message)

    If errors is non-empty the other values may be empty strings.
    """
    data = request.get_json(silent=True) or request.form

    import re as _re

    def _strip_tags(s: str) -> str:
        return _re.sub(r'<[^>]*?>', '', s or '').strip()

    name    = _strip_tags(data.get("name") or "")
    email   = (data.get("email") or "").strip()
    subject = _strip_tags(data.get("subject") or "")
    message = _strip_tags(data.get("message") or "")

    errors: list[str] = []

    if not name:
        errors.append("Name is required.")
    elif len(name) > 120:
        errors.append("Name must be 120 characters or fewer.")

    if not email:
        errors.append("Email address is required.")
    elif len(email) > 200 or not _re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]{2,}$', email):
        errors.append("Please enter a valid email address.")

    if not subject:
        errors.append("Subject is required.")
    elif len(subject) > 200:
        errors.append("Subject must be 200 characters or fewer.")

    if not message:
        errors.append("Message is required.")
    elif len(message) > 2000:
        errors.append("Message must be 2,000 characters or fewer.")

    return errors, name, email, subject, message

# ─────────────────────────────────────────────────────────────────────────────
# MISSING EXPORTS — v3.6.0 fixes
# ─────────────────────────────────────────────────────────────────────────────

def generate_license_key(plan: str, tenant_slug: str) -> str:
    """
    Generate a human-readable subscription reference string.

    NOTE: The old license-key system is deprecated.  This function exists
    solely for backward-compatibility with manual_billing.py references.
    New code should use Subscription.reference_id directly.

    Format: <PLAN>-<SLUG[:8].upper()>-<8 random hex chars>
    Example: BASIC-MYPORT-3f9a2c1e
    """
    import secrets as _secrets
    plan_prefix = (plan or 'BASIC').upper()[:8]
    slug_part   = (tenant_slug or 'default').upper()[:8]
    random_part = _secrets.token_hex(4)
    return f"{plan_prefix}-{slug_part}-{random_part}"


def send_subscription_activated_notification(profile, subscription) -> None:
    """
    Send an email notification when a subscription is activated via MailerSend.

    v5.0: Flask-Mail removed — emails now route through MailerSend only.
    Silently skips if no recipient email is available.
    """
    try:
        from app.services.mailersend_service import send_subscription_email

        tenant_email = getattr(profile, "email", None)
        if not tenant_email:
            logger.debug("send_subscription_activated_notification: no tenant email, skipping.")
            return

        plan        = getattr(subscription, "plan", "Unknown")
        tenant_slug = getattr(profile, "tenant_slug", "unknown")
        expires_at  = getattr(subscription, "expires_at", None)
        expires_str = expires_at.strftime('%Y-%m-%d') if expires_at else None

        send_subscription_email(
            recipient_email=tenant_email,
            tenant_name=tenant_slug,
            event='activated',
            plan=plan,
            expires_on=expires_str,
        )
        logger.info("Subscription activated notification sent to %s", tenant_email)
    except Exception:
        logger.exception("send_subscription_activated_notification failed — subscription still active")


def refresh_current_subscription() -> None:
    """
    before_request hook: auto-expire subscriptions that have passed their
    expires_at without a renewal.  Only runs for authenticated non-superadmin
    users on non-static routes.

    Safe to call on every request — does a single cheap DB read and only
    commits if the status actually changes.
    """
    from flask import request as _req, g as _g
    from flask_login import current_user as _cu

    # Skip static files, health checks, and webhooks
    endpoint = _req.endpoint or ''
    if endpoint.startswith('static') or endpoint in ('heartbeat.ping', 'webhooks.paymongo_webhook'):
        return

    if not _cu.is_authenticated or _cu.is_superadmin:
        return

    try:
        from app.models.portfolio import Profile, Subscription
        from app import db as _db
        from datetime import datetime, timezone

        tenant_slug = getattr(_cu, 'tenant_slug', None) or 'default'
        profile = Profile.query.filter_by(tenant_slug=tenant_slug).first()
        if profile is None:
            return

        sub = profile.current_subscription()
        if sub is None:
            return

        now = datetime.now(timezone.utc)
        expires = sub.expires_at
        if expires and expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)

        if sub.status == 'active' and expires and expires < now:
            sub.status = 'expired'
            sub.is_active = False
            if profile.tenant:
                profile.tenant.status = 'suspended'
            # Bust in-process cache
            if hasattr(profile, '_current_subscription_cache'):
                del profile._current_subscription_cache
            _db.session.commit()
            logger.info(
                "Auto-expired subscription %s for tenant %s (expired %s)",
                sub.id, tenant_slug, expires.isoformat(),
            )
    except Exception:
        logger.exception("refresh_current_subscription hook failed")
        try:
            from app import db as _db
            _db.session.rollback()
        except Exception:
            pass
