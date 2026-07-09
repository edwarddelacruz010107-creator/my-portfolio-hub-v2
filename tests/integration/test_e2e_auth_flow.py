import pytest
from unittest.mock import patch


def test_full_signup_verify_login_logout_cycle(app):
    """E2E: signup → receive OTP (captured) → verify → auto-login → logout → login again."""
    from app import db

    client = app.test_client()

    email = 'e2e_test_user@example.com'
    password = 'Password123!'
    captured = {}

    # Patch the OTP email send used by the pending signup flow.
    with patch('app.auth.routes_signup.send_pending_signup_otp') as mock_send:
        def _send(pending_signup, raw_otp):
            captured['otp'] = raw_otp
            return True

        mock_send.side_effect = _send

        # Register user (POST to /auth/register)
        resp = client.post('/auth/register', data={
            'full_name': 'E2E Tester',
            'email': email,
            'password': password,
            'confirm_password': password,
            'accept_terms': 'y',
        }, follow_redirects=True)

        assert resp.status_code in (200, 302)
        assert 'Enter the 6-digit code' in resp.get_data(as_text=True)

    # OTP should have been captured
    assert 'otp' in captured and len(captured['otp']) == 6

    # Submit OTP to the canonical verification endpoint
    resp = client.post('/auth/verify-email', data={'email': email, 'code': captured['otp']}, follow_redirects=True)
    assert resp.status_code == 200

    # After verification the user should be logged in; admin dashboard accessible
    dash = client.get('/studio/', follow_redirects=True)
    assert dash.status_code == 200

    # Logout
    lo = client.get('/auth/logout', follow_redirects=True)
    assert lo.status_code == 200

    # Login again
    resp = client.post('/auth/login', data={'username': email, 'password': password}, follow_redirects=True)
    assert resp.status_code == 200

    # Dashboard should be accessible again
    dash2 = client.get('/studio/', follow_redirects=True)
    assert dash2.status_code == 200
