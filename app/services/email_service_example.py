"""
OPTIONAL: Email Service Integration Example

This shows how to update your email dispatch service to use per-tenant MailerSend.
If you have a separate email service module, apply these patterns to it.

Location: app/services/email_service.py (or similar)
"""

from typing import Optional, Dict
import requests
from app.models.core import TenantCommunicationSettings, GlobalEmailConfig
from app.models.tenant_data import Profile


class EmailServiceError(Exception):
    """Base exception for email service errors."""
    pass


class MailerSendError(EmailServiceError):
    """MailerSend-specific errors."""
    pass


def get_effective_mailersend_config(tenant_id: int) -> Dict[str, str]:
    """Resolve the MailerSend config to use for a tenant.
    
    Priority order:
      1. Per-tenant MailerSend (highest priority)
      2. Global MailerSend
      3. None (raise error)
    
    Args:
        tenant_id: Tenant ID to get config for.
        
    Returns:
        Dict with 'api_key', 'from_email', 'from_name'.
        
    Raises:
        EmailServiceError: If no MailerSend config found.
    """
    # Check per-tenant config first
    from app import db
    comm = TenantCommunicationSettings.query.filter_by(tenant_id=tenant_id).first()
    
    if comm and comm.has_mailersend:
        return comm.effective_mailersend_config()
    
    # Fall back to global config
    global_cfg = GlobalEmailConfig.get()
    if global_cfg.has_mailersend:
        return {
            'api_key': global_cfg.mailersend_api_key,
            'from_email': global_cfg.sender_email,
            'from_name': global_cfg.sender_name,
        }
    
    raise EmailServiceError(
        f'No MailerSend configuration found for tenant {tenant_id}. '
        'Configure either per-tenant or global MailerSend settings.'
    )


def send_email_via_mailersend(
    to_email: str,
    subject: str,
    body_html: str,
    body_text: Optional[str] = None,
    from_email: Optional[str] = None,
    from_name: Optional[str] = None,
    api_key: Optional[str] = None,
    reply_to: Optional[str] = None,
) -> Dict:
    """Send email via MailerSend API.
    
    Args:
        to_email: Recipient email address.
        subject: Email subject line.
        body_html: HTML email body.
        body_text: Plain text email body (optional).
        from_email: Sender email (required if api_key provided).
        from_name: Sender display name (required if api_key provided).
        api_key: MailerSend API key (required).
        reply_to: Reply-To email address (optional).
        
    Returns:
        MailerSend response dict (includes message_id if successful).
        
    Raises:
        MailerSendError: If API request fails.
    """
    if not api_key or not from_email or not from_name:
        raise MailerSendError(
            'API key, from_email, and from_name are required for MailerSend.'
        )
    
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }
    
    # MailerSend API v1 endpoint
    url = 'https://api.mailersend.com/v1/email'
    
    # Build request payload
    payload = {
        'from': {
            'email': from_email,
            'name': from_name,
        },
        'to': [
            {
                'email': to_email,
            }
        ],
        'subject': subject,
        'html': body_html,
    }
    
    if body_text:
        payload['text'] = body_text
    
    if reply_to:
        payload['reply_to'] = {
            'email': reply_to,
        }
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        
        # MailerSend returns 202 Accepted on success
        if response.status_code in (200, 202):
            return {
                'success': True,
                'message_id': response.json().get('message_id'),
                'status_code': response.status_code,
            }
        else:
            error_body = response.json()
            raise MailerSendError(
                f'MailerSend API error: {error_body.get("message", "Unknown error")}'
            )
    
    except requests.RequestException as e:
        raise MailerSendError(f'Failed to send email via MailerSend: {str(e)}')
    except ValueError as e:
        # JSON decode error
        raise MailerSendError(f'Invalid response from MailerSend API: {str(e)}')


def send_tenant_email(
    tenant_id: int,
    to_email: str,
    subject: str,
    body_html: str,
    body_text: Optional[str] = None,
    reply_to: Optional[str] = None,
) -> Dict:
    """Send email for a tenant using their configured email provider.
    
    This is the main function to use in application code.
    It handles provider selection and fallback logic.
    
    Args:
        tenant_id: Tenant ID for context.
        to_email: Recipient email address.
        subject: Email subject line.
        body_html: HTML email body.
        body_text: Plain text email body (optional).
        reply_to: Reply-To email address (optional).
        
    Returns:
        Dict with 'success' bool and other metadata.
        
    Raises:
        EmailServiceError: If email sending fails.
        
    Example:
        >>> result = send_tenant_email(
        ...     tenant_id=1,
        ...     to_email='user@example.com',
        ...     subject='Welcome!',
        ...     body_html='<p>Welcome to our platform</p>',
        ... )
        >>> if result['success']:
        ...     print(f"Email sent: {result['message_id']}")
    """
    # Get the MailerSend config to use (with fallback logic)
    try:
        mailersend_config = get_effective_mailersend_config(tenant_id)
    except EmailServiceError as e:
        raise EmailServiceError(f'Cannot send email for tenant {tenant_id}: {str(e)}')
    
    # Send via MailerSend
    try:
        result = send_email_via_mailersend(
            to_email=to_email,
            subject=subject,
            body_html=body_html,
            body_text=body_text,
            from_email=mailersend_config['from_email'],
            from_name=mailersend_config['from_name'],
            api_key=mailersend_config['api_key'],
            reply_to=reply_to,
        )
        return result
    
    except MailerSendError as e:
        # Log error for debugging
        print(f'[EMAIL ERROR] Tenant {tenant_id}: {str(e)}')
        raise EmailServiceError(f'Failed to send email: {str(e)}')


def send_password_reset_email(
    tenant_id: int,
    user_email: str,
    reset_token: str,
    frontend_url: str = 'https://yourdomain.com',
) -> Dict:
    """Send password reset email to a tenant user.
    
    This is an example of using send_tenant_email() in application code.
    
    Args:
        tenant_id: Tenant ID.
        user_email: User's email address.
        reset_token: Password reset token (signed with itsdangerous).
        frontend_url: Base URL for reset link.
        
    Returns:
        Dict with send result.
    """
    reset_url = f'{frontend_url}/auth/reset-password?token={reset_token}'
    
    html_body = f'''
    <html>
    <body style="font-family: Arial, sans-serif;">
        <p>Hello,</p>
        <p>You requested a password reset. Click the link below to proceed:</p>
        <p><a href="{reset_url}" style="background: #0066cc; color: white; padding: 10px 20px; text-decoration: none; border-radius: 4px; display: inline-block;">
            Reset Password
        </a></p>
        <p>Or copy this link: <code>{reset_url}</code></p>
        <p>This link expires in 24 hours.</p>
        <p>If you didn't request this, ignore this email.</p>
    </body>
    </html>
    '''
    
    text_body = f'''
    Password Reset Request
    
    Click the link below to reset your password:
    {reset_url}
    
    Or copy the link above into your browser.
    This link expires in 24 hours.
    
    If you didn't request this, ignore this email.
    '''
    
    return send_tenant_email(
        tenant_id=tenant_id,
        to_email=user_email,
        subject='Password Reset Request',
        body_html=html_body,
        body_text=text_body,
    )


def send_welcome_email(
    tenant_id: int,
    user_email: str,
    user_name: str,
    frontend_url: str = 'https://yourdomain.com',
) -> Dict:
    """Send welcome email to a new tenant user.
    
    Args:
        tenant_id: Tenant ID.
        user_email: User's email address.
        user_name: User's display name.
        frontend_url: Base URL for links.
        
    Returns:
        Dict with send result.
    """
    html_body = f'''
    <html>
    <body style="font-family: Arial, sans-serif;">
        <h2>Welcome, {user_name}!</h2>
        <p>Your account has been created successfully.</p>
        <p><a href="{frontend_url}/login" style="background: #0066cc; color: white; padding: 10px 20px; text-decoration: none; border-radius: 4px; display: inline-block;">
            Sign In
        </a></p>
        <p>If you have any questions, reach out to support.</p>
    </body>
    </html>
    '''
    
    text_body = f'''
    Welcome, {user_name}!
    
    Your account has been created successfully.
    
    Sign in at: {frontend_url}/login
    
    If you have any questions, reach out to support.
    '''
    
    return send_tenant_email(
        tenant_id=tenant_id,
        to_email=user_email,
        subject='Welcome to Our Platform',
        body_html=html_body,
        body_text=text_body,
    )


# ════════════════════════════════════════════════════════════════════════════
# INTEGRATION EXAMPLE IN APPLICATION ROUTES
# ════════════════════════════════════════════════════════════════════════════

"""
Example: Using the email service in a Flask route:

    from app.services.email_service import send_password_reset_email, EmailServiceError
    
    @auth.route('/forgot-password', methods=['POST'])
    def forgot_password():
        email = request.form.get('email')
        user = User.query.filter_by(email=email).first()
        
        if not user:
            flash('If email exists, reset link will be sent.', 'info')
            return redirect(url_for('auth.login'))
        
        # Generate reset token
        reset_token = generate_password_reset_token(user.id)
        
        # Send email
        try:
            result = send_password_reset_email(
                tenant_id=user.tenant_id,
                user_email=user.email,
                reset_token=reset_token,
            )
            
            if result.get('success'):
                flash('Password reset link sent to your email.', 'success')
            else:
                flash('Failed to send email. Please try again.', 'danger')
        
        except EmailServiceError as e:
            # Log error, notify admin
            print(f'Email service error: {e}')
            flash('Email service temporarily unavailable. Please try again later.', 'danger')
        
        return redirect(url_for('auth.login'))
"""

# ════════════════════════════════════════════════════════════════════════════
# TESTING
# ════════════════════════════════════════════════════════════════════════════

"""
Test the email service:

    # Unit test
    def test_send_email_with_per_tenant_config():
        # Setup: Create tenant with MailerSend config
        tenant = Tenant(id=1)
        comm = TenantCommunicationSettings(
            tenant_id=1,
            mailersend_api_key='sk_live_...',  # Will be encrypted
            mailersend_from_email='noreply@example.com',
            mailersend_from_name='My App',
        )
        db.session.add(tenant)
        db.session.add(comm)
        db.session.commit()
        
        # Test: Send email
        result = send_tenant_email(
            tenant_id=1,
            to_email='test@example.com',
            subject='Test',
            body_html='<p>Test email</p>',
        )
        
        assert result['success']
        assert 'message_id' in result
    
    # Integration test
    def test_fallback_to_global_config():
        # Setup: No per-tenant config, only global
        global_cfg = GlobalEmailConfig.get()
        global_cfg.mailersend_api_key = 'sk_live_...'
        global_cfg.sender_email = 'noreply@example.com'
        global_cfg.sender_name = 'Global App'
        db.session.commit()
        
        # Test: Send email (should use global config)
        result = send_tenant_email(
            tenant_id=999,  # Tenant with no config
            to_email='test@example.com',
            subject='Test',
            body_html='<p>Test email</p>',
        )
        
        assert result['success']
"""
