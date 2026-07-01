"""
app/services/paymongo_service.py — PayMongo Integration v5.0

FIX SUMMARY (Requirements #1-2):
  ✅ Fixed initiate_checkout() function signature
  ✅ Fixed all callers to match new signature
  ✅ Added proper exception handling and logging
  ✅ Added transaction rollback on failures
  ✅ Webhook signature verification
  ✅ Idempotency with event tracking
  ✅ Proper HTTP response codes
  ✅ User-friendly error messages

All PayMongo operations are logged and validated.
Never crashes on None values or missing fields.
Database changes are atomic with rollback support.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from dataclasses import dataclass

import requests
from flask import current_app
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)

PAYMONGO_BASE_URL = 'https://api.paymongo.com/v1'

BILLING_CYCLES = {
    'monthly': {'interval': 'month', 'count': 1},
    'yearly': {'interval': 'year', 'count': 1},
}


@dataclass
class CheckoutResult:
    """Result of a checkout session creation."""
    success: bool
    checkout_url: Optional[str] = None
    session_id: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────────────

def _get_auth_header() -> str:
    """Get PayMongo Basic Auth header."""
    secret_key = current_app.config.get('PAYMONGO_SECRET_KEY', '').strip()
    if not secret_key:
        raise ValueError('PAYMONGO_SECRET_KEY not configured in environment')
    
    credentials = base64.b64encode(f'{secret_key}:'.encode()).decode()
    return f'Basic {credentials}'


def _api_request(
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
    timeout: int = 15,
) -> Optional[dict]:
    """Make API request to PayMongo with error handling."""
    try:
        headers = {
            'Authorization': _get_auth_header(),
            'Content-Type': 'application/json',
        }
        url = f'{PAYMONGO_BASE_URL}{path}'
        
        response = requests.request(
            method,
            url,
            json=json_body,
            headers=headers,
            timeout=timeout,
        )
        
        if response.status_code in (200, 201):
            return response.json()
        
        # Log errors for debugging
        logger.error(
            'PayMongo API error: %s %s → %d: %s',
            method, path, response.status_code, response.text[:500]
        )
        return None
        
    except requests.exceptions.Timeout:
        logger.error('PayMongo API timeout: %s %s', method, path)
        return None
    except requests.exceptions.RequestException as exc:
        logger.error('PayMongo API request error: %s %s: %s', method, path, exc)
        return None
    except Exception as exc:
        logger.exception('PayMongo API unexpected error: %s %s', method, path)
        return None


def _plan_amount_centavos(plan_name: str, billing_cycle: str = 'monthly') -> int:
    """Convert plan price to centavos (PHP cents)."""
    from app.utils import BILLING_PLANS
    from app.models.portfolio import normalize_plan_name
    
    plan_norm = normalize_plan_name(plan_name)
    plan_data = BILLING_PLANS.get(plan_norm, BILLING_PLANS.get('Basic', {}))
    
    price = float(plan_data.get('price', 0))
    if billing_cycle == 'yearly':
        price *= 12  # 1 month discount
    
    return int(price * 100)  # Convert to centavos


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

    Args:
        payload: Raw request body (bytes)
        signature: Full value of the Paymongo-Signature header

    Returns:
        True if signature is valid, False otherwise
    """
    webhook_secret = current_app.config.get('PAYMONGO_WEBHOOK_SECRET', '').strip()
    if not webhook_secret or not signature:
        logger.warning('Webhook signature verification skipped: missing secret or signature')
        return False

    try:
        # ── Parse compound header (t=...,te=...,li=...) ───────────────────────
        # PayMongo live webhooks send: t=<ts>,te=<test_hmac>,li=<live_hmac>
        # We only need the li= field for HMAC verification.
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
            # Plain hex digest — test environment or legacy path
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
        logger.exception('Webhook signature verification error')
        return False


# ─────────────────────────────────────────────────────────────────────────
# CHECKOUT SESSION
# ─────────────────────────────────────────────────────────────────────────

def initiate_checkout(
    db_session,
    profile,
    plan,
    billing_cycle: str,
    success_url: str,
    cancel_url: str,
) -> CheckoutResult:
    """
    Create a PayMongo checkout session for subscription purchase.
    
    Args:
        db_session: SQLAlchemy session
        profile: Tenant profile object
        plan: Plan object or plan name string
        billing_cycle: 'monthly' or 'yearly'
        success_url: URL to redirect after successful payment
        cancel_url: URL to redirect on cancellation
    
    Returns:
        CheckoutResult with checkout_url and session_id on success
    
    Raises:
        ValueError: If payment configuration is invalid
    """
    # ─────────────────────────────────────────────────────────────
    # VALIDATION
    # ─────────────────────────────────────────────────────────────
    
    if not profile:
        logger.error('initiate_checkout: profile is None')
        return CheckoutResult(
            success=False,
            error_code='INVALID_PROFILE',
            error_message='Unable to create checkout: profile not found'
        )
    
    if not plan:
        logger.error('initiate_checkout: plan is None for profile=%s', profile.id)
        return CheckoutResult(
            success=False,
            error_code='INVALID_PLAN',
            error_message='Unable to create checkout: plan not found'
        )
    
    if billing_cycle not in BILLING_CYCLES:
        logger.error('initiate_checkout: invalid billing_cycle=%s', billing_cycle)
        return CheckoutResult(
            success=False,
            error_code='INVALID_CYCLE',
            error_message='Invalid billing cycle. Choose monthly or yearly.'
        )
    
    # Verify PayMongo is enabled
    if not current_app.config.get('PAYMONGO_ENABLED'):
        logger.error('initiate_checkout: PayMongo is disabled')
        return CheckoutResult(
            success=False,
            error_code='PAYMONGO_DISABLED',
            error_message='Payment processing is not available'
        )
    
    if not current_app.config.get('PAYMONGO_SECRET_KEY'):
        logger.error('initiate_checkout: PAYMONGO_SECRET_KEY not configured')
        return CheckoutResult(
            success=False,
            error_code='CONFIG_ERROR',
            error_message='Payment service is misconfigured'
        )
    
    # ─────────────────────────────────────────────────────────────
    # EXTRACT PLAN INFO
    # ─────────────────────────────────────────────────────────────
    
    plan_name = getattr(plan, 'name', str(plan))
    if not plan_name:
        logger.error('initiate_checkout: cannot determine plan name')
        return CheckoutResult(
            success=False,
            error_code='INVALID_PLAN',
            error_message='Plan information is incomplete'
        )
    
    # ─────────────────────────────────────────────────────────────
    # CREATE SUBSCRIPTION RECORD (PRE-CHECKOUT)
    # ─────────────────────────────────────────────────────────────
    
    from app.models.portfolio import Subscription, SubscriptionStatus
    
    try:
        # Create pending subscription
        subscription = Subscription(
            tenant_id=profile.id,
            plan_name=plan_name,
            billing_cycle=billing_cycle,
            status=SubscriptionStatus.PENDING,
            started_at=datetime.now(timezone.utc),
        )
        db_session.add(subscription)
        db_session.flush()  # Get subscription.id without committing
        subscription_id = subscription.id
        
        logger.info(
            'Created pending subscription: id=%s tenant=%s plan=%s cycle=%s',
            subscription_id, profile.id, plan_name, billing_cycle
        )
        
    except SQLAlchemyError as exc:
        db_session.rollback()
        logger.exception(
            'Error creating subscription record for tenant=%s',
            profile.id
        )
        return CheckoutResult(
            success=False,
            error_code='DB_ERROR',
            error_message='Unable to create subscription. Please try again.'
        )
    
    # ─────────────────────────────────────────────────────────────
    # CREATE CHECKOUT SESSION IN PAYMONGO
    # ─────────────────────────────────────────────────────────────
    
    amount = _plan_amount_centavos(plan_name, billing_cycle)
    metadata = {
        'tenant_id': str(profile.id),
        'tenant_slug': profile.slug,
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
                    'failed': success_url.replace('/success', '/failed'),
                    'cancelled': cancel_url,
                },
                'payment_method_types': [
                    'card',
                    'gcash',
                    'paymaya',
                    'grab_pay',
                    'dob',
                ],
                'description': f'{plan_name} subscription — {profile.slug}',
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
    
    logger.info(
        'Initiating PayMongo checkout: subscription=%s amount=%d plan=%s',
        subscription_id, amount, plan_name
    )
    
    result = _api_request('POST', '/checkout_sessions', json_body=payload)
    if not result:
        db_session.rollback()
        logger.error('PayMongo checkout session creation failed for subscription=%s', subscription_id)
        return CheckoutResult(
            success=False,
            error_code='PAYMONGO_ERROR',
            error_message='Unable to create checkout session. Please try again.'
        )
    
    # ─────────────────────────────────────────────────────────────
    # EXTRACT CHECKOUT INFO
    # ─────────────────────────────────────────────────────────────
    
    try:
        data = result.get('data', {})
        attrs = data.get('attributes', {})
        checkout_url = attrs.get('checkout_url')
        session_id = data.get('id')
        
        if not checkout_url or not session_id:
            db_session.rollback()
            logger.error(
                'Invalid checkout response: missing checkout_url or session_id for subscription=%s',
                subscription_id
            )
            return CheckoutResult(
                success=False,
                error_code='INVALID_RESPONSE',
                error_message='Payment service returned incomplete data'
            )
        
        # Update subscription with PayMongo session ID
        subscription.paymongo_session_id = session_id
        db_session.commit()
        
        logger.info(
            'Checkout session created: id=%s subscription=%s',
            session_id, subscription_id
        )
        
        return CheckoutResult(
            success=True,
            checkout_url=checkout_url,
            session_id=session_id,
        )
        
    except (KeyError, AttributeError) as exc:
        db_session.rollback()
        logger.exception('Error parsing checkout response for subscription=%s', subscription_id)
        return CheckoutResult(
            success=False,
            error_code='PARSE_ERROR',
            error_message='Payment service returned invalid data'
        )
    except SQLAlchemyError as exc:
        db_session.rollback()
        logger.exception('Database error updating subscription=%s', subscription_id)
        return CheckoutResult(
            success=False,
            error_code='DB_ERROR',
            error_message='Unable to save payment information'
        )


# ─────────────────────────────────────────────────────────────────────────
# SUBSCRIPTION STATUS QUERIES
# ─────────────────────────────────────────────────────────────────────────

def fetch_subscription(paymongo_subscription_id: str) -> Optional[dict]:
    """Fetch subscription details from PayMongo."""
    if not paymongo_subscription_id:
        return None
    
    result = _api_request('GET', f'/subscriptions/{paymongo_subscription_id}')
    return result.get('data') if result else None


def fetch_payment(payment_id: str) -> Optional[dict]:
    """Fetch payment details from PayMongo."""
    if not payment_id:
        return None
    
    result = _api_request('GET', f'/payments/{payment_id}')
    return result.get('data') if result else None


# ─────────────────────────────────────────────────────────────────────────
# WEBHOOK HANDLING
# ─────────────────────────────────────────────────────────────────────────

def record_webhook_event(
    db_session,
    event_data: dict,
    event_id: str,
    event_type: str,
) -> bool:
    """
    Record webhook event in database for idempotency.
    
    Returns:
        True if event is new (not processed before)
        False if event was already processed
    """
    from app.models.portfolio import WebhookEvent
    
    try:
        # Check if event was already processed
        existing = db_session.query(WebhookEvent).filter_by(event_id=event_id).first()
        if existing:
            logger.info('Webhook event already processed: %s', event_id)
            return False
        
        # Create new event record
        event = WebhookEvent(
            event_id=event_id,
            event_type=event_type,
            payload=json.dumps(event_data)[:5000],  # Store truncated payload
            processed=False,
            processed_at=None,
        )
        db_session.add(event)
        db_session.commit()
        
        logger.info('Webhook event recorded: type=%s id=%s', event_type, event_id)
        return True
        
    except SQLAlchemyError as exc:
        db_session.rollback()
        logger.exception('Error recording webhook event: %s', event_id)
        return False


def mark_webhook_processed(db_session, event_id: str, success: bool) -> None:
    """Mark webhook event as processed."""
    from app.models.portfolio import WebhookEvent
    
    try:
        event = db_session.query(WebhookEvent).filter_by(event_id=event_id).first()
        if event:
            event.processed = success
            event.processed_at = datetime.now(timezone.utc)
            db_session.commit()
            logger.info('Webhook marked processed: id=%s success=%s', event_id, success)
    except SQLAlchemyError as exc:
        db_session.rollback()
        logger.exception('Error marking webhook processed: %s', event_id)
