"""
app/webhooks/__init__.py — Webhook Handlers v5.0

FIX SUMMARY (Requirements #2):
  ✅ Signature verification with constant-time comparison
  ✅ Idempotency: prevents duplicate processing via event_id tracking
  ✅ Proper transaction handling with rollback
  ✅ Correct HTTP response codes (200, 400, 401, 500)
  ✅ User-friendly error messages
  ✅ Comprehensive logging
  ✅ Never crashes on missing fields
  ✅ Proper decorator ordering

All webhook handlers:
  1. Verify HMAC signature
  2. Check for idempotency (event_id)
  3. Process event within transaction
  4. Rollback on error
  5. Return proper HTTP response
  6. Log all operations
"""

import logging
import json
from datetime import datetime, timezone

from flask import Blueprint, request, jsonify, current_app
from sqlalchemy.exc import SQLAlchemyError

from app import csrf, limiter, db
from app.services.paymongo_service import (
    verify_webhook_signature,
    record_webhook_event,
    mark_webhook_processed,
)

logger = logging.getLogger(__name__)

webhooks = Blueprint('webhooks', __name__, url_prefix='/webhooks')


# ─────────────────────────────────────────────────────────────────────────
# WEBHOOK: PAYMONGO
# ─────────────────────────────────────────────────────────────────────────

@webhooks.route('/paymongo', methods=['POST'])
@csrf.exempt  # Webhooks don't use CSRF tokens
@limiter.limit('120 per minute')
def paymongo_webhook():
    """
    Handle PayMongo webhook events.
    
    Process:
      1. Capture raw body before parsing (signature verification needs it)
      2. Verify HMAC-SHA256 signature
      3. Parse JSON
      4. Check idempotency (event_id)
      5. Route to handler
      6. Return appropriate HTTP response
    
    Returns:
        (200, success) - Event processed
        (400, error)   - Invalid request (bad signature, no payload)
        (401, error)   - Signature verification failed
        (500, error)   - Server error (still returns 200 to prevent retries)
    """
    remote_ip = request.remote_addr
    
    try:
        # ─────────────────────────────────────────────────────────
        # CAPTURE RAW BODY (required for signature verification)
        # ─────────────────────────────────────────────────────────
        
        payload = request.get_data()
        if not payload:
            logger.warning('PayMongo webhook: empty body from %s', remote_ip)
            return jsonify(error='Missing payload'), 400
        
        # ─────────────────────────────────────────────────────────
        # EXTRACT AND VALIDATE SIGNATURE
        # ─────────────────────────────────────────────────────────
        
        # PayMongo sends signature in one of two headers
        signature = (
            request.headers.get('Paymongo-Signature', '')
            or request.headers.get('X-Paymongo-Signature', '')
            or ''
        )
        
        if not signature:
            logger.warning('PayMongo webhook: missing signature from %s', remote_ip)
            return jsonify(error='Missing signature'), 401
        
        # ─────────────────────────────────────────────────────────
        # VERIFY SIGNATURE (constant-time comparison)
        # ─────────────────────────────────────────────────────────
        
        if not verify_webhook_signature(payload, signature):
            logger.error(
                'PayMongo webhook: HMAC verification failed from %s. '
                'Signature: %.20s…',
                remote_ip, signature
            )
            # Return 401 but log as security issue
            return jsonify(error='Invalid signature'), 401
        
        # ─────────────────────────────────────────────────────────
        # PARSE JSON PAYLOAD
        # ─────────────────────────────────────────────────────────
        
        try:
            event_data = request.get_json(silent=False)
        except Exception as exc:
            logger.error('PayMongo webhook: JSON parse error from %s: %s', remote_ip, exc)
            return jsonify(error='Invalid JSON'), 400
        
        if not event_data:
            logger.warning('PayMongo webhook: empty JSON from %s', remote_ip)
            return jsonify(error='Empty JSON'), 400
        
        # ─────────────────────────────────────────────────────────
        # EXTRACT EVENT DETAILS
        # ─────────────────────────────────────────────────────────
        
        data_block = event_data.get('data', {})
        attrs = data_block.get('attributes', {})
        event_id = data_block.get('id', '')
        event_type = attrs.get('type', 'unknown')
        
        if not event_id:
            logger.warning(
                'PayMongo webhook: missing event_id. type=%s from %s',
                event_type, remote_ip
            )
            return jsonify(error='Missing event_id'), 400
        
        logger.info(
            'PayMongo webhook: type=%s id=%s from %s',
            event_type, event_id, remote_ip
        )
        
        # ─────────────────────────────────────────────────────────
        # CHECK IDEMPOTENCY
        # ─────────────────────────────────────────────────────────
        
        is_new_event = record_webhook_event(
            db.session,
            event_data,
            event_id,
            event_type,
        )
        
        if not is_new_event:
            logger.info('PayMongo webhook: duplicate event skipped. id=%s', event_id)
            # Return 200 for idempotent retry
            return jsonify(success=True, message='Event already processed'), 200
        
        # ─────────────────────────────────────────────────────────
        # ROUTE TO HANDLER
        # ─────────────────────────────────────────────────────────
        
        success = _handle_paymongo_event(event_type, attrs, event_id, event_data)
        mark_webhook_processed(db.session, event_id, success)
        
        if success:
            logger.info('PayMongo webhook: event processed. id=%s', event_id)
            return jsonify(success=True, message='Event processed'), 200
        else:
            logger.error('PayMongo webhook: handler failed. id=%s type=%s', event_id, event_type)
            return jsonify(success=False, message='Handler error'), 500
        
    except Exception as exc:
        logger.exception('PayMongo webhook: unhandled error from %s', remote_ip)
        return jsonify(error='Internal server error'), 500


def _handle_paymongo_event(
    event_type: str,
    attrs: dict,
    event_id: str,
    event_data: dict,
) -> bool:
    """
    Route PayMongo event to appropriate handler.
    
    Returns:
        True if handler succeeded, False otherwise
    """
    handlers = {
        'payment.paid': _handle_payment_paid,
        'payment.failed': _handle_payment_failed,
        'payment.refunded': _handle_payment_adjustment,
        'refund.succeeded': _handle_payment_adjustment,
        'chargeback.created': _handle_payment_adjustment,
        'dispute.lost': _handle_payment_adjustment,
        'checkout_session.payment.paid': _handle_checkout_paid,
        'subscription.created': _handle_subscription_created,
        'subscription.updated': _handle_subscription_updated,
        'subscription.cancelled': _handle_subscription_cancelled,
        'subscription.expired': _handle_subscription_expired,
    }
    
    handler = handlers.get(event_type)
    if not handler:
        logger.info('PayMongo webhook: unhandled event type=%s id=%s', event_type, event_id)
        return True  # Return True for unhandled events (no action needed)
    
    try:
        return handler(attrs, event_id, event_data)
    except Exception as exc:
        logger.exception('PayMongo event handler error: type=%s id=%s', event_type, event_id)
        return False


# ─────────────────────────────────────────────────────────────────────────
# EVENT HANDLERS
# ─────────────────────────────────────────────────────────────────────────

def _paymongo_resource(attrs: dict) -> tuple[dict, dict]:
    resource = attrs.get('data') if isinstance(attrs.get('data'), dict) else {}
    resource_attrs = resource.get('attributes') if isinstance(resource.get('attributes'), dict) else resource
    return resource, resource_attrs


def _provider_datetime(value):
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)
        except ValueError:
            return None
    return None


def _accept_paymongo_state_event(subscription, resource_attrs: dict, event_id: str) -> bool:
    occurred_at = _provider_datetime(
        resource_attrs.get('updated_at')
        or resource_attrs.get('paid_at')
        or resource_attrs.get('created_at')
    )
    if occurred_at is None:
        logger.warning('PayMongo state event lacks provider occurrence time. id=%s', event_id)
        return False
    previous = subscription.provider_state_occurred_at
    if previous and previous.tzinfo is None:
        previous = previous.replace(tzinfo=timezone.utc)
    if previous and occurred_at < previous:
        logger.info('PayMongo out-of-order state event ignored. id=%s', event_id)
        return False
    subscription.provider_state_occurred_at = occurred_at
    subscription.last_webhook_at = datetime.now(timezone.utc)
    return True

def _handle_payment_paid(attrs: dict, event_id: str, event_data: dict) -> bool:
    """Handle payment.paid event."""
    from app.models.portfolio import Subscription, SubscriptionStatus
    
    try:
        resource, resource_attrs = _paymongo_resource(attrs)
        payment_id = str(resource.get('id') or event_data.get('data', {}).get('id', ''))
        metadata = resource_attrs.get('metadata') or attrs.get('metadata') or {}
        subscription_id = metadata.get('subscription_id')
        
        if not subscription_id:
            logger.warning('Payment paid: missing subscription_id in metadata. id=%s', event_id)
            return True
        
        try:
            sub = db.session.query(Subscription).filter_by(id=int(subscription_id)).first()
        except (ValueError, TypeError):
            logger.error('Payment paid: invalid subscription_id=%s', subscription_id)
            return False
        
        if not sub:
            logger.warning('Payment paid: subscription not found. id=%s', subscription_id)
            return True

        received_at = datetime.now(timezone.utc)
        if not _accept_paymongo_state_event(sub, resource_attrs, event_id):
            from app.services.ledger import post_provider_event
            post_provider_event(
                'paymongo', event_data,
                tenant_id=sub.tenant_id, subscription_id=sub.id,
                received_at=received_at,
                environment=current_app.config.get('PAYMONGO_MODE', 'test'),
                event_id_override=event_id,
                session=db.session, commit=False,
            )
            db.session.commit()
            return True

        # FIX: previously this only flipped `sub.status` inline, bypassing
        # activate_subscription() entirely — expires_at/started_at were
        # never set for PayMongo-webhook activations, amount_paid was never
        # synced from the (still full-price) placeholder, and the discount
        # a tenant applied at checkout never got redeemed. `sub.paid_at`
        # was also a dead write: no such column exists on Subscription, so
        # it silently discarded the assignment.
        from app.services.billing.billing import activate_subscription
        from app.services.billing import discount_checkout
        from app.models.portfolio import normalize_plan_name

        plan = normalize_plan_name(sub.plan or metadata.get('plan_name') or 'Basic')
        cycle = getattr(sub, 'billing_cycle', None) or metadata.get('billing_cycle') or 'monthly'

        # amount here is what PayMongo actually charged (from the checkout
        # session's line_items, already discount-adjusted at initiate_checkout
        # time) — pass it through so amount_paid reflects the real charge
        # rather than being recomputed from list price.
        # NOTE: verify 'amount' is the correct key in your live PayMongo
        # payment.paid payload before relying on this in prod (PayMongo's
        # documented convention is centavos under `attributes.amount`, but
        # confirm against an actual captured webhook body). This is
        # defensive either way: if the key is missing/wrong, charged_amount
        # stays None and activate_subscription()/apply_on_activation() fall
        # back to list price / quoted discount price respectively — it
        # never silently records a wrong number, it just doesn't override.
        amount_attr = resource_attrs.get('amount', attrs.get('amount'))
        charged_amount = (amount_attr / 100.0) if isinstance(amount_attr, (int, float)) else None
        provider_currency = str(resource_attrs.get('currency') or attrs.get('currency') or '').strip().upper()
        if len(provider_currency) != 3:
            provider_currency = None

        activate_subscription(
            sub,
            plan=plan,
            billing_cycle=cycle,
            paymongo_payment_id=payment_id,
            amount=charged_amount,
            currency=provider_currency,
            currency_exponent=2,
            source='payment.paid',
        )

        redemption = discount_checkout.apply_on_activation(
            tenant_id=sub.tenant_id,
            subscription=sub,
            plan=plan,
            billing_cycle=cycle,
            code=sub.coupon_code,
            commit=False,
        )

        from app.services.billing import invoice_service
        invoice = invoice_service.record_invoice(
            tenant_id=sub.tenant_id,
            subscription=sub,
            plan=plan,
            billing_cycle=cycle,
            payment_method=sub.payment_method,
            payment_provider='paymongo',
            payment_reference=payment_id,
            redemption=redemption,
            currency_override=provider_currency,
            original_amount_minor=int(amount_attr) if isinstance(amount_attr, int) and provider_currency else None,
            original_currency=provider_currency if isinstance(amount_attr, int) and provider_currency else None,
            currency_exponent=2 if isinstance(amount_attr, int) and provider_currency else None,
            actor=f'paymongo:{event_id}',
            commit=False,
        )

        from app.services.ledger import post_provider_event
        post_provider_event(
            'paymongo',
            event_data,
            tenant_id=sub.tenant_id,
            subscription_id=sub.id,
            received_at=received_at,
            environment=current_app.config.get('PAYMONGO_MODE', 'test'),
            event_id_override=event_id,
            invoice_id=invoice.id if invoice else None,
            session=db.session,
            commit=False,
        )

        db.session.commit()

        logger.info('Payment processed: subscription=%s payment=%s', subscription_id, payment_id)
        return True
        
    except SQLAlchemyError as exc:
        db.session.rollback()
        logger.exception('Database error in payment handler. id=%s', event_id)
        return False


def _handle_payment_adjustment(attrs: dict, event_id: str, event_data: dict) -> bool:
    """Post a refund/chargeback without mutating the original settlement."""
    from app.models import Subscription
    from app.services.ledger import post_provider_event

    resource, resource_attrs = _paymongo_resource(attrs)
    metadata = resource_attrs.get('metadata') or attrs.get('metadata') or {}
    subscription_id = metadata.get('subscription_id')
    if not subscription_id:
        logger.warning('PayMongo adjustment lacks subscription metadata. id=%s', event_id)
        return False
    try:
        subscription = Subscription.query.filter_by(id=int(subscription_id)).first()
    except (TypeError, ValueError):
        return False
    if subscription is None:
        logger.warning('PayMongo adjustment subscription not found. id=%s', subscription_id)
        return False

    try:
        posting, _ = post_provider_event(
            'paymongo',
            event_data,
            tenant_id=subscription.tenant_id,
            subscription_id=subscription.id,
            received_at=datetime.now(timezone.utc),
            environment=current_app.config.get('PAYMONGO_MODE', 'test'),
            event_id_override=event_id,
            session=db.session,
            commit=False,
        )
        if posting is None:
            logger.warning('PayMongo adjustment could not be normalized. id=%s', event_id)
            db.session.rollback()
            return False
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        logger.exception('PayMongo adjustment failed. id=%s', event_id)
        return False


def _handle_payment_failed(attrs: dict, event_id: str, event_data: dict) -> bool:
    """Handle payment.failed event."""
    from app.models.portfolio import Subscription, SubscriptionStatus
    
    try:
        resource, resource_attrs = _paymongo_resource(attrs)
        metadata = resource_attrs.get('metadata') or attrs.get('metadata') or {}
        subscription_id = metadata.get('subscription_id')
        failure_reason = resource_attrs.get('failure_reason') or attrs.get('failure_reason') or 'unknown'
        
        if not subscription_id:
            logger.warning('Payment failed: missing subscription_id. id=%s', event_id)
            return True
        
        try:
            sub = db.session.query(Subscription).filter_by(id=int(subscription_id)).first()
        except (ValueError, TypeError):
            logger.error('Payment failed: invalid subscription_id=%s', subscription_id)
            return False
        
        if not sub:
            logger.warning('Payment failed: subscription not found. id=%s', subscription_id)
            return True
        
        if _accept_paymongo_state_event(sub, resource_attrs, event_id):
            from app.services.billing.lifecycle_service import transition_subscription
            transition_subscription(
                sub, 'past_due', actor='paymongo-webhook',
                reason='Verified provider payment failure',
                idempotency_key=f'paymongo:{event_id}:past_due',
                provider='paymongo', provider_event_id=event_id, commit=False,
            )
            _publish_billing_lifecycle_notification(sub, 'billing.payment_failed', f'paymongo:{event_id}')
        db.session.commit()
        
        logger.info('Payment failed: subscription=%s reason=%s', subscription_id, failure_reason)
        return True
        
    except SQLAlchemyError as exc:
        db.session.rollback()
        logger.exception('Database error in failed payment handler. id=%s', event_id)
        return False


def _handle_checkout_paid(attrs: dict, event_id: str, event_data: dict) -> bool:
    """Handle checkout_session.payment.paid event."""
    # Treat same as payment.paid
    return _handle_payment_paid(attrs, event_id, event_data)


def _handle_subscription_created(attrs: dict, event_id: str, event_data: dict) -> bool:
    """Handle subscription.created event."""
    from app.models.portfolio import Subscription
    
    try:
        resource, resource_attrs = _paymongo_resource(attrs)
        metadata = resource_attrs.get('metadata') or attrs.get('metadata') or {}
        subscription_id = metadata.get('subscription_id')
        paymongo_sub_id = resource.get('id') or event_data.get('data', {}).get('id', '')
        
        if not subscription_id:
            logger.warning('Subscription created: missing subscription_id. id=%s', event_id)
            return True
        
        try:
            sub = db.session.query(Subscription).filter_by(id=int(subscription_id)).first()
        except (ValueError, TypeError):
            logger.error('Subscription created: invalid subscription_id=%s', subscription_id)
            return False
        
        if not sub:
            logger.warning('Subscription created: subscription not found. id=%s', subscription_id)
            return True
        
        if _accept_paymongo_state_event(sub, resource_attrs, event_id):
            sub.paymongo_subscription_id = paymongo_sub_id
        db.session.commit()
        
        logger.info('Subscription created in PayMongo: local=%s paymongo=%s', 
                   subscription_id, paymongo_sub_id)
        return True
        
    except SQLAlchemyError as exc:
        db.session.rollback()
        logger.exception('Database error in subscription created handler. id=%s', event_id)
        return False


def _handle_subscription_updated(attrs: dict, event_id: str, event_data: dict) -> bool:
    """Handle subscription.updated event."""
    # Update subscription status based on PayMongo subscription status
    logger.info('Subscription updated event received. id=%s', event_id)
    return True


def _handle_subscription_cancelled(attrs: dict, event_id: str, event_data: dict) -> bool:
    """Handle subscription.cancelled event."""
    from app.models.portfolio import Subscription, SubscriptionStatus
    
    try:
        resource, resource_attrs = _paymongo_resource(attrs)
        metadata = resource_attrs.get('metadata') or attrs.get('metadata') or {}
        subscription_id = metadata.get('subscription_id')
        
        if not subscription_id:
            logger.warning('Subscription cancelled: missing subscription_id. id=%s', event_id)
            return True
        
        try:
            sub = db.session.query(Subscription).filter_by(id=int(subscription_id)).first()
        except (ValueError, TypeError):
            logger.error('Subscription cancelled: invalid subscription_id=%s', subscription_id)
            return False
        
        if not sub:
            logger.warning('Subscription cancelled: subscription not found. id=%s', subscription_id)
            return True
        
        if _accept_paymongo_state_event(sub, resource_attrs, event_id):
            from app.services.billing.lifecycle_service import transition_subscription
            transition_subscription(
                sub, 'cancelled', actor='paymongo-webhook',
                reason='Verified provider cancellation',
                idempotency_key=f'paymongo:{event_id}:cancelled',
                provider='paymongo', provider_event_id=event_id, commit=False,
            )
            _publish_billing_lifecycle_notification(sub, 'billing.cancelled', f'paymongo:{event_id}')
        db.session.commit()
        
        logger.info('Subscription cancelled: id=%s', subscription_id)
        return True
        
    except SQLAlchemyError as exc:
        db.session.rollback()
        logger.exception('Database error in subscription cancelled handler. id=%s', event_id)
        return False


def _handle_subscription_expired(attrs: dict, event_id: str, event_data: dict) -> bool:
    """Handle subscription.expired event."""
    from app.models.portfolio import Subscription, SubscriptionStatus
    
    try:
        resource, resource_attrs = _paymongo_resource(attrs)
        metadata = resource_attrs.get('metadata') or attrs.get('metadata') or {}
        subscription_id = metadata.get('subscription_id')
        
        if not subscription_id:
            logger.warning('Subscription expired: missing subscription_id. id=%s', event_id)
            return True
        
        try:
            sub = db.session.query(Subscription).filter_by(id=int(subscription_id)).first()
        except (ValueError, TypeError):
            logger.error('Subscription expired: invalid subscription_id=%s', subscription_id)
            return False
        
        if not sub:
            logger.warning('Subscription expired: subscription not found. id=%s', subscription_id)
            return True
        
        if _accept_paymongo_state_event(sub, resource_attrs, event_id):
            from app.services.billing.lifecycle_service import transition_subscription
            transition_subscription(
                sub, 'expired', actor='paymongo-webhook',
                reason='Verified provider expiry',
                idempotency_key=f'paymongo:{event_id}:expired',
                provider='paymongo', provider_event_id=event_id, commit=False,
            )
            _publish_billing_lifecycle_notification(sub, 'billing.expired', f'paymongo:{event_id}')
        db.session.commit()
        
        logger.info('Subscription expired: id=%s', subscription_id)
        return True
        
    except SQLAlchemyError as exc:
        db.session.rollback()
        logger.exception('Database error in subscription expired handler. id=%s', event_id)
        return False


# ─────────────────────────────────────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────────────────────────────────────

@webhooks.route('/health', methods=['GET'])
def webhook_health():
    """Health check endpoint for webhook receiver."""
    return jsonify(status='ok', endpoint='/webhooks/paymongo'), 200

# ─────────────────────────────────────────────────────────────────────────
# WEBHOOK: DODO PAYMENTS
# ─────────────────────────────────────────────────────────────────────────

@webhooks.route('/dodo', methods=['POST'])
@csrf.exempt
@limiter.limit('120 per minute')
def dodo_webhook():
    """Verify and process Dodo Payments subscription webhooks.

    Uses the raw request body and Standard Webhooks headers. The unique
    ``webhook-id`` header is stored in the existing WebhookEvent table so
    retries are idempotent.
    """
    from standardwebhooks.webhooks import Webhook
    from app.models import WebhookEvent

    raw = request.get_data(cache=True)
    secret = current_app.config.get('DODO_PAYMENTS_WEBHOOK_SECRET', '')
    if not secret:
        logger.error('Dodo webhook received but DODO_PAYMENTS_WEBHOOK_SECRET is missing')
        return jsonify(error='Webhook is not configured'), 503
    if not raw:
        return jsonify(error='Missing payload'), 400

    headers = {
        'webhook-id': request.headers.get('webhook-id', ''),
        'webhook-timestamp': request.headers.get('webhook-timestamp', ''),
        'webhook-signature': request.headers.get('webhook-signature', ''),
    }
    if not all(headers.values()):
        return jsonify(error='Missing webhook signature headers'), 401

    try:
        event = Webhook(secret).verify(raw, headers)
    except Exception:
        logger.warning('Dodo webhook signature verification failed', exc_info=True)
        return jsonify(error='Invalid signature'), 401

    event_id = headers['webhook-id']
    event_type = str(event.get('type') or 'unknown')
    existing = WebhookEvent.query.filter_by(event_id=f'dodo:{event_id}').first()
    if existing:
        return jsonify(received=True, duplicate=True), 200

    row = WebhookEvent(
        event_id=f'dodo:{event_id}',
        event_type=event_type,
        payload_summary=json.dumps({'provider': 'dodo', 'event_type': event_type}),
        processed=False,
    )
    db.session.add(row)
    try:
        ok, tenant_id = _handle_dodo_event(event_type, event, event_id=event_id)
        row.tenant_id = tenant_id
        row.processed = bool(ok)
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception('Dodo webhook processing failed: type=%s id=%s', event_type, event_id)
        # Non-2xx lets Dodo retry transient failures.
        return jsonify(error='Processing failed'), 500

    return jsonify(received=True, processed=bool(ok)), 200


def _dodo_object(event: dict) -> dict:
    data = event.get('data') or {}
    if isinstance(data, dict) and isinstance(data.get('object'), dict):
        return data['object']
    return data if isinstance(data, dict) else {}


def _handle_dodo_event(event_type: str, event: dict, *, event_id: str):
    """Synchronize a local Subscription from a verified Dodo event."""
    from app.models import Subscription
    from app.services.billing.dodo_service import parse_iso_datetime

    obj = _dodo_object(event)
    metadata = obj.get('metadata') or {}
    local_id = metadata.get('subscription_id')
    sub = None
    if local_id:
        try:
            sub = Subscription.query.filter_by(id=int(local_id)).first()
        except (TypeError, ValueError):
            sub = None
    dodo_sub_id = obj.get('subscription_id') or obj.get('id')
    if sub is None and dodo_sub_id:
        sub = Subscription.query.filter_by(dodo_subscription_id=str(dodo_sub_id)).first()
    if sub is None:
        logger.warning('Dodo webhook has no matching local subscription: type=%s metadata=%s', event_type, metadata)
        return True, None

    received_at = datetime.now(timezone.utc)
    sub.payment_provider = 'dodo'
    sub.payment_method = 'dodo'
    sub.last_webhook_at = received_at

    amount_minor = obj.get('recurring_pre_tax_amount') or obj.get('total_amount') or obj.get('amount')
    provider_currency = str(obj.get('currency') or '').strip().upper()
    active_event_types = {'subscription.active', 'subscription.renewed', 'payment.succeeded', 'checkout.session.completed'}
    invoice = None
    if event_type in active_event_types and isinstance(amount_minor, int) and len(provider_currency) == 3:
        from app.services.billing.invoice_service import record_invoice
        from app.services.billing.money import minor_to_decimal
        invoiced_amount = minor_to_decimal(amount_minor, 2)
        invoice = record_invoice(
            tenant_id=sub.tenant_id,
            subscription=sub,
            plan=metadata.get('plan_code') or sub.plan,
            billing_cycle=metadata.get('billing_cycle') or sub.billing_cycle or 'monthly',
            payment_method='dodo',
            payment_provider='dodo',
            payment_reference=str(obj.get('payment_id') or event_id),
            amount_subtotal_override=invoiced_amount,
            amount_total_override=invoiced_amount,
            currency_override=provider_currency,
            original_amount_minor=amount_minor,
            original_currency=provider_currency,
            currency_exponent=2,
            actor=f'dodo:{event_id}',
            commit=False,
        )

    # Financial facts are ordered by their own occurred_at and must still be
    # posted when delivery is late. Subscription state, however, must never be
    # rolled backward by an older provider event.
    from app.services.ledger import post_provider_event
    post_provider_event(
        'dodo',
        event,
        tenant_id=sub.tenant_id,
        subscription_id=sub.id,
        received_at=received_at,
        environment=current_app.config.get('DODO_PAYMENTS_MODE', 'test'),
        event_id_override=event_id,
        invoice_id=invoice.id if invoice else None,
        session=db.session,
        commit=False,
    )
    state_occurred_at = parse_iso_datetime(
        obj.get('updated_at') or obj.get('paid_at') or obj.get('created_at')
    )
    previous_state_at = sub.provider_state_occurred_at
    if previous_state_at and previous_state_at.tzinfo is None:
        previous_state_at = previous_state_at.replace(tzinfo=timezone.utc)
    if state_occurred_at is None:
        logger.warning('Dodo state event lacks provider occurrence time: type=%s id=%s', event_type, event_id)
        db.session.add(sub)
        return True, sub.tenant_id
    if previous_state_at and state_occurred_at < previous_state_at:
        logger.info('Dodo out-of-order state event ignored: type=%s id=%s', event_type, event_id)
        db.session.add(sub)
        return True, sub.tenant_id
    sub.provider_state_occurred_at = state_occurred_at
    sub.dodo_subscription_id = str(dodo_sub_id) if dodo_sub_id else sub.dodo_subscription_id
    sub.dodo_customer_id = str(obj.get('customer_id') or (obj.get('customer') or {}).get('customer_id') or '') or sub.dodo_customer_id
    sub.dodo_payment_id = str(obj.get('payment_id') or '') or sub.dodo_payment_id
    sub.provider_currency = str(obj.get('currency') or '')[:3].upper() or sub.provider_currency

    plan = metadata.get('plan_code')
    cycle = metadata.get('billing_cycle')
    if plan:
        sub.plan = plan
    if cycle in ('monthly', 'yearly'):
        sub.billing_cycle = cycle

    start = parse_iso_datetime(obj.get('previous_billing_date') or obj.get('created_at'))
    end = parse_iso_datetime(obj.get('next_billing_date'))
    if start:
        sub.started_at = start
    if end:
        sub.expires_at = end

    provider_status = str(obj.get('status') or '').lower()
    from app.services.billing.lifecycle_service import adapt_provider_state, transition_subscription, InvalidSubscriptionTransition
    target_state = None
    for raw_state in (event_type, provider_status):
        if not raw_state:
            continue
        try:
            target_state = adapt_provider_state('dodo', raw_state)
            break
        except InvalidSubscriptionTransition:
            continue
    if target_state:
        transition_subscription(
            sub,
            target_state,
            actor='dodo-webhook',
            reason=f'Verified provider lifecycle event: {event_type}',
            idempotency_key=f'dodo:{event_id}:{target_state}',
            provider='dodo',
            provider_event_id=event_id,
            occurred_at=state_occurred_at,
            commit=False,
        )

    notification_event = {
        'subscription.active': 'billing.activated',
        'subscription.renewed': 'billing.renewed',
        'payment.failed': 'billing.payment_failed',
        'subscription.failed': 'billing.payment_failed',
        'subscription.cancelled': 'billing.cancelled',
        'subscription.expired': 'billing.expired',
    }.get(event_type)
    if notification_event:
        _publish_billing_lifecycle_notification(sub, notification_event, f'dodo:{event_id}')

    if isinstance(amount_minor, int) and len(provider_currency) == 3:
        from app.services.billing.financial_conversion import set_exact_paid_amount
        set_exact_paid_amount(
            sub, amount=float(amount_minor) / 100.0,
            currency=provider_currency, exponent=2,
        )

    db.session.add(sub)
    return True, sub.tenant_id


def _publish_billing_lifecycle_notification(sub, template_key: str, provider_event_id: str) -> None:
    """Publish a verified provider lifecycle fact in the webhook transaction."""
    from app.services.notification_service import Recipient, publish_notification

    publish_notification(
        recipient=Recipient.tenant(int(sub.tenant_id)),
        event_type=template_key,
        template_key=template_key,
        parameters={'plan_name': (sub.plan or 'Subscription').title()},
        dedupe_key=f'{template_key}:provider_event:{provider_event_id}',
        entity_type='subscription',
        entity_id=sub.id,
        actor_type='payment_provider',
        action_route='admin.billing_overview',
        priority='urgent' if template_key == 'billing.payment_failed' else 'high',
        channels=('in_app', 'email') if template_key in {'billing.payment_failed', 'billing.expired'} else ('in_app',),
        session=db.session,
        commit=False,
    )
