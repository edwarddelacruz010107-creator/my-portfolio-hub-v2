# Public URL Contract

Effective: 2026-07-14

This contract defines one canonical URL per public surface. Existing aliases
remain compatibility adapters and must not implement a separate data path.

| Surface | Portfolio | Project detail | Contact submission |
|---|---|---|---|
| Platform owner | `/administrator-portfolio` | `/administrator-portfolio/project/<slug>` | `/contact` |
| Tenant path | `/<tenant_slug>/` | `/<tenant_slug>/project/<slug>` | `/<tenant_slug>/contact` |
| Tenant subdomain | `/` | `/project/<slug>` | `/contact` |
| Verified custom domain | `/` | `/project/<slug>` | `/contact` |

Host precedence is: verified custom domain, configured platform subdomain,
then platform host. A host is treated as a tenant subdomain only when it is a
single child label of `APP_BASE_URL`/`SERVER_NAME` (or `tenant.localhost` in
development) and maps to an active tenant. Arbitrary two-label hosts are never
interpreted as tenant subdomains.

Compatibility adapters:

- `/default` redirects permanently to `/administrator-portfolio`.
- `/u/<tenant_slug>` redirects permanently to the tenant's best public URL.
- `/contact/submit` accepts the legacy request shape but delegates to the
  canonical contact service.
- `/feed` redirects permanently to `/projects`.

GET aliases use permanent redirects. POST aliases delegate in-process so a
redirect never drops the request body. Platform-host `/project/<slug>` is not a
legacy portfolio URL and returns 404; it is reserved for subdomain/custom-domain
project routing.
