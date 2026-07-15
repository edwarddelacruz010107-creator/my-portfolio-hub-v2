# Phase 0C — Routing, Readiness, and Honest Content

Source implementation completed: 2026-07-14

## Delivered

- Added a canonical public URL/host contract for platform-owner, tenant-path,
  tenant-subdomain, and verified custom-domain surfaces.
- Preserved `/default`, `/u/<slug>`, `/contact/submit`, and `/feed` as tested
  redirects or in-process adapters. POST adapters retain their request body.
- Made tenant subdomain `/`, `/project/<slug>`, and `/contact` host-aware without
  treating arbitrary hosts as tenants.
- Consolidated all active contact paths on
  `app.services.communication.contact_service` and retained the old import as a
  logic-free shim.
- Added dependency-free `/livez` and read-only `/readyz`. Readiness verifies the
  core and tenant database connections, their exact Alembic heads, and the
  required production Redis cache. Render and Docker now probe `/readyz`.
- Replaced signup-generated portfolio facts with an empty real profile shell
  plus a versioned `onboarding_workflows` core table and dashboard checklist.
- Removed unproven company/customer claims and attributed testimonials from the
  landing page.
- Removed Developer Pro's remote CDN/font/image dependencies, fabricated
  profile/project/resume fallbacks, fake capability claims, and fake machine
  metrics. Missing real content renders labeled empty states.
- Added the explicit production support matrix.

## Schema and rollout

Core migration `0056` adds `onboarding_workflows`. It is additive and readable
by the previous application version, which ignores the new table. Deploy with:

1. Back up the core database.
2. Run `flask db-upgrade-all` before starting the new web image.
3. Confirm `/livez` returns 200 immediately after process start.
4. Confirm `/readyz` returns 200 only after both databases are at their expected
   heads and Redis responds.
5. Create a disposable email, Google, and GitHub account and verify all six
   portfolio content tables are empty for each tenant.
6. Exercise platform-host, tenant-path, tenant-subdomain, and verified
   custom-domain portfolio/project/contact routes.
7. Run CSP-enforced browser smoke tests for every installed theme.

Rollback the application image without downgrading `0056`; the table is
additive and harmless to the previous version. Do not downgrade after the new
version has created workflow rows unless those rows are explicitly disposable.

## Automated evidence

- `python -m unittest tests.test_phase0c_routing_readiness_content -v`: 7/7 pass.
- `python -m unittest tests.test_deterministic_migrations -v`: 6/6 pass.
- `python -m compileall -q app migrations tests`: pass.
- AST parse of every application and migration Python file: pass.
- Developer Pro external URL scan: zero matches.

## Deployment-gated evidence

Flask, SQLAlchemy, Alembic, Redis, PostgreSQL, and browser runtimes were not
available in this workspace. Therefore the live route-map snapshot,
host-aware end-to-end tests, failure injection for PostgreSQL/Redis, provider
sandbox sends, and CSP browser runs remain mandatory release checks. Source
guards do not replace those tests.
