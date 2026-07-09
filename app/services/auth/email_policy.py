"""Central email reuse policy for authentication and tenant ownership.

Production rule:
- Every normal user email must be globally unique.
- The platform-owner email may appear only in two protected contexts:
  1) the SuperAdmin account
  2) the default portfolio / Administrator tenant admin account

Do not bypass this module from signup, OAuth, SuperAdmin tenant CRUD, seeds, or
password-reset lookup code.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from sqlalchemy import func

OWNER_SHARED_EMAIL = "delacruzedward735@gmail.com"
_RESERVED_OWNER_MESSAGE = "This email is reserved for the platform owner. Please use a different email."
_DUPLICATE_MESSAGE = "That email address is already in use."

_DEFAULT_TENANT_SLUGS = {"default", "administrator"}
_SUPERADMIN_ROLES = {"superadmin", "super_admin", "platform_owner", "owner"}
_DEFAULT_PORTFOLIO_ROLES = {
    "default", "administrator", "portfolio_owner", "default_portfolio", "system_tenant", "tenant_admin"
}


class EmailPolicyError(ValueError):
    """Raised when an email assignment would violate the platform policy."""


def normalize_email(email: str | None) -> str:
    return (email or "").strip().lower()


def is_owner_shared_email(email: str | None) -> bool:
    return normalize_email(email) == OWNER_SHARED_EMAIL


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _slug_from(obj: Any) -> str:
    if obj is None:
        return ""
    for attr in ("slug", "tenant_slug"):
        value = getattr(obj, attr, None)
        if value:
            return _norm(value)
    tenant = getattr(obj, "tenant", None)
    if tenant is not None and tenant is not obj:
        return _slug_from(tenant)
    return ""


def _email_from(obj: Any) -> str:
    if obj is None:
        return ""
    for attr in ("email", "owner_email", "contact_email"):
        value = getattr(obj, attr, None)
        if value:
            return normalize_email(str(value))
    tenant = getattr(obj, "tenant", None)
    if tenant is not None and tenant is not obj:
        return _email_from(tenant)
    return ""


def is_superadmin_context(user: Any = None, role: str | None = None) -> bool:
    if getattr(user, "is_superadmin", False):
        return True
    return _norm(role) in _SUPERADMIN_ROLES


def is_default_portfolio_context(
    user: Any = None,
    tenant: Any = None,
    role: str | None = None,
    slug: str | None = None,
) -> bool:
    """Return True only for the protected default/administrator tenant context."""
    candidate_slug = _norm(slug) or _slug_from(tenant) or _slug_from(user)
    if candidate_slug in _DEFAULT_TENANT_SLUGS:
        return True
    if _norm(role) in _DEFAULT_PORTFOLIO_ROLES and candidate_slug in _DEFAULT_TENANT_SLUGS:
        return True
    return False


def _is_protected_owner_context(user: Any = None, tenant: Any = None, role: str | None = None, slug: str | None = None) -> bool:
    return is_superadmin_context(user=user, role=role) or is_default_portfolio_context(
        user=user, tenant=tenant, role=role, slug=slug
    )


def get_email_matches(email: str | None, *, include_target: Any = None) -> list[Any]:
    """Return users whose email matches case-insensitively."""
    normalized = normalize_email(email)
    if not normalized:
        return []
    from app.models import User

    query = User.query.filter(func.lower(User.email) == normalized)
    target_id = getattr(include_target, "id", None)
    if target_id is not None:
        query = query.filter(User.id != target_id)
    return list(query.all())


def _classify_owner_user(user: Any) -> str:
    if getattr(user, "is_superadmin", False):
        return "superadmin"
    if is_default_portfolio_context(user=user):
        return "default_portfolio"
    return "normal"


def can_email_be_reused(
    email: str | None,
    *,
    existing_user: Any = None,
    target_user: Any = None,
    tenant: Any = None,
    role: str | None = None,
    slug: str | None = None,
) -> bool:
    try:
        assert_email_allowed_for_user(
            email,
            user=target_user,
            tenant=tenant,
            role=role,
            slug=slug,
            exclude_user=existing_user,
        )
        return True
    except EmailPolicyError:
        return False


def assert_email_allowed_for_user(
    email: str | None,
    *,
    user: Any = None,
    tenant: Any = None,
    role: str | None = None,
    slug: str | None = None,
    exclude_user: Any = None,
) -> str:
    """Validate that `email` can be assigned to the target context.

    Returns the normalized email. Raises EmailPolicyError with a user-safe
    message when the assignment is not allowed.
    """
    normalized = normalize_email(email)
    if not normalized:
        raise EmailPolicyError("A valid email address is required.")

    target_id = getattr(user, "id", None)
    exclude_id = getattr(exclude_user, "id", None)
    matches = get_email_matches(normalized)
    others = [m for m in matches if getattr(m, "id", None) not in {target_id, exclude_id, None}]

    if not is_owner_shared_email(normalized):
        if others:
            raise EmailPolicyError(_DUPLICATE_MESSAGE)
        return normalized

    target_is_superadmin = is_superadmin_context(user=user, role=role)
    target_is_default = is_default_portfolio_context(user=user, tenant=tenant, role=role, slug=slug)
    if not (target_is_superadmin or target_is_default):
        raise EmailPolicyError(_RESERVED_OWNER_MESSAGE)

    # Owner email can only appear on protected users. It cannot be used by a
    # random normal tenant, even if they submit this email manually.
    categories: dict[str, set[int | str]] = {"superadmin": set(), "default_portfolio": set()}
    for match in others:
        category = _classify_owner_user(match)
        if category == "normal":
            raise EmailPolicyError(_RESERVED_OWNER_MESSAGE)
        categories[category].add(getattr(match, "id", f"unsaved-{id(match)}"))

    # Include the pending target context in the cardinality check.
    pending_key: int | str = target_id if target_id is not None else "pending"
    if target_is_superadmin:
        categories["superadmin"].add(pending_key)
    if target_is_default and not target_is_superadmin:
        categories["default_portfolio"].add(pending_key)

    if len(categories["superadmin"]) > 1:
        raise EmailPolicyError("Only one SuperAdmin account may use the platform-owner email.")
    if len(categories["default_portfolio"]) > 1:
        raise EmailPolicyError("Only one default portfolio administrator may use the platform-owner email.")

    return normalized


def assert_public_signup_email_allowed(email: str | None) -> str:
    normalized = normalize_email(email)
    if is_owner_shared_email(normalized):
        raise EmailPolicyError(_RESERVED_OWNER_MESSAGE)
    return assert_email_allowed_for_user(normalized, role="public_signup")


def resolve_email_for_login(email: str | None, *, tenant_slug: str | None = None, require_superadmin: bool | None = None) -> Optional[Any]:
    """Resolve a user by email without using nondeterministic `.first()`.

    Normal emails should be globally unique after policy migration. The owner
    shared email needs context: superadmin portal, default/admin portal, or a
    tenant slug.
    """
    normalized = normalize_email(email)
    matches = get_email_matches(normalized)
    if require_superadmin is True:
        matches = [u for u in matches if getattr(u, "is_superadmin", False)]
    elif require_superadmin is False:
        matches = [u for u in matches if not getattr(u, "is_superadmin", False)]

    if tenant_slug:
        scoped = [u for u in matches if _slug_from(u) == _norm(tenant_slug)]
        if len(scoped) == 1:
            return scoped[0]
        if scoped:
            return None

    if len(matches) == 1:
        return matches[0]
    return None
