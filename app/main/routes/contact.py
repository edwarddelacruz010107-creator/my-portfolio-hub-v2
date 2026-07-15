from flask import Blueprint, request, jsonify, current_app
from app.services.communication.contact_service import process_contact_submission
from app.extensions import limiter

bp = Blueprint('contact', __name__)


@bp.route('/contact/submit', methods=['POST'])
@limiter.limit('5 per 15 minutes')
def submit_contact():
    data = request.form.to_dict() or request.get_json() or {}

    # Honeypot field
    if data.get('website'):
        # silently drop
        return jsonify({'status': 'ok'}), 200

    # Prepare fields for the central contact pipeline
    name = data.get('name') or data.get('full_name', '')
    from app.request_security import get_client_ip
    ip_address = get_client_ip()
    user_agent = request.headers.get('User-Agent')

    tenant_slug = 'default'  # landing page always targets default tenant

    result = process_contact_submission(
        tenant_slug=tenant_slug,
        name=name,
        email=data.get('email', ''),
        subject=data.get('subject', ''),
        message=data.get('message', ''),
        phone=data.get('phone', ''),
        company=data.get('company', ''),
        source='legacy_contact',
        ip_address=ip_address,
        user_agent=user_agent,
        submission_id=(data.get('submission_id') or request.headers.get('X-Request-Id') or '')[:80] or None,
    )

    status_code = 201 if result.success else 400
    return jsonify({'status': 'ok' if result.success else 'error', 'message': result.user_message, 'inquiry_id': result.inquiry_id}), status_code
