"""
app/utils/paymongo.py — PayMongo billing integration (v4.1 PATCHED)

MED-01 FIX:
  verify_webhook_signature() used hmac.new() with positional hashlib.sha256.
  Replaced with explicit hmac.new(key, msg, digestmod='sha256') for clarity
  and forward compatibility.  Both forms work in Python 3.12 but the string
  digestmod is the documented idiom.

No other logic changed in this file.
CRIT-04 NOTE: This file imports mark_subscription_cancelled and
mark_subscription_expired from app.services.billing. Apply patches/billing.py
FIRST or these imports will still raise ImportError.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests
from flask import current_app

logger = logging.getLogger(__name__)

PAYMONGO_BASE_URL = 'https://api.paymongo.com/v1'

BILLING_CYCLES = {
    'monthly': {'interval': 'month', 'count': 1},
    'yearly': {'interval': 'year', 'count': 1},
}


def _get_auth_header() -> str:
    secret_key = current_app.config.get('PAYMONGO_SECRET_KEY', '')
    if not secret_key:
        raise ValueError('PAYMONGO_SECRET_KEY not configured')
    credentials = base64.b64encode(f'{secret_key}:'.encode()).decode()
    return f'Basic {credentials}'


def _api_request(method: str, path: str, *, json_body: dict | None = None, timeout: int = 15) -> dict | None:
    headers = {
        'Authorization': _get_auth_header(),
        'Content-Type': 'application/json',
    }
    url = f'{PAYMONGO_BASE_URL}{path}'
    try:
        response = requests.request(method, url, json=json_body, headers=headers, timeout=timeout)
        if response.status_code in (200, 201):
            return response.json()
        logger.error('PayMongo API %s %s failed: %s %s', method, path, response.status_code, response.text)
    except Exception as exc:
        logger.exception('PayMongo API error %s %s: %s', method, path, exc)
    return None


def _plan_amount_centavos(plan_name: str, billing_cycle: str = 'monthly') -> int:
    from app.utils import BILLING_PLANS
    from app.models.portfolio import normalize_plan_name
    from app.services.billing import plan_duration_days

    plan_norm = normalize_plan_name(plan_name)
    plan_data = BILLING_PLANS.get(plan_norm, BILLING_PLANS['Basic'])
    price = float(plan_data.get('price', 0))
    if billing_cycle == 'yearly':
        price *= 12
    return int(price * 100)


def create_checkout_session(
    *,
    tenant_id: int,
    tenant_slug: str,
    plan_name: str,
    billing_cycle: str,
    subscription_id: int,
    success_url: str,
    failed_url: str,
    cancel_url: str,
) -> Optional[Dict[str, Any]]:
    """Create a PayMongo Checkout Session with tenant metadata for webhooks."""
    if billing_cycle not in BILLING_CYCLES:
        logger.error('Invalid billing cycle: %s', billing_cycle)
        return None

    amount = _plan_amount_centavos(plan_name, billing_cycle)
    metadata = {
        'tenant_id': str(tenant_id),
        'tenant_slug': tenant_slug,
        'plan_name': plan_name,
        'billing_cycle': billing_cycle,
        'subscription_id': str(subscription_id),
    }

    payload = {
        'data': {
            'attributes': {
                'send_email_receipt': True,
                'show_payment_details': True,
                'redirect': {
                    'success': success_url,
                    'failed': failed_url,
                    'cancelled': cancel_url,
                },
                'payment_method_types': ['card', 'gcash', 'paymaya', 'grab_pay', 'dob'],
                'description': f'{plan_name} subscription — {tenant_slug}',
                'metadata': metadata,
                'line_items': [
                    {
                        'amount': amount,
                        'currency': 'PHP',
                        'name': f'{plan_name} Plan ({billing_cycle})',
                        'quantity': 1,
                    }
                ],
            }
        }
    }

    result = _api_request('POST', '/checkout_sessions', json_body=payload)
    if not result:
        return None

    data = result.get('data', {})
    attrs = data.get('attributes', {})
    return {
        'checkout_url': attrs.get('checkout_url'),
        'session_id': data.get('id'),
        'customer_id': attrs.get('customer_id'),
    }


def fetch_subscription(paymongo_subscription_id: str) -> dict | None:
    result = _api_request('GET', f'/subscriptions/{paymongo_subscription_id}')
    return result.get('data') if result else None


def verify_webhook_signature(payload: bytes, signature: str) -> bool:
    """
    Verify PayMongo webhook HMAC-SHA256 signature.

    PayMongo sends a compound Paymongo-Signature header:
        t=<unix_ts>,te=<test_hmac>,li=<live_hmac>

    We extract the ``li=`` field (live HMAC) and compare it to
    HMAC-SHA256(PAYMONGO_WEBHOOK_SECRET, raw_body).

    If the header contains no ``=`` separator it is treated as a plain
    hex digest (unit-test / legacy paths).

    Constant-time comparison prevents timing attacks.

    Reference:
        https://developers.paymongo.com/docs/webhook-signature-verification
    """
    webhook_secret = current_app.config.get('PAYMONGO_WEBHOOK_SECRET', '').strip()
    if not webhook_secret or not signature:
        logger.warning(
            'Webhook signature verification skipped: missing secret or signature'
        )
        return False

    try:
        # ── Parse compound header (t=...,te=...,li=...) ───────────────────────
        sig_to_check: str | None = None

        if '=' in signature:
            for part in signature.split(','):
                part = part.strip()
                if part.startswith('li='):
                    sig_to_check = part[3:]
                    break
            if sig_to_check is None:
                logger.warning(
                    'Webhook signature header has no li= field: %.40s', signature
                )
                return False
        else:
            # Plain hex digest — test / legacy
            sig_to_check = signature.strip()

        # ── Compute expected HMAC ─────────────────────────────────────────────
        expected = hmac.new(
            webhook_secret.encode('utf-8'),
            payload,
            digestmod='sha256',
        ).hexdigest()

        # ── Constant-time comparison ──────────────────────────────────────────
        is_valid = hmac.compare_digest(expected, sig_to_check.lower())

        if not is_valid:
            logger.warning(
                'Webhook HMAC mismatch: expected=%.16s… got=%.16s…',
                expected,
                sig_to_check,
            )

        return is_valid

    except Exception:
        logger.exception('Webhook signature verification raised an exception')
        return False


def _record_webhook_event(event_data: dict, *, summary: str = '') -> bool:
    """Return False if event_id already exists (idempotency skip)."""
    from app import db
    from app.models.portfolio import WebhookEvent

    data_block = event_data.get('data', {})
    event_id = data_block.get('id') or event_data.get('id')
    if not event_id:
        return True

    existing = WebhookEvent.query.filter_by(event_id=event_id).first()
    if existing:
        return False

    tenant_id = None
    attrs = data_block.get('attributes', {})
    metadata = attrs.get('metadata') or {}
    if metadata.get('tenant_id'):
        try:
            tenant_id = int(metadata['tenant_id'])
        except (TypeError, ValueError):
            pass

    event = WebhookEvent(
        event_id=event_id,
        event_type=event_data.get('type', 'unknown'),
        tenant_id=tenant_id,
        payload_summary=summary[:500],
        processed=False,
    )
    db.session.add(event)
    db.session.commit()
    return True


def handle_payment_webhook(event_data: Dict[str, Any]) -> bool:
    """Route PayMongo webhook events with idempotency."""
    event_type = event_data.get('type', '')
    data_block = event_data.get('data', {})
    attrs = data_block.get('attributes', {})
    event_id = data_block.get('id', 'unknown')

    logger.info('PayMongo webhook: type=%s id=%s', event_type, event_id)

    if not _record_webhook_event(event_data, summary=event_type):
        logger.info('PayMongo webhook duplicate skipped: %s', event_id)
        return True

    handlers = {
        'payment.paid': _handle_payment_paid,
        'checkout_session.payment.paid': _handle_checkout_paid,
        'payment.failed': _handle_payment_failed,
        'subscription.updated': _handle_subscription_updated,
        'subscription.cancelled': _handle_subscription_cancelled,
        'subscription.expired': _handle_subscription_expired,
    }

    handler = handlers.get(event_type)
    if handler:
        ok = handler(attrs, event_id, event_type)
        _mark_event_processed(event_id, ok)
        return ok

    logger.info('PayMongo webhook unhandled type=%s — acknowledged', event_type)
    _mark_event_processed(event_id, True)
    return True


def _mark_event_processed(event_id: str, success: bool) -> None:
    from app import db
    from app.models.portfolio import WebhookEvent

    event = WebhookEvent.query.filter_by(event_id=event_id).first()
    if event:
        event.processed = success
        db.session.commit()


def _resolve_subscription(metadata: dict, payment_id: str | None = None):
    from app.models.portfolio import Subscription

    tenant_id = metadata.get('tenant_id')
    subscription_id = metadata.get('subscription_id')

    if subscription_id:
        try:
            sub = Subscription.query.get(int(subscription_id))
            if sub:
                return sub
        except (TypeError, ValueError):
            pass

    if payment_id:
        sub = Subscription.query.filter_by(paymongo_payment_id=payment_id).first()
        if sub:
            return sub

    if tenant_id:
        try:
            tid = int(tenant_id)
            return (
                Subscription.query
                .filter(
                    Subscription.tenant_id == tid,
                    Subscription.status.in_(['pending', 'active']),
                )
                .order_by(Subscription.created_at.desc())
                .first()
            )
        except (TypeError, ValueError):
            pass
    return None


def _extract_metadata(attrs: dict) -> dict:
    return attrs.get('metadata') or {}


def _handle_payment_paid(attrs: dict, payment_id: str, _event_type: str = '') -> bool:
    from app import db
    from app.services.billing import activate_subscription

    metadata = _extract_metadata(attrs)
    subscription = _resolve_subscription(metadata, payment_id)
    if not subscription:
        logger.warning('payment.paid: no subscription for payment_id=%s', payment_id)
        return False

    if subscription.paymongo_payment_id == payment_id and subscription.status == 'active':
        return True

    amount = attrs.get('amount', 0)
    amount_php = float(amount) / 100 if amount else None

    try:
        activate_subscription(
            subscription,
            plan=metadata.get('plan_name'),
            billing_cycle=metadata.get('billing_cycle'),
            paymongo_payment_id=payment_id,
            amount=amount_php,
            source='payment.paid',
        )
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        logger.exception('payment.paid handler failed')
        return False


def _handle_checkout_paid(attrs: dict, event_id: str, _event_type: str = '') -> bool:
    """checkout_session.payment.paid — payment nested under attributes."""
    payment = attrs.get('payment') or {}
    if isinstance(payment, dict) and payment.get('id'):
        payment_attrs = payment.get('attributes') or payment
        merged = {**attrs, **payment_attrs}
        merged.setdefault('metadata', attrs.get('metadata') or payment_attrs.get('metadata') or {})
        return _handle_payment_paid(merged, payment.get('id') or event_id)

    metadata = _extract_metadata(attrs)
    return _handle_payment_paid(attrs, event_id)


def _handle_payment_failed(attrs: dict, payment_id: str, _event_type: str = '') -> bool:
    from app import db
    from app.models.portfolio import Subscription

    metadata = _extract_metadata(attrs)
    subscription = _resolve_subscription(metadata, payment_id)
    if subscription and subscription.status == 'pending':
        subscription.last_webhook_at = datetime.now(timezone.utc)
        db.session.commit()
    return True


def _handle_subscription_updated(attrs: dict, _event_id: str, event_type: str) -> bool:
    from app import db
    from app.models.portfolio import Subscription, Profile, normalize_plan_name
    # CRIT-04 NOTE: these now exist in the patched billing.py
    from app.services.billing import activate_subscription, mark_subscription_cancelled

    metadata = _extract_metadata(attrs)
    pm_status = attrs.get('status', '')
    paymongo_sub_id = attrs.get('id') or metadata.get('subscription_id')

    subscription = None
    if paymongo_sub_id:
        subscription = Subscription.query.filter_by(paymongo_subscription_id=paymongo_sub_id).first()
    if not subscription:
        subscription = _resolve_subscription(metadata)

    if not subscription:
        logger.warning('subscription.updated: no local subscription found')
        return True

    subscription.last_webhook_at = datetime.now(timezone.utc)
    subscription.paymongo_subscription_id = paymongo_sub_id or subscription.paymongo_subscription_id

    if pm_status == 'active':
        activate_subscription(subscription, source=event_type)
        db.session.commit()
        return True
    if pm_status in ('cancelled', 'canceled'):
        mark_subscription_cancelled(subscription, source=event_type)
        return True
    if pm_status in ('unpaid', 'past_due', 'paused'):
        subscription.status = 'expired'
        profile = Profile.query.filter_by(tenant_id=subscription.tenant_id).first()
        if profile:
            profile.enforce_expiry(commit=False)
        db.session.commit()
        return True

    db.session.commit()
    return True


def _handle_subscription_cancelled(attrs: dict, _event_id: str, event_type: str) -> bool:
    from app.models.portfolio import Subscription
    # CRIT-04 NOTE: exists in patched billing.py
    from app.services.billing import mark_subscription_cancelled

    metadata = _extract_metadata(attrs)
    paymongo_sub_id = attrs.get('id') or metadata.get('subscription_id')
    subscription = None
    if paymongo_sub_id:
        subscription = Subscription.query.filter_by(paymongo_subscription_id=paymongo_sub_id).first()
    if not subscription:
        subscription = _resolve_subscription(metadata)

    if subscription:
        mark_subscription_cancelled(subscription, source=event_type)
    return True


def _handle_subscription_expired(attrs: dict, _event_id: str, event_type: str) -> bool:
    from app.models.portfolio import Subscription
    # CRIT-04 NOTE: exists in patched billing.py
    from app.services.billing import mark_subscription_expired

    metadata = _extract_metadata(attrs)
    subscription = _resolve_subscription(metadata)
    if subscription:
        mark_subscription_expired(subscription, source=event_type)
    return True


def cancel_subscription(paymongo_subscription_id: str) -> bool:
    result = _api_request(
        'POST',
        f'/subscriptions/{paymongo_subscription_id}/cancel',
        json_body={'data': {}},
    )
    return result is not None
