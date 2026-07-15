# Phase 4 — Global Notification System

**Status:** source implementation complete  
**Migration:** `0058_global_notifications.py`

## Delivered

- General recipient/tenant/actor/event/entity/template/action/priority/dedupe/lifecycle notification schema.
- Per-user receipt state and durable in-app/email delivery outbox with lease, retry, dead-letter, provider evidence, and bounded error details.
- One recipient-authorized service for publishing, rendering, deduplication, feeds, unread state, archive, ETag, cursor pagination, outbox processing, retention, and the operational billing projection.
- Legacy subscription-notification backfill preserving IDs, read state, and prior email delivery state; old direct writers are removed from active producers.
- Shared tenant-admin and superadmin bell/dropdown plus role-specific full pages, named-route deep links, filters, read/archive actions, explicit states, safe DOM rendering, ETag, visibility awareness, and bounded backoff.
- Real producers for project likes/view milestones, portfolio milestones, inquiries, bidirectional platform messaging, manual billing review, provider payment failures/lifecycle, and scheduled renewals/reminders/expiry.
- Unified billing monitor consumption and financial-reset handling for new billing notifications.
- Explicit retention, migration, rollback, and production verification policy in `NOTIFICATION_SYSTEM.md`.

No AI or system-health activity is synthesized. Their safe templates are ready for real usage/telemetry producers in the owning later phases.

## Automated evidence

- 8 Phase 4 schema, authorization, dedupe, migration, producer, outbox, UX, and reset/monitor source tests pass.
- 3 notification DOM security/polling tests pass, including malicious payloads, cross-origin action rejection, ETag reuse, and hidden-page suppression.
- Phase 0C–3 and deterministic-migration regression suites pass.
- Existing shared-component DOM tests pass.
- Python application/migration compilation and JavaScript syntax checks pass.

## Deployment-gated evidence

This workspace does not contain the deployable Flask/SQLAlchemy/PostgreSQL/Redis stack. PostgreSQL migration/backfill reconciliation, concurrent uniqueness/receipt/outbox races, MailerSend fixtures, query plans/budgets, multi-worker lease recovery, tenant matrix integration, Flask/Jinja route rendering, accessibility, and browser visual behavior remain release gates. Follow `NOTIFICATION_SYSTEM.md` before canary rollout.

## Rollback boundary

Keep migration `0058` and the legacy table during application rollback. New events are not visible to an old release, so monitor this bounded compatibility limitation. Do not destructively downgrade after publishing new-only notification data; forward-fix and contract the legacy schema only after a measured zero-usage rollback window.
