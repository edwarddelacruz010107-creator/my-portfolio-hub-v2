"""Phase 4 unified-notification contract tests (stdlib-only)."""
from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class Phase4NotificationSystemTests(unittest.TestCase):
    def read(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_schema_has_recipient_receipt_outbox_and_retention_contracts(self):
        model = self.read("app/models/notification.py")
        service = self.read("app/services/notifications/notification_service.py")
        for field in (
            "recipient_type", "recipient_id", "recipient_role", "tenant_id",
            "actor_type", "event_type", "entity_type", "safe_parameters",
            "action_route", "priority", "dedupe_key", "expires_at",
        ):
            self.assertIn(field, model)
        self.assertIn("class NotificationReceipt", model)
        self.assertIn("class NotificationDelivery", model)
        self.assertIn("uq_notification_receipts_user", model)
        self.assertIn("uq_notification_deliveries_channel", model)
        self.assertIn("purge_notification_retention", service)
        self.assertIn("Tenant/role events remain available", service)

    def test_migration_preserves_legacy_read_and_email_state(self):
        migration = self.read("migrations/versions/0058_global_notifications.py")
        self.assertIn('down_revision = "0057"', migration)
        self.assertIn('legacy_notification_id=row["id"]', migration)
        self.assertIn('read_at=row["read_at"] if row["is_read"] else None', migration)
        self.assertIn('if row["sent_via_email"]', migration)
        self.assertIn('channel="email"', migration)

    def test_authorization_dedupe_cursor_and_safe_rendering_are_server_owned(self):
        service = self.read("app/services/notifications/notification_service.py")
        self.assertIn("def _eligibility(context: RecipientContext)", service)
        self.assertIn("Notification.tenant_id == context.tenant_id", service)
        self.assertIn("Notification.recipient_id == str(context.user_id)", service)
        self.assertIn("Notification.recipient_role.in_(context.roles)", service)
        self.assertIn("with session.begin_nested()", service)
        self.assertIn("except IntegrityError", service)
        self.assertIn("base64.urlsafe_b64encode", service)
        self.assertIn("url_builder(notification.action_route", service)
        self.assertNotIn("|safe", self.read("app/templates/admin/notifications.html"))
        self.assertNotIn("|safe", self.read("app/templates/superadmin/notifications.html"))

    def test_both_role_surfaces_use_shared_center_and_conditional_feed(self):
        admin = self.read("app/templates/admin/base.html")
        superadmin = self.read("app/templates/superadmin/base.html")
        client = self.read("app/static/js/notifications-v1.js")
        for base in (admin, superadmin):
            self.assertEqual(base.count("ui.notification_center("), 1)
            self.assertEqual(base.count("notifications-v1.js"), 1)
        self.assertIn("If-None-Match", client)
        self.assertIn("visibilityState", client)
        self.assertIn("Math.min(MAX_DELAY, delay * 2)", client)
        self.assertIn("textContent", client)
        self.assertNotIn("innerHTML", client)
        self.assertIn("response.status === 304", client)

    def test_routes_enforce_recipient_context_and_cursor_pagination(self):
        tenant_routes = self.read("app/admin/routes/notifications_email.py")
        platform_routes = self.read("app/superadmin/routes/notifications.py")
        self.assertIn("RecipientContext.tenant_admin", tenant_routes)
        self.assertIn("RecipientContext.superadmin", platform_routes)
        self.assertIn("@superadmin_required", platform_routes)
        for source in (tenant_routes, platform_routes):
            self.assertIn("cursor=", source)
            self.assertIn("feed_etag", source)
            self.assertIn("mark_all_read_for_context", source)
            self.assertIn("archive_for_context", source)

    def test_real_producers_use_one_publish_service_and_legacy_writers_are_gone(self):
        producer_paths = (
            "app/public/routes.py",
            "app/tenant/__init__.py",
            "app/services/custom_domain_public.py",
            "app/__init__.py",
            "app/services/notifications/notification_service.py",
            "app/services/billing/renewal_scheduler.py",
            "app/services/billing/manual_billing.py",
            "app/services/communication/contact_service.py",
            "app/webhooks/__init__.py",
            "app/admin/routes/messaging.py",
            "app/superadmin/routes/messaging.py",
        )
        combined = "\n".join(self.read(path) for path in producer_paths)
        for event in (
            "project.like", "project.view_milestone", "billing.payment_submitted", "billing.payment_approved",
            "billing.payment_rejected", "billing.reminder_7d", "inquiry.new",
            "message.tenant_to_platform", "message.platform_to_tenant",
        ):
            self.assertIn(event, combined)
        self.assertGreaterEqual(combined.count("publish_notification("), 10)
        self.assertNotIn("SubscriptionNotification(", combined)
        self.assertNotIn("Inquiry(\n        tenant_slug=profile.tenant_slug", combined)

    def test_email_outbox_is_leased_retried_and_evidenced(self):
        service = self.read("app/services/notifications/notification_service.py")
        scheduler = self.read("app/services/billing/renewal_scheduler.py")
        for contract in (
            "with_for_update(skip_locked=True)", "MAX_DELIVERY_ATTEMPTS",
            'delivery.status = "processing"', '"dead" if dead else "failed"',
            "provider_message_id", "next_attempt_at",
        ):
            self.assertIn(contract, service)
        self.assertIn("process_pending_email_deliveries", scheduler)
        self.assertNotIn("_try_send_email", scheduler)

    def test_financial_reset_and_monitor_consume_unified_notifications(self):
        reset = self.read("app/services/billing/financial_reset_service.py")
        monitor = self.read("app/superadmin/routes/logs_monitor.py")
        self.assertIn('Notification.event_type.like("billing.%")', reset)
        self.assertIn("list_recent_billing_activity", monitor)
        self.assertNotIn("SubscriptionNotification.notification_type", monitor)


if __name__ == "__main__":
    unittest.main()
