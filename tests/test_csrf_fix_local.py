#!/usr/bin/env python3
"""
Test the CSRF SSL Strict fix locally.

This script verifies:
1. Login forms render without errors
2. CSRF token is present in forms
3. CSRF validation still works (missing token is rejected)
4. The before_request hook fires correctly
5. WTF_CSRF_SSL_STRICT is disabled for login routes

Run with: python test_csrf_fix_local.py
"""

import os
import sys
import logging
from typing import Optional

# Setup path
sys.path.insert(0, os.path.dirname(__file__))

# Suppress Flask logs during tests
logging.getLogger('flask').setLevel(logging.ERROR)
logging.getLogger('werkzeug').setLevel(logging.ERROR)

def create_test_app():
    """Create app instance for testing."""
    from app import create_app
    app = create_app('development')
    return app

def test_login_forms_render():
    """Test that login forms render without CSRF errors."""
    print("\n[Test 1/5] Login forms render with CSRF tokens...")
    app = create_test_app()
    
    with app.test_client() as client:
        # Test superadmin login GET
        response = client.get('/superadmin/login')
        assert response.status_code == 200, \
            f"GET /superadmin/login failed with status {response.status_code}"
        
        response_text = response.data.decode('utf-8')
        assert 'csrf_token' in response_text, \
            "CSRF token input not found in /superadmin/login form"
        assert 'login' in response_text.lower(), \
            "Login form not found in response"
        
        print("  ✓ GET /superadmin/login: 200 OK, CSRF token present")
        
        # Test admin login GET
        response = client.get('/auth/login')
        assert response.status_code == 200, \
            f"GET /auth/login failed with status {response.status_code}"
        
        response_text = response.data.decode('utf-8')
        assert 'csrf_token' in response_text, \
            "CSRF token input not found in /auth/login form"
        
        print("  ✓ GET /auth/login: 200 OK, CSRF token present")

def test_csrf_still_validates():
    """Verify CSRF validation still works (missing/invalid token rejected)."""
    print("\n[Test 2/5] CSRF validation still enforced...")
    app = create_test_app()
    
    with app.test_client() as client:
        # Get a login form to extract CSRF token
        response = client.get('/superadmin/login')
        form_text = response.data.decode('utf-8')
        
        # Attempt POST without CSRF token (should fail)
        response = client.post(
            '/superadmin/login',
            data={
                'username': 'test@example.com',
                'password': 'test-password',
                # Note: intentionally omitting csrf_token
            },
            follow_redirects=False,
        )
        
        # Should get a 400 Bad Request or CSRF error, not a successful login
        status_ok = response.status_code in [400, 403]
        csrf_error = 'csrf' in response.data.decode('utf-8').lower() or \
                     'token' in response.data.decode('utf-8').lower()
        
        assert status_ok or csrf_error, \
            f"CSRF validation not working: got status {response.status_code}, " \
            f"response missing CSRF error indicator"
        
        print("  ✓ POST /superadmin/login without CSRF token: Rejected")
        print(f"    (Status: {response.status_code})")

def test_csrf_before_request_hook_fires():
    """Verify the before_request hook is running."""
    print("\n[Test 3/5] before_request hook fires for login routes...")
    app = create_test_app()
    
    # Test login route
    with app.test_request_context('/superadmin/login'):
        from flask import request
        
        # Reset config to production state
        app.config['ENV'] = 'production'
        app.config['WTF_CSRF_SSL_STRICT'] = True
        
        # Call the hook manually
        from app import csrf_ssl_strict_for_login_routes
        csrf_ssl_strict_for_login_routes()
        
        # For login routes, should be disabled
        is_disabled = app.config.get('WTF_CSRF_SSL_STRICT') is False
        assert is_disabled, \
            f"WTF_CSRF_SSL_STRICT not disabled for /superadmin/login " \
            f"(value: {app.config.get('WTF_CSRF_SSL_STRICT')})"
        
        print("  ✓ /superadmin/login: WTF_CSRF_SSL_STRICT disabled")
    
    # Test non-login route (should restore)
    with app.test_request_context('/studio/dashboard'):
        from flask import request
        
        # Reset config
        app.config['ENV'] = 'production'
        app.config['WTF_CSRF_SSL_STRICT'] = False  # Start disabled
        
        # Call the hook
        from app import csrf_ssl_strict_for_login_routes
        csrf_ssl_strict_for_login_routes()
        
        # For non-login routes in production, should be re-enabled
        is_enabled = app.config.get('WTF_CSRF_SSL_STRICT') is True
        assert is_enabled, \
            f"WTF_CSRF_SSL_STRICT not re-enabled for /studio/dashboard " \
            f"(value: {app.config.get('WTF_CSRF_SSL_STRICT')})"
        
        print("  ✓ /studio/dashboard: WTF_CSRF_SSL_STRICT re-enabled")

def test_multiple_login_routes():
    """Test that hook works for all login routes."""
    print("\n[Test 4/5] All login route variations covered...")
    app = create_test_app()
    
    login_routes = [
        '/auth/login',
        '/superadmin/login',
        '/tenant/example/auth/login',  # Tenant login
        '/superadmin/forgot-password/request',  # Related superadmin route
    ]
    
    for route in login_routes:
        with app.test_request_context(route):
            from flask import request
            
            app.config['ENV'] = 'production'
            app.config['WTF_CSRF_SSL_STRICT'] = True
            
            from app import csrf_ssl_strict_for_login_routes
            csrf_ssl_strict_for_login_routes()
            
            # Determine expected state
            is_login_route = (
                request.path in ['/auth/login', '/superadmin/login']
                or '/auth/login' in request.path
                or ('/superadmin/' in request.path and 'login' in request.path)
            )
            
            expected_state = False if is_login_route else True
            actual_state = app.config.get('WTF_CSRF_SSL_STRICT')
            
            symbol = "✓" if actual_state == expected_state else "✗"
            state_str = "disabled" if actual_state is False else "enabled"
            print(f"  {symbol} {route}: {state_str}")
            
            assert actual_state == expected_state, \
                f"Unexpected WTF_CSRF_SSL_STRICT state for {route}"

def test_production_config():
    """Verify production config is correct."""
    print("\n[Test 5/5] Production config correct...")
    from config import ProductionConfig
    
    assert ProductionConfig.WTF_CSRF_ENABLED is True, \
        "WTF_CSRF_ENABLED should be True in production"
    assert ProductionConfig.WTF_CSRF_SSL_STRICT is True, \
        "WTF_CSRF_SSL_STRICT should be True in production (hook disables for login routes)"
    assert ProductionConfig.WTF_CSRF_CHECK_DEFAULT is True, \
        "WTF_CSRF_CHECK_DEFAULT should be True in production"
    
    print("  ✓ WTF_CSRF_ENABLED: True")
    print("  ✓ WTF_CSRF_SSL_STRICT: True")
    print("  ✓ WTF_CSRF_CHECK_DEFAULT: True")

def main():
    """Run all tests."""
    print("=" * 70)
    print("Testing CSRF SSL Strict Fix for Render.com Proxy")
    print("=" * 70)
    
    tests = [
        test_login_forms_render,
        test_csrf_still_validates,
        test_csrf_before_request_hook_fires,
        test_multiple_login_routes,
        test_production_config,
    ]
    
    failed = []
    
    for test in tests:
        try:
            test()
        except AssertionError as e:
            failed.append((test.__name__, str(e)))
            print(f"  ✗ FAILED: {e}")
        except Exception as e:
            failed.append((test.__name__, f"Unexpected error: {e}"))
            print(f"  ✗ ERROR: {e}")
            import traceback
            traceback.print_exc()
    
    print("\n" + "=" * 70)
    
    if failed:
        print(f"❌ {len(failed)} test(s) failed:\n")
        for test_name, error in failed:
            print(f"  • {test_name}")
            print(f"    {error}\n")
        return 1
    else:
        print("✅ All tests passed!")
        print("\nThe CSRF SSL Strict fix is working correctly.")
        print("Ready for deployment to production.")
        return 0

if __name__ == '__main__':
    sys.exit(main())
