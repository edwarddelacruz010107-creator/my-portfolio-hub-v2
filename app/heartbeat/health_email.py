"""
app/heartbeat/health_email.py — /health/email endpoint (v5.7)

Register this blueprint in create_app():

    from app.heartbeat.health_email import health_email_bp
    app.register_blueprint(health_email_bp)

Access control mirrors the existing /health endpoint:
  • Bearer token (HEARTBEAT_SECRET) → full JSON report
  • Authenticated superadmin session → full JSON report
  • All other callers → {"status": "ok"} (anti-information-leak)
"""
from __future__ import annotations

import os

from flask import Blueprint, jsonify, request
from flask_login import current_user

health_email_bp = Blueprint('health_email', __name__)


@health_email_bp.route('/health/email', methods=['GET'])
def health_email():
    """
    JSON health report for the email subsystem.

    Public response (unauthenticated):
        {"status": "ok"}

    Authenticated response (full):
        {
          "status": "ok" | "degraded" | "critical",
          "primary": "smtp" | "mailersend",
          "fallback": "mailersend" | "none",
          "smtp": {
            "enabled": true,
            "configured": true,
            "host": "smtp.example.com",
            "port": 587,
            "status": "ok" | "error: <type>"
          },
          "mailersend": {
            "configured": true,
            "key_length": 68,
            "status": "configured" | "not_configured" | "error: ..."
          },
          "warnings": []
        }
    """
    # ── Access gate (mirrors /health behaviour) ───────────────────────
    heartbeat_secret = os.environ.get('HEARTBEAT_SECRET', '')
    auth_header      = request.headers.get('Authorization', '')
    bearer_valid     = bool(heartbeat_secret and auth_header == f'Bearer {heartbeat_secret}')
    superadmin_ok    = (
        hasattr(current_user, 'is_authenticated')
        and current_user.is_authenticated
        and getattr(current_user, 'is_superadmin', False)
    )

    if not bearer_valid and not superadmin_ok:
        return jsonify({'status': 'ok'}), 200

    # ── Full report ───────────────────────────────────────────────────
    from app.services.email_service import EmailService
    svc      = EmailService()
    health   = svc.health_check()
    warnings = svc.validate_configuration()

    smtp_ok = health['smtp']['status'] == 'ok'
    ms_ok   = health['mailersend']['status'] == 'configured'

    if smtp_ok or ms_ok:
        overall = 'ok'
    elif not smtp_ok and ms_ok:
        overall = 'degraded'  # primary down, fallback available
    else:
        overall = 'critical'  # both providers down

    payload = {
        'status':     overall,
        'primary':    health['primary'],
        'fallback':   health['fallback'],
        'smtp':       health['smtp'],
        'mailersend': health['mailersend'],
        'warnings':   warnings,
    }

    http_status = 200 if overall != 'critical' else 503
    return jsonify(payload), http_status