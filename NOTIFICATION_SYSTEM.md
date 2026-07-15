# Unified Notification System

## Ownership

`app/services/notifications/notification_service.py` is the only notification publisher and feed service. Compatibility imports from `app.services.notification_service` delegate to it. Producers must call `publish_notification`; they must not construct `Notification`, `NotificationDelivery`, or the legacy `SubscriptionNotification` directly.

## Recipient and authorization model

Notifications target exactly one of:

- a tenant (`recipient_type=tenant`, `tenant_id` required);
- a user (`recipient_type=user`, `recipient_id` required); or
- an allowlisted role (`recipient_type=role`, `recipient_role` required, optionally tenant-scoped).

The server constructs a `RecipientContext` from the authenticated principal. Feed, unread, read, archive, cursor, and ETag queries all apply the same eligibility expression. Client filters never grant access. Tenant and role notifications use `NotificationReceipt` for per-user read/archive state; a user cannot mark a notification that is outside their eligible query.

## Content and links

Content is selected from the `TEMPLATES` registry. Producers supply only the template's allowlisted scalar parameters, truncated to bounded lengths. Secrets, arbitrary nested objects, HTML templates, and arbitrary URLs are rejected. Actions store an allowlisted Flask endpoint plus scalar route parameters; URLs are built at read time. Jinja autoescaping and browser `textContent` remain the final output boundaries.

## Idempotency and aggregation

Every notification has a database-unique, recipient-scoped dedupe key. The publisher uses a nested transaction and recovers from concurrent uniqueness races. Naturally unique facts use provider event, payment submission, inquiry, reply, subscription occurrence, or entity IDs. Project-like activity uses `hourly_dedupe_key`, creating at most one tenant notification per project per UTC hour.

## Delivery outbox

Each channel has one `NotificationDelivery` row. In-app delivery is immediately evidenced as sent. Email begins pending and is leased with `SELECT ... FOR UPDATE SKIP LOCKED`, a ten-minute processing lease, bounded exponential retry, five-attempt dead-letter state, provider message ID, and privacy-bounded error evidence. Renewal scheduling and manual billing process the outbox; a failed provider send does not erase the notification or retry state.

## UX and polling

Tenant admin and superadmin use the same bell/dropdown macro and external controller. Each role has a separately authorized full feed with cursor pagination, exact event/status/date filters, mark one/all read, archive, and named-route actions. Polling sends `If-None-Match`, accepts `304`, pauses while hidden, refreshes on visibility, backs off from 30 seconds to five minutes, and displays an explicit unavailable state on failure. It never injects feed HTML.

## Event inventory

Real producers currently publish:

- project likes with hourly aggregation and real 10/100/1,000/10,000 view milestones;
- portfolio completion milestones at 25%, 50%, 75%, and 100%;
- visitor inquiries;
- tenant/platform new threads and replies;
- manual payment proof submitted, approved, and rejected;
- verified PayMongo/Dodo payment failure and subscription lifecycle events;
- scheduled activation, renewal, reminders, expiry, and cancellation.

AI budget/error and durable system-health templates are registered, but no activity is emitted until a real AI usage ledger or durable monitoring source exists in its owning phase. This prevents synthetic feed activity.

## Migration and rollback

Migration `0058_global_notifications.py` expands the schema with notifications, receipts, and deliveries, then backfills legacy `SubscriptionNotification` rows with stable legacy IDs, shared read timestamps, and email delivery evidence. Old rows remain during the rollback window. The application writes only the new schema.

Before release, rehearse upgrade and application rollback on PostgreSQL. An old application can continue reading the preserved legacy table, but it will not see new notification rows. Do not drop the legacy table until a stable release confirms zero old-writer usage. A downgrade of `0058` discards new-only notifications and is therefore not an acceptable production rollback after new events have been published; roll application code back while preserving the expanded schema, then forward-fix.

## Retention

- Explicitly expired rows: eligible immediately.
- Globally archived rows: retain 90 days.
- Direct-user rows carrying shared legacy read state: retain one year.
- Tenant and role events: retain unless explicitly expired, because their read/archive state is per user.

`purge_notification_retention` deletes bounded batches and is invoked by the renewal job. Database cascades remove receipts and deliveries with the notification.

## Production verification checklist

1. Upgrade a PostgreSQL clone and reconcile legacy/backfilled counts, read state, and sent-email evidence.
2. Exercise two tenants and two superadmins; prove unread/read/archive changes do not cross users or tenants.
3. Race identical producer events and confirm one notification/delivery pair.
4. Verify every action route under normal, removed-entity, and unauthorized conditions.
5. Force MailerSend transient/permanent failures and inspect retry, lease recovery, provider evidence, and dead-letter state.
6. Load-test unread, ETag feed, cursor pagination, and outbox claims with production-scale row counts and query plans.
7. Run axe, keyboard, screen-reader, zoom, reduced-motion, and supported-browser checks on both centers.
8. Monitor notification publish failures, outbox depth/age, dead letters, polling errors, and retention throughput after canary rollout.
