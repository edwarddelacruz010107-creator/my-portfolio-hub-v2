# Phase 10 — Integrated release hardening

**Source checkpoint:** 2026-07-15  
**Source decision:** **PRODUCTION RELEASE CANDIDATE**  
**Production promotion:** **HOLD — staging and infrastructure evidence required**

## Implemented controls

- Client addressing now consumes `request.remote_addr` only. `ProxyFix` is disabled by default and accepts a bounded, explicit `TRUSTED_PROXY_HOPS` value; Render declares one hop and direct Docker declares zero.
- Every template `<script>` and `<style>` block carries a CSP nonce. No CSP directive contains `unsafe-inline`. Legacy event/style attributes receive exact response-specific CSP hashes under `unsafe-hashes`, and all 229 referenced Iconify definitions/runtime assets are pinned and self-hosted.
- Validated/generated `X-Request-ID` values and aggregate `Server-Timing` query telemetry provide correlation without logging SQL text or parameters. Slow SQL logs contain a statement fingerprint only.
- Sensitive billing proofs pass through an owner-only quarantine file and a shell-free, bounded malware-scanner command. Production configuration fails sensitive upload screening closed.
- Production Compose no longer supplies a default PostgreSQL password.
- The deterministic source gate blocks raw forwarding-header use, missing CSP nonces, global inline block policy, dangerous JavaScript execution primitives, and insecure database defaults.
- Empty or drifted databases are detected by required-table, core/tenant migration-head, and tenant-model checks. Production fails before serving; development returns a controlled setup page. `setup-local.ps1` is the one-command Windows bootstrap.
- The reviewed 46-occurrence first-party `innerHTML` inventory is locked by path and normalized source-context digest. New or changed sinks fail the release gate.
- The canonical Render deployment is Docker-based, installs ClamAV signatures, verifies scanner availability, runs as a non-root user under `tini`, and probes `/readyz`.
- A CycloneDX 1.5 SBOM inventories 123 pinned Python and npm components, including reduced vendored icon artifacts.

## Verification completed

| Evidence | Result |
|---|---:|
| Phase 0B–5 and 7–10 plus release-candidate contracts | 139 passed |
| Phase 6 deterministic functions | 16 passed |
| JavaScript DOM/security contracts | 7 passed |
| Design-token lint | 9 files passed |
| Theme contract validation | 5 themes passed |
| Python compilation | Passed |
| Deterministic release source gate | Passed, 0 failures |
| npm dependency audit | 0 known vulnerabilities across 39 dependencies |
| CycloneDX inventory | 123 components |

## Known findings

The source gate inventories 67 inline handler attributes and 654 style attributes. They no longer require `unsafe-inline`: exact response hashes authorize only the rendered values. The 46 first-party HTML-rendering occurrences are classified and digest-locked; user/network/database strings that previously entered high-risk sinks were converted to safe DOM/text APIs. Frontend still owns gradual attribute removal, but any new or changed HTML-rendering sink now requires explicit security review.

The workspace did not include Flask/Jinja/SQLAlchemy/Alembic, PostgreSQL, Redis, a browser, a container daemon, or the `pip-audit`, Bandit, Semgrep, Trivy/Grype, and ClamAV executables. The 13 runtime-dependent test modules could not be collected here; the 139 source/domain contracts and 16 deterministic functions are not substitutes for the exact-image staging suite.

## Mandatory release gates

1. Build the exact pinned release image; run `pip-audit`, SAST, secret scan and container CVE scan against that artifact. No Critical/High finding may remain.
2. Execute the authorization/tenant negative matrix and full Flask/Jinja/CSRF suite against PostgreSQL and Redis.
3. Run CSP enforcement in staging across every page family; require zero blocked first-party assets and investigate every report-only violation.
4. Install and update the malware engine/signatures; verify clean, EICAR, timeout, unavailable, and quarantine-deletion paths.
5. Run backup restore/PITR for core, tenant, and private-object metadata; record RPO/RTO and checksums.
6. Rehearse expand/contract rolling deploy and rollback with old/new workers concurrently.
7. Run load/soak and accessibility/browser matrices; attach p95/p99, query-count, cache, page-weight, axe and manual results.
8. Complete an authorized staging penetration test covering tenant isolation, custom hosts, authentication recovery, impersonation, uploads, exports, SSRF/XSS, CSRF, webhook replay and abuse controls.

No canary or production promotion is approved until these gates are attached to the release decision.
