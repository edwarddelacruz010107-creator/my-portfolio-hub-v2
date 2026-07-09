"""Custom-domain validation, DNS verification, and host resolution helpers.

This module is intentionally small and dependency-light. DNS TXT verification
uses dnspython when available (email-validator normally installs it
transitively); if it is unavailable, the UI reports that verification cannot be
completed in this runtime instead of silently marking a domain verified.
"""
from __future__ import annotations

import ipaddress
import re
import secrets
from dataclasses import dataclass
from datetime import timezone
from urllib.parse import quote, urlparse

from flask import current_app

from app import db
from app.models.core import Tenant, TenantCustomDomain
from app.utils.datetime_utils import utc_now

_DOMAIN_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_TOKEN_PREFIX = "myportfoliohub-verify="


@dataclass(frozen=True)
class DomainValidationResult:
    ok: bool
    domain: str = ""
    error: str = ""


@dataclass(frozen=True)
class DomainVerificationResult:
    verified: bool
    message: str
    checked_txt: str = ""


def _host_from_config_url(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"//{raw}")
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    return host


def platform_hosts() -> set[str]:
    """Hosts that tenants must not claim as custom domains."""
    hosts = {
        _host_from_config_url(current_app.config.get("APP_BASE_URL")),
        _host_from_config_url(current_app.config.get("CUSTOM_DOMAIN_CNAME_TARGET")),
        _host_from_config_url(current_app.config.get("SERVER_NAME")),
    }
    raw_extra = current_app.config.get("CUSTOM_DOMAIN_BLOCKED_HOSTS") or ""
    hosts.update(_host_from_config_url(h) for h in str(raw_extra).split(",") if h.strip())
    return {h for h in hosts if h}


def custom_domain_target_host() -> str:
    """Return the public host tenants should CNAME to."""
    configured = _host_from_config_url(current_app.config.get("CUSTOM_DOMAIN_CNAME_TARGET"))
    if configured:
        return configured
    app_host = _host_from_config_url(current_app.config.get("APP_BASE_URL"))
    return app_host or "your-production-domain.example"


def generate_verification_token() -> str:
    return f"{_TOKEN_PREFIX}{secrets.token_urlsafe(24)}"


def normalize_custom_domain(raw_domain: str | None) -> DomainValidationResult:
    raw = (raw_domain or "").strip()
    if not raw:
        return DomainValidationResult(False, error="Enter a domain name.")

    parsed = urlparse(raw if "://" in raw else f"//{raw}")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        return DomainValidationResult(False, error="Enter only the domain, without a path or query string.")

    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        return DomainValidationResult(False, error="Enter a valid domain name.")

    try:
        # Normalize international domains safely for storage/comparison.
        host = host.encode("idna").decode("ascii")
    except Exception:
        return DomainValidationResult(False, error="Domain contains unsupported characters.")

    if len(host) > 253:
        return DomainValidationResult(False, error="Domain is too long.")
    if "*" in host or "_" in host or ".." in host:
        return DomainValidationResult(False, error="Domain cannot contain wildcards, underscores, or empty labels.")

    try:
        ipaddress.ip_address(host)
        return DomainValidationResult(False, error="Use a hostname, not an IP address.")
    except ValueError:
        pass

    if host in {"localhost", "example.com"} or host.endswith((".localhost", ".local", ".internal", ".test", ".example")):
        return DomainValidationResult(False, error="Use a real public domain.")

    labels = host.split(".")
    if len(labels) < 2:
        return DomainValidationResult(False, error="Enter a fully-qualified domain such as www.example.com.")
    if not all(_DOMAIN_LABEL_RE.match(label) for label in labels):
        return DomainValidationResult(False, error="Domain contains an invalid label.")

    if host in platform_hosts():
        return DomainValidationResult(False, error="This is a platform/system domain and cannot be assigned to a tenant.")

    return DomainValidationResult(True, domain=host)


def dns_txt_name(domain: str) -> str:
    return f"_myportfoliohub.{domain}"


def dns_instructions(record: TenantCustomDomain) -> dict[str, str]:
    return {
        "txt_name": dns_txt_name(record.normalized_domain),
        "txt_value": record.verification_token,
        "cname_name": record.normalized_domain,
        "cname_value": custom_domain_target_host(),
    }


def resolve_verified_custom_domain(host_header: str | None) -> TenantCustomDomain | None:
    """Resolve request Host to a verified tenant custom domain record."""
    validation = normalize_custom_domain(host_header or "")
    if not validation.ok:
        return None
    record = (
        TenantCustomDomain.query
        .filter_by(normalized_domain=validation.domain, status="verified")
        .first()
    )
    if not record or not record.tenant:
        return None
    if (record.tenant.status or "").lower() != "active":
        return None
    return record


def can_tenant_use_custom_domain(tenant: Tenant | None) -> bool:
    if tenant is None:
        return False
    if callable(getattr(tenant, "has_feature", None)):
        return bool(tenant.has_feature("custom_domain"))
    return False


def verify_domain_dns(record: TenantCustomDomain) -> DomainVerificationResult:
    """Verify ownership via TXT record.

    Required DNS record:
      _myportfoliohub.<domain> TXT <verification_token>
    """
    txt_name = dns_txt_name(record.normalized_domain)
    record.last_checked_at = utc_now()

    try:
        import dns.resolver  # type: ignore
    except Exception:
        record.status = "pending"
        record.failure_reason = "DNS resolver package is unavailable on this server."
        return DomainVerificationResult(
            False,
            "DNS verification could not run because dnspython is not installed in this environment.",
            checked_txt=txt_name,
        )

    try:
        answers = dns.resolver.resolve(txt_name, "TXT")
        values: list[str] = []
        for answer in answers:
            strings = getattr(answer, "strings", None)
            if strings:
                values.append("".join(part.decode("utf-8", "ignore") for part in strings))
            else:
                values.append(str(answer).strip('"'))
    except Exception as exc:
        record.status = "pending"
        record.failure_reason = f"TXT record not found yet: {type(exc).__name__}"
        return DomainVerificationResult(
            False,
            "TXT verification record was not found yet. DNS changes can take a few minutes to propagate.",
            checked_txt=txt_name,
        )

    expected = (record.verification_token or "").strip()
    if any(value.strip() == expected for value in values):
        record.status = "verified"
        record.verified_at = utc_now()
        record.failure_reason = None
        return DomainVerificationResult(True, "Domain ownership verified successfully.", checked_txt=txt_name)

    record.status = "pending"
    record.failure_reason = "TXT record exists but does not match the verification token."
    return DomainVerificationResult(
        False,
        "TXT record exists, but its value does not match the verification token.",
        checked_txt=txt_name,
    )


def create_or_replace_domain(tenant: Tenant, domain: str) -> tuple[TenantCustomDomain | None, str | None]:
    validation = normalize_custom_domain(domain)
    if not validation.ok:
        return None, validation.error

    existing_other = (
        TenantCustomDomain.query
        .filter(TenantCustomDomain.normalized_domain == validation.domain)
        .filter(TenantCustomDomain.tenant_id != tenant.id)
        .first()
    )
    if existing_other:
        return None, "This domain is already assigned to another tenant."

    record = (
        TenantCustomDomain.query
        .filter_by(tenant_id=tenant.id, normalized_domain=validation.domain)
        .first()
    )
    if record is None:
        record = TenantCustomDomain(
            tenant_id=tenant.id,
            tenant_slug=tenant.slug,
            domain=validation.domain,
            normalized_domain=validation.domain,
            verification_token=generate_verification_token(),
            status="pending",
        )
        db.session.add(record)
    else:
        record.domain = validation.domain
        record.tenant_slug = tenant.slug
        if not record.verification_token:
            record.verification_token = generate_verification_token()
        if record.status != "verified":
            record.status = "pending"
    return record, None

def _custom_domain_public_scheme() -> str:
    scheme = str(current_app.config.get("CUSTOM_DOMAIN_PUBLIC_SCHEME") or "https").strip().lower()
    return scheme if scheme in {"http", "https"} else "https"


def _current_verified_custom_domain():
    """Return cached verified custom-domain record for the current request host, if any."""
    try:
        from flask import g, request
    except Exception:
        return None
    if hasattr(g, "_custom_domain_record_cache"):
        return g._custom_domain_record_cache
    record = resolve_verified_custom_domain(getattr(request, "host", None))
    g._custom_domain_record_cache = record
    return record


def primary_custom_domain_for_tenant(tenant_slug: str | None) -> TenantCustomDomain | None:
    """Return the tenant's preferred verified custom domain, if the plan still allows it.

    The lookup is cached per request to avoid one query per project card. If a
    tenant loses custom-domain access, links safely fall back to the platform
    slug URL even if an old verified row remains in the database.
    """
    slug = (tenant_slug or "").strip().lower()
    if not slug:
        return None

    try:
        from flask import g
    except Exception:
        g = None  # type: ignore

    cache = getattr(g, "_primary_custom_domain_by_slug", None) if g is not None else None
    if cache is None and g is not None:
        cache = {}
        g._primary_custom_domain_by_slug = cache
    if isinstance(cache, dict) and slug in cache:
        return cache[slug]

    query = (
        TenantCustomDomain.query
        .filter_by(tenant_slug=slug, status="verified")
        .order_by(TenantCustomDomain.is_primary.desc(), TenantCustomDomain.verified_at.desc(), TenantCustomDomain.id.asc())
    )
    record = query.first()
    if record and record.tenant:
        tenant_active = (record.tenant.status or "").lower() == "active"
        if not tenant_active or not can_tenant_use_custom_domain(record.tenant):
            record = None

    if isinstance(cache, dict):
        cache[slug] = record
    return record


def tenant_portfolio_public_url(tenant_slug: str | None, *, external: bool = False) -> str:
    """Build the best public portfolio URL for a tenant.

    On a verified custom-domain request for that same tenant, this returns the
    clean local root path. Else, it prefers the tenant's primary verified custom
    domain when available, then falls back to the existing platform slug URL.
    """
    from flask import url_for

    slug = (tenant_slug or "default").strip().lower() or "default"
    current = _current_verified_custom_domain()
    if current is not None and current.tenant_slug == slug:
        return url_for("root", _external=external)

    record = primary_custom_domain_for_tenant(slug)
    if record is not None:
        return f"{_custom_domain_public_scheme()}://{record.normalized_domain}/"

    if slug == "default":
        return url_for("public.administrator_portfolio", _external=external)
    return url_for("tenant.portfolio", tenant_slug=slug, _external=external)


def tenant_project_public_url(tenant_slug: str | None, project_slug: str | None, *, external: bool = False) -> str:
    """Build the best public project/case-study URL for a tenant project."""
    from flask import url_for

    slug = (tenant_slug or "default").strip().lower() or "default"
    project = (project_slug or "").strip()
    if not project:
        return tenant_portfolio_public_url(slug, external=external)

    current = _current_verified_custom_domain()
    if current is not None and current.tenant_slug == slug:
        return url_for("custom_domain_project_detail", slug=project, _external=external)

    if slug == "default":
        return url_for("public.administrator_project_detail", slug=project, _external=external)

    record = primary_custom_domain_for_tenant(slug)
    if record is not None:
        return f"{_custom_domain_public_scheme()}://{record.normalized_domain}/project/{quote(project, safe='')}"

    return url_for("tenant.project_detail", tenant_slug=slug, slug=project, _external=external)

