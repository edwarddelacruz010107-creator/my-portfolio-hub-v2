"""
app/webhooks/__init__.py — PayMongo webhook handlers (v3.9 patched)

Patches applied:
  • BUG-015: Webhook now returns 200 on internal handler errors to prevent
             PayMongo retry storms. Internal errors are still logged at ERROR level.
  • Decorator ordering preserved (route innermost, correct for Flask).
"""

import logging

from flask import Blueprint, request, jsonify

from app import csrf, limiter

logger = logging.getLogger(__name__)

webhooks = Blueprint('webhooks', __name__, url_prefix='/webhooks')


@webhooks.route('/paymongo', methods=['POST'])
@csrf.exempt
@limiter.limit('120 per minute')
def paymongo_webhook():
    """Verify PayMongo HMAC signature, dispatch idempotent billing events."""
    try:
        # Read raw body BEFORE any JSON parsing — signature covers raw bytes
        payload = request.get_data()

        # PayMongo sends the signature in one of two header names
        signature = (
            request.headers.get('Paymongo-Signature', '')
            or request.headers.get('X-Paymongo-Signature', '')
        )

        if not signature:
            logger.warning(
                'PayMongo webhook received without signature from %s',
                request.remote_addr,
            )
            return jsonify(error='Missing signature'), 401

        from app.utils.paymongo import verify_webhook_signature, handle_payment_webhook

        if not verify_webhook_signature(payload, signature):
            logger.error(
                'PayMongo webhook HMAC verification failed — possible replay or wrong secret. '
                'Remote: %s  Signature: %.20s…',
                request.remote_addr,
                signature,
            )
            return jsonify(error='Invalid signature'), 401

        event_data = request.get_json(silent=True)
        if not event_data:
            logger.warning('PayMongo webhook: empty or non-JSON body after signature OK')
            return jsonify(error='Empty payload'), 400

        event_type = (event_data.get('data') or {}).get('attributes', {}).get('type', 'unknown')
        event_id   = (event_data.get('data') or {}).get('id', 'unknown')
        logger.info('PayMongo webhook accepted: type=%s id=%s', event_type, event_id)

        handle_payment_webhook(event_data)
        return jsonify(success=True, message='Event processed'), 200

    except Exception as exc:
        logger.error('PayMongo webhook unhandled error: %s', exc, exc_info=True)
        # BUG-015 FIX: Return 200 to prevent PayMongo retry storms on our bugs.
        # Internal errors are logged above. PayMongo will NOT retry on 200.
        return jsonify(success=False, message='Event received, internal error logged'), 200


@webhooks.route('/health', methods=['GET'])
def webhook_health():
    """Liveness probe for PayMongo webhook endpoint."""
    return jsonify(status='ok', endpoint='/webhooks/paymongo'), 200
