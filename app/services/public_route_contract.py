"""Canonical public URL and host contract.

The route decorators remain where Flask historically registered them. This
module owns the contract used to distinguish the platform host, tenant path
URLs, tenant subdomains, and verified custom domains without duplicating host
parsing in individual handlers.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from flask import current_app


@dataclass(frozen=True)
class PublicRouteContract:
    surface: str
    portfolio: str
    project: str
    contact: str


ROUTE_CONTRACTS = {
    'platform': PublicRouteContract('platform', '/administrator-portfolio', '/administrator-portfolio/project/<slug>', '/contact'),
    'tenant_path': PublicRouteContract('tenant_path', '/<tenant_slug>/', '/<tenant_slug>/project/<slug>', '/<tenant_slug>/contact'),
    'tenant_subdomain': PublicRouteContract('tenant_subdomain', '/', '/project/<slug>', '/contact'),
    'custom_domain': PublicRouteContract('custom_domain', '/', '/project/<slug>', '/contact'),
}

LEGACY_ROUTE_ADAPTERS = {
    '/default': '/administrator-portfolio',
    '/u/<tenant_slug>': '/<tenant_slug>/',
    '/contact/submit': '/contact',
}


def _hostname(value: str | None) -> str:
    raw = (value or '').strip()
    if not raw:
        return ''
    parsed = urlparse(raw if '://' in raw else f'//{raw}')
    return (parsed.hostname or '').lower().rstrip('.')


def platform_base_host() -> str:
    return (
        _hostname(current_app.config.get('APP_BASE_URL'))
        or _hostname(current_app.config.get('SERVER_NAME'))
    )


def subdomain_slug_for_host(host_header: str | None) -> str | None:
    """Return a tenant slug only for a real child of the configured host."""
    host = _hostname(host_header)
    if not host:
        return None

    base = platform_base_host()
    if base and host.endswith(f'.{base}'):
        candidate = host[:-(len(base) + 1)]
        if candidate and '.' not in candidate:
            return candidate

    # Explicit local-development contract: tenant.localhost.
    if host.endswith('.localhost') and host.count('.') == 1:
        return host.split('.', 1)[0]
    return None


def resolve_active_subdomain_tenant(host_header: str | None):
    slug = subdomain_slug_for_host(host_header)
    if not slug or slug in {'www', 'api', 'admin', 'mail', 'ftp'}:
        return None
    from app.models.core import Tenant
    return Tenant.query.filter_by(slug=slug, status='active').first()
