# Security and operations runbook

## Proxy and request identity

Set `TRUSTED_PROXY_HOPS` to the exact fixed proxy chain length. Use `0` when the application is directly reachable. Restrict the origin to the declared proxy at the network layer. A topology change requires a spoofing test before rollout. Search application Python for direct `X-Forwarded-For` or `CF-Connecting-IP` consumption; the release gate must find none.

## Sensitive-upload screening

Set `MALWARE_SCAN_REQUIRED=true`, `MALWARE_SCANNER_COMMAND="clamscan --no-summary"`, and keep `UPLOAD_QUARANTINE_FOLDER` outside static/public roots on owner-only persistent storage. The canonical Docker build installs the engine without loading its signature database in the memory-bounded build worker. Its non-root Gunicorn entrypoint downloads/refreshes signatures, verifies a real clean-file scan, and then refreshes every six hours. The pre-deploy migration command checks the executable but does not load signatures. Use at least the Render Standard 2 GiB service tier; 512 MiB Free/Starter instances are unsupported for inline ClamAV. A refresh failure is tolerated only when runtime signatures from the prior 24 hours exist. Alert on `infected`, `error`, timeout, or unavailable results. Never retain an infected sample in application storage. A scanner outage blocks startup or sensitive uploads, while an in-process post-start failure does not expose quarantined bytes.

## Dependency and patch SLA

| Severity | Triage | Remediation target |
|---|---:|---:|
| Critical, exploitable | 4 hours | 24 hours or disable affected feature |
| High | 1 business day | 7 days |
| Medium | 5 business days | 30 days |
| Low | Next review | 90 days |

Generate the SBOM from lock files, then scan the built artifact—not only source. Preserve scanner version, advisory database timestamp, image digest, findings, exceptions, owner and expiry.

## Data retention baseline

| Data | Default | Deletion behavior |
|---|---:|---|
| Financial ledger/provider facts | 7 years, jurisdiction review required | Append correction; do not erase evidence required by law |
| Notification delivery/outbox | 90 days after terminal state | Delete payload, retain aggregate operational evidence |
| AI request content | 30 days unless feature policy is shorter | Redact content; preserve privacy-safe usage/accounting facts |
| Security/admin audit | 1 year online, 7 years archive | Legal/privacy review; integrity evidence retained |
| Billing proofs | 90 days after final decision/dispute window | Delete private object and reference; retain decision/audit metadata |
| Raw analytics events | 13 months | Aggregate or delete; honor tenant erasure and minimum thresholds |

Owners must configure jurisdiction-specific overrides. Every erasure job is bounded, idempotent, audited, and reconciles object plus database references.

## Backup/PITR drill

1. Record core/tenant backup IDs, WAL/PITR boundary, private-object manifest checksum and encryption-key version.
2. Restore into an isolated account/network with outbound delivery disabled.
3. Apply migrations through the normal locked command; never stamp or use runtime DDL.
4. Verify both Alembic heads, row-count/checksum samples, ledger reconciliation, tenant isolation, private-object authorization and deletion tombstones.
5. Record achieved RPO/RTO. Destroy restored sensitive data under the approved disposal procedure.

## Rolling migration and rollback

Only additive expand migrations may precede mixed-version workers. New code must dual-read/write where documented and the previous version must understand all live writes. Backfills are resumable and rate-bounded. Contract changes require one stable rollback window and measured zero legacy use. Roll back application workers before any forward-fix migration; never reverse an immutable financial fact.

## SLO and alert baseline

- Availability: 99.9% monthly for public reads; 99.5% for authenticated mutations.
- Latency: public read p95 under 500 ms and p99 under 1.5 s; authenticated read p95 under 750 ms; provider-backed work reports its own budget.
- Error budget alerts at 25%, 50%, 75% and 100% consumption; immediate alert for readiness failure, cross-tenant authorization signal, migration-head drift, webhook verification spike, outbox dead letters, malware detection, backup failure, or ledger reconciliation mismatch.
- Logs include correlation ID, route/endpoint, privacy-safe tenant/user identifiers, provider event/request IDs where applicable, outcome and duration. Never log secrets, tokens, prompts/proofs, raw SQL parameters, or full provider references.

## Incident first response

Contain the affected route/provider, preserve immutable logs and relevant artifact digests, rotate exposed credentials, and start a timestamped incident record. Do not purge evidence before legal/privacy review. For suspected tenant isolation, private proof exposure, ledger mutation or secret compromise, treat severity as Critical until scoped otherwise.
