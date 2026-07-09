"""SuperAdmin-editable plan limits for Trial, Basic, Pro, and Enterprise.

This module intentionally stores limits in PlatformSetting JSON instead of a
new migration/table. That keeps production rollout safer while allowing the
SuperAdmin dashboard to tune feature gates used throughout the Admin area.

Backward-compatible Trial helpers are kept because signup code already imports
``get_trial_duration_days`` and older templates may still reference Trial names.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

PLAN_LIMITS_KEY = "plan_limits_v2"
TRIAL_LIMITS_KEY = "trial_plan_limits_v1"  # legacy import/migration bridge

EDITABLE_PLAN_KEYS = ("Trial", "Basic", "Pro", "Enterprise")
PLAN_SLUGS = {"Trial": "trial", "Basic": "basic", "Pro": "pro", "Enterprise": "enterprise"}
SLUG_TO_PLAN = {v: k for k, v in PLAN_SLUGS.items()}
PLAN_ALIASES = {
    "trial": "Trial",
    "free_trial": "Trial",
    "basic": "Basic",
    "starter": "Basic",
    "pro": "Pro",
    "professional": "Pro",
    "business": "Enterprise",
    "enterprise": "Enterprise",
    "ent": "Enterprise",
}

UNLIMITED_SENTINELS = {"", "none", "null", "unlimited", "∞", "-1"}

NUMERIC_FIELDS = {
    "trial_duration_days": (1, 365),
    "max_projects": (0, 1_000_000),
    "max_skills": (0, 1_000_000),
    "max_media_uploads": (0, 1_000_000),
    "max_testimonials": (0, 1_000_000),
    "max_certificates": (0, 1_000_000),
    "max_services": (0, 1_000_000),
    "max_experiences": (0, 1_000_000),
    "storage_limit_mb": (1, 10_000_000),
    "max_upload_size_mb": (1, 100_000),
    "daily_email_limit": (0, 10_000_000),
    "max_team_members": (1, 1_000_000),
}

BOOL_FIELDS = {
    "projects",
    "skills",
    "uploads",
    "testimonials",
    "certificates",
    "badges",
    "services",
    "experiences",
    "custom_domain",
    "email_services",
    "custom_smtp",
    "resend",
    "mailersend",
    "theme_customization",
    "premium_themes",
    "analytics",
    "white_label",
    "team_members",
    "api_access",
    "ai_features",
    "branding_removal",
}

DEFAULT_PLAN_LIMITS: dict[str, dict[str, Any]] = {
    "Trial": {
        "trial_duration_days": 7,
        "max_projects": 5,
        "max_skills": 20,
        "max_media_uploads": 10,
        "max_testimonials": 3,
        "max_certificates": 3,
        "max_services": 3,
        "max_experiences": 3,
        "storage_limit_mb": 10,
        "max_upload_size_mb": 2,
        "daily_email_limit": 50,
        "max_team_members": 1,
        "projects": True,
        "skills": True,
        "uploads": True,
        "testimonials": True,
        "certificates": True,
        "badges": True,
        "services": True,
        "experiences": True,
        "custom_domain": False,
        "email_services": False,
        "custom_smtp": False,
        "resend": False,
        "mailersend": False,
        "theme_customization": False,
        "premium_themes": False,
        "analytics": False,
        "white_label": False,
        "team_members": False,
        "api_access": False,
        "ai_features": False,
        "branding_removal": False,
    },
    "Basic": {
        "trial_duration_days": 7,
        "max_projects": 25,
        "max_skills": 50,
        "max_media_uploads": 100,
        "max_testimonials": 10,
        "max_certificates": 10,
        "max_services": 10,
        "max_experiences": 10,
        "storage_limit_mb": 100,
        "max_upload_size_mb": 5,
        "daily_email_limit": 500,
        "max_team_members": 2,
        "projects": True,
        "skills": True,
        "uploads": True,
        "testimonials": True,
        "certificates": True,
        "badges": True,
        "services": True,
        "experiences": True,
        "custom_domain": True,
        "email_services": True,
        "custom_smtp": True,
        "resend": False,
        "mailersend": False,
        "theme_customization": False,
        "premium_themes": False,
        "analytics": True,
        "white_label": False,
        "team_members": True,
        "api_access": False,
        "ai_features": False,
        "branding_removal": False,
    },
    "Pro": {
        "trial_duration_days": 7,
        "max_projects": None,
        "max_skills": None,
        "max_media_uploads": None,
        "max_testimonials": None,
        "max_certificates": None,
        "max_services": None,
        "max_experiences": None,
        "storage_limit_mb": 1024,
        "max_upload_size_mb": 25,
        "daily_email_limit": 5_000,
        "max_team_members": 10,
        "projects": True,
        "skills": True,
        "uploads": True,
        "testimonials": True,
        "certificates": True,
        "badges": True,
        "services": True,
        "experiences": True,
        "custom_domain": True,
        "email_services": True,
        "custom_smtp": True,
        "resend": True,
        "mailersend": True,
        "theme_customization": True,
        "premium_themes": True,
        "analytics": True,
        "white_label": False,
        "team_members": True,
        "api_access": False,
        "ai_features": True,
        "branding_removal": True,
    },
    "Enterprise": {
        "trial_duration_days": 7,
        "max_projects": None,
        "max_skills": None,
        "max_media_uploads": None,
        "max_testimonials": None,
        "max_certificates": None,
        "max_services": None,
        "max_experiences": None,
        "storage_limit_mb": 5120,
        "max_upload_size_mb": 100,
        "daily_email_limit": 50_000,
        "max_team_members": None,
        "projects": True,
        "skills": True,
        "uploads": True,
        "testimonials": True,
        "certificates": True,
        "badges": True,
        "services": True,
        "experiences": True,
        "custom_domain": True,
        "email_services": True,
        "custom_smtp": True,
        "resend": True,
        "mailersend": True,
        "theme_customization": True,
        "premium_themes": True,
        "analytics": True,
        "white_label": True,
        "team_members": True,
        "api_access": True,
        "ai_features": True,
        "branding_removal": True,
    },
}

# Backward-compatible public name used by older code/tests.
DEFAULT_TRIAL_LIMITS: dict[str, Any] = DEFAULT_PLAN_LIMITS["Trial"]

PLAN_LIMIT_SECTIONS = [
    ("Trial Window", ["trial_duration_days"]),
    ("Content Limits", ["max_projects", "max_skills", "max_media_uploads", "max_testimonials", "max_certificates", "max_services", "max_experiences"]),
    ("Storage & Delivery", ["storage_limit_mb", "max_upload_size_mb", "daily_email_limit", "max_team_members"]),
    ("Admin Dashboard Feature Access", [
        "projects", "skills", "uploads", "testimonials", "certificates", "badges", "services",
        "experiences", "custom_domain", "email_services", "custom_smtp", "resend", "mailersend",
        "theme_customization", "premium_themes", "analytics", "white_label", "team_members",
        "api_access", "ai_features", "branding_removal",
    ]),
]

# Trial editor used this legacy name; keep it as alias.
TRIAL_LIMIT_SECTIONS = PLAN_LIMIT_SECTIONS

FIELD_LABELS = {
    "trial_duration_days": "Trial duration (days)",
    "max_projects": "Project limit",
    "max_skills": "Skill limit",
    "max_media_uploads": "Media upload slots",
    "max_testimonials": "Testimonial limit",
    "max_certificates": "Certificate/badge item limit",
    "max_services": "Service item limit",
    "max_experiences": "Experience timeline item limit",
    "storage_limit_mb": "Total storage limit (MB)",
    "max_upload_size_mb": "Max file size (MB)",
    "daily_email_limit": "Daily email limit",
    "max_team_members": "Team member limit",
    "projects": "Projects page",
    "skills": "Skills page",
    "uploads": "Uploads/media manager",
    "testimonials": "Testimonials page",
    "certificates": "Certificates & badges page",
    "badges": "Badges support",
    "services": "Services page",
    "experiences": "Work experience timeline page",
    "custom_domain": "Custom domains",
    "email_services": "Email services page",
    "custom_smtp": "Custom SMTP",
    "resend": "Resend provider",
    "mailersend": "MailerSend provider",
    "theme_customization": "Theme customization",
    "premium_themes": "Premium themes",
    "analytics": "Analytics",
    "white_label": "White label / remove branding",
    "team_members": "Team members",
    "api_access": "API access",
    "ai_features": "AI features",
    "branding_removal": "Branding removal",
}

FIELD_HELP = {
    "trial_duration_days": "Only affects new Trial signups. Paid plans ignore this value.",
    "max_media_uploads": "Counts profile, project, testimonial, certificate and badge images where the existing routes enforce upload slots. Leave blank/unlimited for paid plans if desired.",
    "email_services": "Master switch for the tenant Email Services dashboard.",
    "custom_smtp": "Provider-level switch; still requires Email Services to be enabled.",
    "resend": "Provider-level switch; still requires Email Services to be enabled.",
    "mailersend": "Provider-level switch; still requires Email Services to be enabled.",
    "premium_themes": "Controls premium theme access in addition to theme plan requirements.",
    "storage_limit_mb": "Set storage quota in MB. Paid plans can still be capped here.",
}

UNLIMITED_FIELDS = {
    "max_projects",
    "max_skills",
    "max_media_uploads",
    "max_testimonials",
    "max_certificates",
    "max_services",
    "max_experiences",
    "max_team_members",
}


def _platform_setting_model():
    from app.models.core import PlatformSetting
    return PlatformSetting


def normalize_editable_plan(plan: str | None) -> str:
    key = (plan or "").strip().lower()
    return PLAN_ALIASES.get(key, "Trial" if not key else (plan or "Trial").strip().title())


def plan_slug(plan: str | None) -> str:
    return PLAN_SLUGS.get(normalize_editable_plan(plan), (plan or "trial").strip().lower())


def _clean_int(value: Any, field: str, *, allow_unlimited: bool = False) -> int | None:
    if isinstance(value, str) and value.strip().lower() in UNLIMITED_SENTINELS:
        return None if allow_unlimited else DEFAULT_PLAN_LIMITS["Trial"].get(field)
    if value is None:
        return None if allow_unlimited else DEFAULT_PLAN_LIMITS["Trial"].get(field)
    try:
        ivalue = int(float(value))
    except (TypeError, ValueError):
        return None if allow_unlimited else DEFAULT_PLAN_LIMITS["Trial"].get(field)
    low, high = NUMERIC_FIELDS[field]
    return max(low, min(high, ivalue))


def _load_stored_plan_limits() -> dict[str, dict[str, Any]]:
    data: dict[str, dict[str, Any]] = {}
    try:
        PlatformSetting = _platform_setting_model()
        loaded = PlatformSetting.get_json(PLAN_LIMITS_KEY, default={}) or {}
        if isinstance(loaded, dict):
            for raw_plan, raw_limits in loaded.items():
                plan = normalize_editable_plan(raw_plan)
                if plan in EDITABLE_PLAN_KEYS and isinstance(raw_limits, dict):
                    data[plan] = dict(raw_limits)

        # Bridge the old Trial-only setting into the new all-plan object so
        # existing production edits are not lost after this patch.
        if "Trial" not in data:
            old_trial = PlatformSetting.get_json(TRIAL_LIMITS_KEY, default={}) or {}
            if isinstance(old_trial, dict) and old_trial:
                data["Trial"] = dict(old_trial)
    except Exception:
        data = {}
    return data


def _merge_plan_limits(plan: str, stored: dict[str, Any] | None = None) -> dict[str, Any]:
    plan = normalize_editable_plan(plan)
    defaults = deepcopy(DEFAULT_PLAN_LIMITS.get(plan, DEFAULT_PLAN_LIMITS["Trial"]))
    stored = stored or {}
    defaults.update({k: v for k, v in stored.items() if k in defaults})

    allow_unlimited = plan in {"Basic", "Pro", "Enterprise"}
    for field in NUMERIC_FIELDS:
        field_allows_unlimited = allow_unlimited and field in UNLIMITED_FIELDS
        defaults[field] = _clean_int(defaults.get(field), field, allow_unlimited=field_allows_unlimited)
    for field in BOOL_FIELDS:
        defaults[field] = bool(defaults.get(field))
    return defaults


def get_plan_limits(plan: str) -> dict[str, Any]:
    plan = normalize_editable_plan(plan)
    stored = _load_stored_plan_limits().get(plan, {})
    return _merge_plan_limits(plan, stored)


def get_all_plan_limits() -> dict[str, dict[str, Any]]:
    stored = _load_stored_plan_limits()
    return {plan: _merge_plan_limits(plan, stored.get(plan, {})) for plan in EDITABLE_PLAN_KEYS}


def save_plan_limits(plan: str, form) -> dict[str, Any]:
    plan = normalize_editable_plan(plan)
    if plan not in EDITABLE_PLAN_KEYS:
        raise ValueError(f"Unsupported editable plan: {plan}")

    prefix = plan_slug(plan)
    allow_unlimited = plan in {"Basic", "Pro", "Enterprise"}
    limits: dict[str, Any] = {}
    for field in NUMERIC_FIELDS:
        field_allows_unlimited = allow_unlimited and field in UNLIMITED_FIELDS
        limits[field] = _clean_int(form.get(f"{prefix}_{field}"), field, allow_unlimited=field_allows_unlimited)
    for field in BOOL_FIELDS:
        limits[field] = form.get(f"{prefix}_{field}") in ("1", "true", "on", "yes")

    all_limits = get_all_plan_limits()
    all_limits[plan] = limits
    PlatformSetting = _platform_setting_model()
    PlatformSetting.set_json(PLAN_LIMITS_KEY, all_limits)
    if plan == "Trial":
        PlatformSetting.set_json(TRIAL_LIMITS_KEY, limits)
    return limits


def reset_plan_limits(plan: str | None = None) -> None:
    PlatformSetting = _platform_setting_model()
    if plan:
        plan = normalize_editable_plan(plan)
        all_limits = get_all_plan_limits()
        all_limits[plan] = deepcopy(DEFAULT_PLAN_LIMITS[plan])
        PlatformSetting.set_json(PLAN_LIMITS_KEY, all_limits)
        if plan == "Trial":
            PlatformSetting.set_json(TRIAL_LIMITS_KEY, deepcopy(DEFAULT_PLAN_LIMITS["Trial"]))
        return
    PlatformSetting.set_json(PLAN_LIMITS_KEY, deepcopy(DEFAULT_PLAN_LIMITS))
    PlatformSetting.set_json(TRIAL_LIMITS_KEY, deepcopy(DEFAULT_PLAN_LIMITS["Trial"]))


# Backward-compatible Trial helpers -------------------------------------------------
def get_trial_limits() -> dict[str, Any]:
    return get_plan_limits("Trial")


def save_trial_limits(form) -> dict[str, Any]:
    return save_plan_limits("Trial", form)


def reset_trial_limits() -> None:
    reset_plan_limits("Trial")


def get_trial_duration_days() -> int:
    return int(get_trial_limits().get("trial_duration_days") or DEFAULT_PLAN_LIMITS["Trial"]["trial_duration_days"])


def _feature_payload_from_limits(limits: dict[str, Any]) -> dict[str, Any]:
    return {
        "max_projects": limits["max_projects"] if limits.get("projects") else 0,
        "max_skills": limits["max_skills"] if limits.get("skills") else 0,
        "max_media_uploads": limits["max_media_uploads"] if limits.get("uploads") else 0,
        "max_testimonials": limits["max_testimonials"] if limits.get("testimonials") else 0,
        "max_certificates": limits["max_certificates"] if (limits.get("certificates") or limits.get("badges")) else 0,
        "max_services": limits["max_services"] if limits.get("services") else 0,
        "max_experiences": limits["max_experiences"] if limits.get("experiences") else 0,
        "storage_limit_mb": limits["storage_limit_mb"] if limits.get("uploads") else 0,
        "max_upload_size_mb": limits["max_upload_size_mb"],
        "daily_email_limit": limits["daily_email_limit"],
        "max_team_members": limits["max_team_members"],
        "projects": limits["projects"],
        "skills": limits["skills"],
        "uploads": limits["uploads"],
        "testimonials": limits["testimonials"],
        "certificates": limits["certificates"],
        "badges": limits["badges"],
        "services": limits["services"],
        "experiences": limits["experiences"],
        "custom_domain": limits["custom_domain"],
        "email_services": limits["email_services"],
        "custom_smtp": bool(limits.get("email_services") and limits.get("custom_smtp")),
        "resend": bool(limits.get("email_services") and limits.get("resend")),
        "mailersend": bool(limits.get("email_services") and limits.get("mailersend")),
        "theme_customization": limits["theme_customization"],
        "premium_themes": limits["premium_themes"],
        "analytics": limits["analytics"],
        "white_label": limits["white_label"],
        "team_members": limits["team_members"],
        "api_access": limits["api_access"],
        "ai_features": limits["ai_features"],
        "branding_removal": limits["branding_removal"],
    }


def apply_plan_feature_overrides(plan: str, features: dict[str, Any]) -> dict[str, Any]:
    plan = normalize_editable_plan(plan)
    if plan not in EDITABLE_PLAN_KEYS:
        return dict(features or {})
    out = dict(features or {})
    out.update(_feature_payload_from_limits(get_plan_limits(plan)))
    return out


def apply_trial_feature_overrides(features: dict[str, Any]) -> dict[str, Any]:
    return apply_plan_feature_overrides("Trial", features)


def plan_field_metadata() -> dict[str, Any]:
    return {
        "plans": list(EDITABLE_PLAN_KEYS),
        "slugs": PLAN_SLUGS,
        "sections": PLAN_LIMIT_SECTIONS,
        "numeric_fields": sorted(NUMERIC_FIELDS),
        "bool_fields": sorted(BOOL_FIELDS),
        "labels": FIELD_LABELS,
        "help": FIELD_HELP,
        "defaults": deepcopy(DEFAULT_PLAN_LIMITS),
        "unlimited_fields": sorted(UNLIMITED_FIELDS),
    }


def trial_field_metadata() -> dict[str, Any]:
    meta = plan_field_metadata()
    meta["plans"] = ["Trial"]
    return meta
