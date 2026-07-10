from __future__ import annotations

from app.security import FileUploadPolicy
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

from app.system_plan import (
    ADMINISTRATOR_PLAN,
    ADMINISTRATOR_PLAN_NAME,
    is_administrator_plan,
    public_billing_plans,
    system_billing_plans,
)

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
        "price_monthly":   1.00,
        "price_yearly":    round(1.00  * 12 * YEARLY_DISCOUNT, 2),
        "duration_days":   30,
        "price":           1.00,          # legacy compat key
        "price_label":     "₱1.00/mo",
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
    "trial":        "Trial",
    "free_trial":   "Trial",
    "basic":        "Basic",
    "starter":      "Basic",
    "pro":          "Pro",
    "professional": "Pro",
    "business":     "Enterprise",
    "enterprise":   "Enterprise",
    "ent":          "Enterprise",
    "administrator": "Administrator",
    "admin":        "Administrator",
    "system":       "Administrator",
}


def normalize_plan_name(plan: str) -> str:
    """Return the canonical billing plan key.

    Administrator is intentionally supported as an internal-only plan, but it
    is not included in BILLING_PLANS so it cannot appear in tenant checkout.
    """
    if not plan:
        return "Basic"
    key = plan.strip().lower()
    return _PLAN_ALIASES.get(key, plan.strip().title())




def get_plan_display_name(plan: str) -> str:
    """Human-facing plan label after alias normalization."""
    return normalize_plan_name(plan)


def is_trial_plan(plan: str) -> bool:
    return normalize_plan_name(plan) == "Trial"


def is_basic_plan(plan: str) -> bool:
    return normalize_plan_name(plan) == "Basic"


def is_pro_plan(plan: str) -> bool:
    return normalize_plan_name(plan) == "Pro"


def is_enterprise_plan(plan: str) -> bool:
    return normalize_plan_name(plan) == "Enterprise"


def is_paid_plan(plan: str) -> bool:
    return normalize_plan_name(plan) in {"Basic", "Pro", "Enterprise"}


def is_public_plan(plan: str) -> bool:
    return normalize_plan_name(plan) in {"Trial", "Basic", "Pro", "Enterprise"}


def is_checkout_plan(plan: str) -> bool:
    return normalize_plan_name(plan) in {"Basic", "Pro", "Enterprise"}


def plan_allows_feature(plan: str, feature: str) -> bool:
    if is_administrator_plan(plan):
        return True
    try:
        from app.services.billing.plan_capabilities import get_capabilities
        cap = get_capabilities(normalize_plan_name(plan))
        value = getattr(cap, feature, None)
        return bool(value)
    except Exception:
        return False


def get_plan_limit(plan: str, limit_name: str):
    if is_administrator_plan(plan):
        return None
    try:
        from app.services.billing.plan_capabilities import get_capabilities
        cap = get_capabilities(normalize_plan_name(plan))
        return getattr(cap, limit_name, None)
    except Exception:
        return None


def get_plan_price(plan: str, billing_cycle: str = "monthly") -> float:
    """
    Return the total amount charged for one billing period.

    monthly → price_monthly  (e.g. 49.00)
    yearly  → price_yearly   (e.g. 487.32, already discounted)
    """
    norm = normalize_plan_name(plan)
    if is_administrator_plan(norm):
        return 0.0
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
    if is_administrator_plan(norm):
        return ADMINISTRATOR_PLAN["price_label"]
    data = BILLING_PLANS.get(norm, BILLING_PLANS["Basic"])
    symbol = data.get("currency_symbol", "₱")
    price = get_plan_price(plan, billing_cycle)
    if billing_cycle == "yearly":
        pct = get_yearly_discount_percent(plan)
        pct_str = f"{pct:g}"
        return f"{symbol}{price:,.2f}/yr (Save ~{pct_str}%)"
    return f"{symbol}{price:,.2f}/mo"


# ─────────────────────────────────────────────────────────────────────────────
# YEARLY BILLING DISCOUNT  (superadmin-editable, persisted in PlatformSetting)
#
# YEARLY_DISCOUNT above is the hardcoded fallback (~17% off) used only when
# no superadmin override has ever been saved. Once the superadmin edits the
# rate on the Discounts & Promotions page, it's persisted here — either as
# one platform-wide rate, or as a per-plan override that beats the global
# rate for that one plan.
# ─────────────────────────────────────────────────────────────────────────────

_YEARLY_DISCOUNT_GLOBAL_KEY = "yearly_discount_global"
_YEARLY_DISCOUNT_PLAN_KEY_PREFIX = "yearly_discount_plan_"


def _yearly_discount_plan_key(plan: str) -> str:
    return _YEARLY_DISCOUNT_PLAN_KEY_PREFIX + normalize_plan_name(plan).lower()


def get_yearly_discount(plan: str | None = None) -> float:
    """
    Return the yearly-billing discount as a decimal multiplier applied to
    (monthly_price * 12) — e.g. 0.83 means "17% off". Matches the semantics
    of the original hardcoded YEARLY_DISCOUNT constant.

    Resolution order:
      1. Per-plan override (if `plan` is given and one has been saved)
      2. Platform-wide override
      3. Hardcoded YEARLY_DISCOUNT default
    """
    try:
        from app.models.portfolio import PlatformSetting

        if plan:
            plan_pct = PlatformSetting.get_float(_yearly_discount_plan_key(plan))
            if plan_pct is not None:
                return max(0.0, min(1.0, 1 - (plan_pct / 100)))

        global_pct = PlatformSetting.get_float(_YEARLY_DISCOUNT_GLOBAL_KEY)
        if global_pct is not None:
            return max(0.0, min(1.0, 1 - (global_pct / 100)))
    except Exception:
        logger.exception("get_yearly_discount lookup failed — using hardcoded default")

    return YEARLY_DISCOUNT


def get_yearly_discount_percent(plan: str | None = None) -> float:
    """Human-facing 'percent off' version of get_yearly_discount(), e.g. 17 or 17.5."""
    pct = round((1 - get_yearly_discount(plan)) * 100, 2)
    return int(pct) if pct == int(pct) else pct


def get_yearly_discount_percent_override(plan: str) -> float | None:
    """
    Return the raw per-plan override percent if one has been explicitly
    saved for this plan, or None if the plan simply follows the global rate.
    Used by the superadmin UI to distinguish "20% override" from "no
    override, currently showing 17% because that's the global rate".
    """
    try:
        from app.models.portfolio import PlatformSetting
        return PlatformSetting.get_float(_yearly_discount_plan_key(plan))
    except Exception:
        logger.exception("get_yearly_discount_percent_override lookup failed")
        return None


def set_yearly_discount(percent_off: float, plan: str | None = None) -> None:
    """
    Persist the yearly-billing discount percentage.

    plan=None  → sets the platform-wide rate that applies to every plan
                 without its own override.
    plan='Pro' → sets a Pro-only override that beats the global rate.

    Immediately refreshes BILLING_PLANS so price_yearly is correct on the
    very next render — no app restart required.
    """
    try:
        from app import db
        from app.models.portfolio import PlatformSetting
        key = _yearly_discount_plan_key(plan) if plan else _YEARLY_DISCOUNT_GLOBAL_KEY
        PlatformSetting.set_float(key, percent_off)
        db.session.commit()
    except Exception:
        logger.exception("set_yearly_discount failed")
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass
        raise
    refresh_yearly_pricing()


def clear_yearly_discount_override(plan: str) -> None:
    """Remove a per-plan override so that plan goes back to following the
    global yearly discount rate."""
    try:
        from app import db
        from app.models.portfolio import PlatformSetting
        row = db.session.get(PlatformSetting, _yearly_discount_plan_key(plan))
        if row is not None:
            db.session.delete(row)
            db.session.commit()
    except Exception:
        logger.exception("clear_yearly_discount_override failed")
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass
        raise
    refresh_yearly_pricing()


def get_public_billing_plans() -> dict[str, dict]:
    """Tenant-visible/purchasable billing plans only."""
    return public_billing_plans(BILLING_PLANS)


def get_system_billing_plans() -> dict[str, dict]:
    """Internal system plans shown only in protected superadmin sections."""
    return system_billing_plans()


def refresh_yearly_pricing() -> None:
    """
    Recompute price_yearly for every plan in BILLING_PLANS from its current
    monthly price and the (possibly superadmin-edited) yearly discount rate.

    Cheap — safe to call at the top of any request path that renders plan
    prices, so a superadmin edit takes effect immediately across all workers
    on their next request without an app restart.
    """
    for key, data in BILLING_PLANS.items():
        monthly = data.get("price_monthly", data.get("price", 0))
        data["price_yearly"] = round(monthly * 12 * get_yearly_discount(key), 2)


def get_yearly_savings_label(plan: str) -> str:
    """Return e.g. 'Save ₱100.68/yr' for the savings badge."""
    norm = normalize_plan_name(plan)
    if is_administrator_plan(norm):
        return ADMINISTRATOR_PLAN["price_label"]
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

_ALLOWED_IMAGE_EXTENSIONS = set(FileUploadPolicy.ALLOWED_IMAGE_EXTENSIONS)


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


def _convert_uploads_to_webp_enabled() -> bool:
    """Feature flag for lightweight WebP storage of uploaded portfolio images."""
    try:
        return bool(current_app.config.get("CONVERT_UPLOADS_TO_WEBP", True))
    except RuntimeError:
        return True


def save_image(
    file_storage,
    subfolder: str,
    max_size: tuple[int, int] | None = None,
    allowed_extensions: set[str] | None = None,
    quality: int = 85,
) -> tuple[str | None, str | None]:
    """
    Validate, optimise and save an uploaded portfolio image.

    New uploads are converted to WebP by default so profile photos, project
    screenshots, testimonial avatars, certificates and badges load much faster.
    Animated GIF/WebP uploads are preserved to avoid losing animation.

    Config knobs:
        CONVERT_UPLOADS_TO_WEBP=True       # default enabled
        UPLOAD_WEBP_QUALITY=82             # default WebP quality
        UPLOAD_IMAGE_MAX_DIMENSION=2048    # cap huge photos when max_size absent

    Returns:
        (filename, None)  on success
        (None, error_msg) on failure

    Important contract: callers must unpack this tuple. Image fields should
    only ever receive the returned filename string, never the tuple itself.
    """
    if not is_upload_file(file_storage):
        return None, "No file provided."

    # Production-safe object storage path. When enabled, store the public URL in
    # the model instead of a local filename so uploaded photos survive redeploys
    # on ephemeral filesystems. Supabase helper performs the same validation and
    # WebP optimization before upload.
    try:
        if bool(current_app.config.get("USE_SUPABASE_STORAGE", False)):
            from app.utils.supabase_storage import save_image as _save_supabase_image

            remote_url = _save_supabase_image(file_storage, subfolder)
            if remote_url:
                return remote_url, None
            return None, "Failed to upload image to Supabase Storage. Please check storage credentials and bucket permissions."
    except Exception:
        logger.exception("save_image: Supabase storage upload failed")
        return None, "Failed to upload image to persistent storage. Please check storage settings."

    allowed = {ext.lower().lstrip('.') for ext in (allowed_extensions or _ALLOWED_IMAGE_EXTENSIONS)}
    original_name = secure_filename(file_storage.filename or "")
    ext = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else ""

    if not original_name or not ext:
        return None, "Please upload an image file with a valid extension."
    if ext not in allowed:
        return None, f"File type '.{ext}' is not allowed. Accepted: {', '.join(sorted(allowed))}."

    # Centralized validation before any disk write. Read the stream once,
    # validate extension/MIME/magic bytes/Pillow, then process in memory.
    try:
        file_storage.stream.seek(0)
        file_bytes = file_storage.stream.read()
        file_storage.stream.seek(0)
    except Exception:
        logger.exception("save_image: failed to inspect uploaded image stream")
        return None, "Could not read the uploaded image. Please try again."

    ok, validation_error = FileUploadPolicy.validate_image_upload(
        filename=original_name,
        file_size_bytes=len(file_bytes or b""),
        file_bytes=file_bytes,
        declared_mime=getattr(file_storage, "mimetype", None),
    )
    if not ok:
        try:
            file_storage.stream.seek(0)
        except Exception:
            pass
        return None, validation_error or "Uploaded image failed validation."

    try:
        from app.services.media.upload_storage import ensure_upload_folder, primary_upload_root

        upload_folder = str(primary_upload_root())
        dest_dir = str(ensure_upload_folder(subfolder))
        root_dir = os.path.abspath(upload_folder)
        if not (os.path.abspath(dest_dir) == root_dir or os.path.abspath(dest_dir).startswith(root_dir + os.sep)):
            logger.warning("save_image blocked unsafe upload subfolder=%s", subfolder)
            return None, "Invalid upload destination."

        convert_to_webp = _convert_uploads_to_webp_enabled()
        final_bytes = file_bytes
        final_ext = ext
        converted = False

        if convert_to_webp:
            try:
                from app.services.media.image_optimizer import optimize_image_bytes_to_webp

                webp_quality = int(current_app.config.get("UPLOAD_WEBP_QUALITY", quality or 82))
                max_dimension = int(current_app.config.get("UPLOAD_IMAGE_MAX_DIMENSION", 2048))
                optimized = optimize_image_bytes_to_webp(
                    file_bytes,
                    ext,
                    source_mime=getattr(file_storage, "mimetype", None),
                    max_size=max_size,
                    quality=webp_quality,
                    max_dimension=max_dimension,
                    preserve_animation=True,
                    force=True,
                )
                final_bytes = optimized.data
                final_ext = optimized.extension
                converted = optimized.converted
                if converted:
                    logger.info(
                        "save_image converted upload to WebP folder=%s original=%d final=%d saved=%.1f%%",
                        subfolder,
                        optimized.original_size,
                        optimized.final_size,
                        optimized.percent_saved,
                    )
            except Exception as exc:
                logger.exception("save_image: WebP optimization failed")
                return None, "Uploaded image could not be converted to WebP. Please try another image."

        elif max_size:
            # Legacy fallback if CONVERT_UPLOADS_TO_WEBP is disabled.
            try:
                from PIL import Image as PILImage
                import io
                with PILImage.open(io.BytesIO(file_bytes)) as img:
                    img.thumbnail(max_size, PILImage.LANCZOS)
                    out = io.BytesIO()
                    save_kwargs = {"quality": quality} if ext in {"jpg", "jpeg", "webp"} else {}
                    img.save(out, format=img.format, **save_kwargs)
                    final_bytes = out.getvalue()
            except ImportError:
                logger.warning("Pillow unavailable; saving original image bytes")

        unique_name = f"{uuid.uuid4().hex}.{final_ext}"
        dest_path = os.path.join(dest_dir, unique_name)
        with open(dest_path, "wb") as fh:
            fh.write(final_bytes)

        return unique_name, None

    except Exception as exc:
        logger.exception("save_image failed: %s", exc)
        return None, "Failed to save image. Please try again."
    finally:
        try:
            file_storage.stream.seek(0)
        except Exception:
            pass

def delete_image(filename: str | None, subfolder: str) -> None:
    """
    Delete an image from the active storage backend. Silently ignores missing
    local files. Remote Supabase URLs are deleted when Supabase storage is active.
    """
    if not filename:
        return
    try:
        if isinstance(filename, str) and filename.startswith(('http://', 'https://')):
            if bool(current_app.config.get("USE_SUPABASE_STORAGE", False)):
                try:
                    from app.utils.supabase_storage import delete_image as _delete_supabase_image
                    _delete_supabase_image(filename)
                except Exception:
                    logger.exception("delete_image failed for Supabase URL")
            return

        from app.services.media.upload_storage import delete_upload_file
        delete_upload_file(filename, subfolder)
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
        from app.models.core import Tenant
        from app import db as _db
        from app.utils.datetime_utils import ensure_utc_aware, utc_now
        from app.services.billing.billing import expire_trial_if_needed

        tenant_slug = getattr(_cu, 'tenant_slug', None) or 'default'
        profile = Profile.query.filter_by(tenant_slug=tenant_slug).first()
        if profile is None:
            return

        tenant = Tenant.query.get(profile.tenant_id)
        if tenant is not None:
            expire_trial_if_needed(tenant)
            if tenant.subscription_status == 'expired':
                _db.session.commit()
                logger.info("Auto-expired trial for tenant %s", tenant_slug)
                return

        sub = profile.current_subscription()
        if sub is None:
            return

        now = utc_now()
        expires = ensure_utc_aware(sub.expires_at)

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
