"""
app/services/renewal_scheduler.py — Subscription Renewal Notification Scheduler
Portfolio CMS v4.0

Runs as a daily background job (02:00 AM) via APScheduler.
Responsibilities:
  1. Send 7-day reminder for monthly plans (billing_cycle='monthly' or duration~30d)
  2. Send 30-day reminder for yearly/custom plans (billing_cycle='yearly' or duration>=365d)
  3. Auto-expire subscriptions whose expires_at has passed
  4. Send email notifications via MailerSend
  5. Deduplicate — never send the same reminder twice per subscription

Dedup flags on Subscription:
    reminder_sent_7d   — set True after 7d reminder dispatched
    reminder_sent_30d  — set True after 30d reminder dispatched

These flags are reset when the tenant renews (handled by billing service hooks).
"""

import logging
from datetime import datetime, timezone, timedelta

from flask import current_app

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Threshold helpers
# ─────────────────────────────────────────────────────────────────────────────

def _is_monthly(sub) -> bool:
    """True for monthly plans: billing_cycle='monthly' OR duration ≈30 days."""
    if sub.billing_cycle and sub.billing_cycle.lower() in ('monthly', 'month'):
        return True
    if sub.started_at and sub.expires_at:
        started = sub.started_at if sub.started_at.tzinfo else sub.started_at.replace(tzinfo=timezone.utc)
        expires = sub.expires_at if sub.expires_at.tzinfo else sub.expires_at.replace(tzinfo=timezone.utc)
        duration_days = (expires - started).days
        return duration_days <= 31
    return False


def _is_yearly_or_longer(sub) -> bool:
    """True for yearly or multi-year plans."""
    if sub.billing_cycle and sub.billing_cycle.lower() in ('yearly', 'annual', 'year'):
        return True
    if sub.started_at and sub.expires_at:
        started = sub.started_at if sub.started_at.tzinfo else sub.started_at.replace(tzinfo=timezone.utc)
        expires = sub.expires_at if sub.expires_at.tzinfo else sub.expires_at.replace(tzinfo=timezone.utc)
        duration_days = (expires - started).days
        return duration_days >= 365
    return False


def _days_until_expiry(sub) -> int | None:
    """Return days until expiry (can be negative if already expired). None if no expires_at."""
    if not sub.expires_at:
        return None
    expires = sub.expires_at if sub.expires_at.tzinfo else sub.expires_at.replace(tzinfo=timezone.utc)
    delta = expires - datetime.now(timezone.utc)
    return delta.days


# ─────────────────────────────────────────────────────────────────────────────
# Notification dispatch
# ─────────────────────────────────────────────────────────────────────────────

def _create_notification(db, SubscriptionNotification, sub, notif_type: str,
                          title: str, message: str) -> 'SubscriptionNotification':
    notif = SubscriptionNotification(
        tenant_id=sub.tenant_id,
        subscription_id=sub.id,
        notification_type=notif_type,
        title=title,
        message=message,
        is_read=False,
        sent_via_dashboard=True,
        sent_via_email=False,
    )
    db.session.add(notif)
    return notif


def _try_send_email(sub, subject: str, body: str) -> bool:
    """
    Send renewal notification email via MailerSend.
    v5.0: Flask-Mail / SMTP removed. MailerSend is the sole provider.
    """
    try:
        tenant = sub.tenant
        recipient = (
            tenant.contact_email or
            tenant.email or
            None
        )
        if not recipient:
            # Try first admin user email
            try:
                first_user = tenant.users.first()
                recipient = first_user.email if first_user else None
            except Exception:
                recipient = None
        if not recipient:
            logger.warning('No recipient email for tenant_id=%s', sub.tenant_id)
            return False

        from app.services.mailersend_service import send_email
        ok, _ = send_email(recipient, subject, body)
        if ok:
            logger.info('Renewal email sent via MailerSend to %s (tenant_id=%s)', recipient, sub.tenant_id)
            return True
        logger.error('Renewal email failed for %s (tenant_id=%s) — MailerSend returned error', recipient, sub.tenant_id)
        return False
    except Exception as exc:
        logger.error('Renewal email failed for tenant_id=%s: %s', sub.tenant_id, exc)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Core scheduler job
# ─────────────────────────────────────────────────────────────────────────────

def run_renewal_check(app=None):
    """
    Main scheduler entrypoint. Must be called inside app context.

    Flow for each active subscription:
        1. Compute days_until_expiry
        2. If monthly and days==7 and not reminder_sent_7d → send 7d reminder
        3. If yearly/custom and days==30 and not reminder_sent_30d → send 30d reminder
        4. If days <= 0 → auto-expire subscription + tenant
        5. Persist all changes in a single commit
    """
    _app = app or current_app._get_current_object()
    with _app.app_context():
        try:
            from app import db
            from app.models.portfolio import Subscription, Tenant
            from app.models.portfolio import SubscriptionNotification

            now = datetime.now(timezone.utc)
            logger.info('[RenewalScheduler] Starting daily check at %s UTC', now.isoformat())

            active_subs = (
                Subscription.query
                .filter(Subscription.status == 'active')
                .filter(Subscription.expires_at.isnot(None))
                .all()
            )

            processed = expired_count = reminder_7d_count = reminder_30d_count = 0

            for sub in active_subs:
                try:
                    days_left = _days_until_expiry(sub)
                    if days_left is None:
                        continue

                    tenant = sub.tenant
                    plan_label = (sub.plan or 'Subscription').title()
                    expires_str = sub.expires_at.strftime('%B %d, %Y') if sub.expires_at else 'N/A'
                    tenant_name = tenant.company_name or tenant.slug

                    # ── AUTO-EXPIRE ──────────────────────────────────────────
                    if days_left <= 0:
                        logger.info('[RenewalScheduler] Expiring sub id=%s tenant=%s', sub.id, tenant.slug)
                        sub.status = 'expired'

                        # Suspend tenant if no other active sub
                        other_active = Subscription.query.filter(
                            Subscription.tenant_id == tenant.id,
                            Subscription.id != sub.id,
                            Subscription.status == 'active',
                        ).first()
                        if not other_active and tenant.status == 'active':
                            tenant.status = 'suspended'

                        _create_notification(
                            db, SubscriptionNotification, sub,
                            notif_type='expired',
                            title='Subscription Expired',
                            message=(
                                f'Your {plan_label} subscription has expired. '
                                f'Please renew to restore access.'
                            ),
                        )
                        expired_count += 1

                    # ── 7-DAY REMINDER (monthly) ─────────────────────────────
                    elif _is_monthly(sub) and days_left == 7 and not sub.reminder_sent_7d:
                        title = 'Subscription Expiring Soon'
                        body = (
                            f'Your {plan_label} subscription will expire in 7 days '
                            f'on {expires_str}.\n'
                            f'Renew now to avoid interruption of service.'
                        )
                        notif = _create_notification(
                            db, SubscriptionNotification, sub,
                            notif_type='reminder_7d',
                            title=title,
                            message=body,
                        )
                        sub.reminder_sent_7d = True

                        email_body = (
                            f'Hello {tenant_name},\n\n'
                            f'Your {plan_label} subscription will expire on {expires_str}.\n\n'
                            f'Please renew before the expiration date to avoid service interruption.\n\n'
                            f'Thank you.'
                        )
                        sent = _try_send_email(sub, 'Subscription Expiry Reminder', email_body)
                        if sent:
                            notif.sent_via_email = True

                        reminder_7d_count += 1
                        logger.info('[RenewalScheduler] 7d reminder sent: tenant=%s plan=%s', tenant.slug, plan_label)

                    # ── 30-DAY REMINDER (yearly / custom) ────────────────────
                    elif _is_yearly_or_longer(sub) and days_left == 30 and not sub.reminder_sent_30d:
                        title = 'Subscription Expiring Soon'
                        body = (
                            f'Your {plan_label} subscription will expire in 30 days '
                            f'on {expires_str}.\n'
                            f'Renew now to continue enjoying premium features.'
                        )
                        notif = _create_notification(
                            db, SubscriptionNotification, sub,
                            notif_type='reminder_30d',
                            title=title,
                            message=body,
                        )
                        sub.reminder_sent_30d = True

                        email_body = (
                            f'Hello {tenant_name},\n\n'
                            f'Your {plan_label} subscription will expire on {expires_str}.\n\n'
                            f'Please renew before the expiration date to avoid service interruption.\n\n'
                            f'Thank you.'
                        )
                        sent = _try_send_email(sub, 'Subscription Expiry Reminder', email_body)
                        if sent:
                            notif.sent_via_email = True

                        reminder_30d_count += 1
                        logger.info('[RenewalScheduler] 30d reminder sent: tenant=%s plan=%s', tenant.slug, plan_label)

                    processed += 1

                except Exception as sub_exc:
                    logger.error('[RenewalScheduler] Error processing sub id=%s: %s', sub.id, sub_exc)
                    db.session.rollback()
                    continue

            db.session.commit()
            logger.info(
                '[RenewalScheduler] Done. Processed=%d Expired=%d 7dReminders=%d 30dReminders=%d',
                processed, expired_count, reminder_7d_count, reminder_30d_count,
            )

        except Exception as exc:
            logger.exception('[RenewalScheduler] Fatal error: %s', exc)
            try:
                from app import db
                db.session.rollback()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────────────
# Renewal hook — called when a tenant renews
# ─────────────────────────────────────────────────────────────────────────────

def on_subscription_renewed(sub, db):
    """
    Reset reminder flags and create a 'Subscription Renewed' notification.
    Call this from billing service after activating a renewed subscription.
    """
    try:
        from app.models.portfolio import SubscriptionNotification
        sub.reminder_sent_7d  = False
        sub.reminder_sent_30d = False

        plan_label = (sub.plan or 'Subscription').title()
        notif = SubscriptionNotification(
            tenant_id=sub.tenant_id,
            subscription_id=sub.id,
            notification_type='renewed',
            title='Subscription Renewed Successfully',
            message=f'Your {plan_label} subscription has been renewed. Thank you!',
            is_read=False,
            sent_via_dashboard=True,
            sent_via_email=False,
        )
        db.session.add(notif)
        logger.info('[RenewalScheduler] Renewal notification created for tenant_id=%s', sub.tenant_id)
    except Exception as exc:
        logger.error('[RenewalScheduler] on_subscription_renewed error: %s', exc)


def on_subscription_activated(sub, db):
    """
    Create an 'activated' notification when a new subscription goes active.
    """
    try:
        from app.models.portfolio import SubscriptionNotification
        plan_label = (sub.plan or 'Subscription').title()
        notif = SubscriptionNotification(
            tenant_id=sub.tenant_id,
            subscription_id=sub.id,
            notification_type='activated',
            title='Subscription Activated',
            message=f'Your {plan_label} subscription is now active. Welcome!',
            is_read=False,
            sent_via_dashboard=True,
            sent_via_email=False,
        )
        db.session.add(notif)
    except Exception as exc:
        logger.error('[RenewalScheduler] on_subscription_activated error: %s', exc)
