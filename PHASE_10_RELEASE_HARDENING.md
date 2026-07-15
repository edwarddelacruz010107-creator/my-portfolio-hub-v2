# Phase 10 — Integrated release hardening

**Source checkpoint:** 2026-07-15  
**Release decision:** **HOLD — staging and infrastructure evidence required**

## Implemented controls

- Client addressing now consumes `request.remote_addr` only. `ProxyFix` is disabled by default and accepts a bounded, explicit `TRUSTED_PROXY_HOPS` value; Render declares one hop and direct Docker declares zero.
- Every template `<script>` and `<style>` block carries a CSP nonce. Global block policies no longer contain `unsafe-inline`; duplicate unpkg Iconify loading and unused script/style/font hosts were removed.
- Validated/generated `X-Request-ID` values and aggregate `Server-Timing` query telemetry provide correlation without logging SQL text or parameters. Slow SQL logs contain a statement fingerprint only.
- Sensitive billing proofs pass through an owner-only quarantine file and a shell-free, bounded malware-scanner command. Production configuration fails sensitive upload screening closed.
- Production Compose no longer supplies a default PostgreSQL password.
- The deterministic source gate blocks raw forwarding-header use, missing CSP nonces, global inline block policy, dangerous JavaScript execution primitives, and insecure database defaults.
- A CycloneDX 1.5 SBOM inventories 118 pinned Python and npm components.

## Verification completed

| Evidence | Result |
|---|---:|
| Phase 0C–5 and 7–10 unittest contracts | 124 passed |
| Phase 6 deterministic functions | 16 passed |
| JavaScript DOM/security contracts | 7 passed |
| Design-token lint | 9 files passed |
| Theme contract validation | 5 themes passed |
| Python compilation | Passed |
| Deterministic release source gate | Passed, 0 failures |
| npm dependency audit | 0 known vulnerabilities across 39 dependencies |
| CycloneDX inventory | 118 components |

## Known findings

The source gate inventories three legacy compatibility sets: 67 inline handler attributes, 660 style attributes, and 62 `innerHTML`/HTML-rendering references. Global executable/style blocks are nonce-protected, but CSP `script-src-attr` and `style-src-attr` retain scoped `unsafe-inline` compatibility. These are not represented as closed. Frontend owns migration and sink-by-sink review by **2026-08-15**; a release must either finish it or record a security-approved exception based on staging CSP reports.

The workspace did not include Flask/Jinja/SQLAlchemy/Alembic, PostgreSQL, Redis, a browser, a container daemon, or the `pip-audit`, Bandit, Semgrep, Trivy/Grype, and ClamAV executables. `pip check` only validated the workspace interpreter and is not dependency-vulnerability evidence for the pinned application environment.

## Mandatory release gates

1. Build the exact pinned release image; run `pip-audit`, SAST, secret scan and container CVE scan against that artifact. No Critical/High finding may remain.
2. Execute the authorization/tenant negative matrix and full Flask/Jinja/CSRF suite against PostgreSQL and Redis.
3. Run CSP enforcement in staging; review every reported attribute and HTML-rendering sink, then remove the scoped compatibility directives.
4. Install and update the malware engine/signatures; verify clean, EICAR, timeout, unavailable, and quarantine-deletion paths.
5. Run backup restore/PITR for core, tenant, and private-object metadata; record RPO/RTO and checksums.
6. Rehearse expand/contract rolling deploy and rollback with old/new workers concurrently.
7. Run load/soak and accessibility/browser matrices; attach p95/p99, query-count, cache, page-weight, axe and manual results.
8. Complete an authorized staging penetration test covering tenant isolation, custom hosts, authentication recovery, impersonation, uploads, exports, SSRF/XSS, CSRF, webhook replay and abuse controls.

No canary or production promotion is approved until these gates are attached to the release decision.

