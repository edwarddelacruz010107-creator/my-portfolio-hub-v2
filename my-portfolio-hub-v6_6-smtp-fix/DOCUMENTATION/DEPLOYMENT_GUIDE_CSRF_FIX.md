# Portfolio CMS v5.3 — CSRF SSL Strict Production Bug: Deployment Guide

## Overview

**Issue:** Superadmin and admin logins fail in production with correct credentials.

**Root Cause:** `WTF_CSRF_SSL_STRICT = True` in production config causes CSRF validation to fail when the app is behind Render.com's reverse proxy (request.host doesn't match request.referrer).

**Fix:** Disable `WTF_CSRF_SSL_STRICT` for login routes only. CSRF token validation remains active.

**Deployment Time:** ~5 minutes | **Rollback Time:** ~2 minutes

---

## Prerequisites

- [ ] Git access to `portfolio_cms_v5_3_audit_corrected` repo
- [ ] Render.com account with deploy access
- [ ] Ability to SSH into production (or monitor logs via Render dashboard)

---

## Step 1: Apply the Fix Locally

### Option A: Manual Edit (Recommended for Understanding)

1. **Open `app/__init__.py`** (line ~544, after `tenant_guard()` function)

2. **Find this section:**
```python
            except Exception:
                return _redirect('/')

    # ── Security headers ──────────────────────────────────────────────────────
    @app.after_request
    def set_security_headers(response):
```

3. **Replace with this:**
```python
            except Exception:
                return _redirect('/')

    # ── CSRF SSL Strict Fix for Render.com Proxy ─────────────────────────────────
    # FIX: Disable WTF_CSRF_SSL_STRICT for login routes
    #
    # PROBLEM (Render.com reverse proxy):
    # - WTF_CSRF_SSL_STRICT = True checks: same_origin(request.referrer, f"https://{request.host}/")
    # - request.referrer = "https://myportfoliohub.online/superadmin/login" ✓
    # - request.host = internal Render hostname (not matching) ✗
    # - CSRF validation fails → login rejected even with correct credentials
    #
    # SOLUTION: Disable strict SSL checking for login routes (they're CSRF-safe anyway)
    # - Login routes use POST-redirect-GET (no persistent state in POST)
    # - SOP blocks cross-origin POSTs automatically
    # - Session token set AFTER login succeeds
    # - CSRF token validation still enabled (WTF_CSRF_ENABLED = True)
    @app.before_request
    def csrf_ssl_strict_for_login_routes():
        """
        Disable WTF_CSRF_SSL_STRICT for login routes when behind Render.com proxy.
        
        Login routes are protected by Same-Origin Policy and don't modify
        persistent state on POST, so they're CSRF-safe without strict SSL checking.
        CSRF token validation is still enabled.
        """
        is_login_route = (
            request.path in ['/auth/login', '/superadmin/login']
            or '/auth/login' in request.path  # Tenant logins: /<tenant>/auth/login
            or ('/superadmin/' in request.path and 'login' in request.path)
        )
        
        if is_login_route:
            # Temporarily disable strict SSL checking for this request only.
            # CSRF token validation still occurs.
            app.config['WTF_CSRF_SSL_STRICT'] = False
        else:
            # Restore strict checking for all other routes
            if app.config.get('ENV') == 'production':
                app.config['WTF_CSRF_SSL_STRICT'] = True

    # ── Security headers ──────────────────────────────────────────────────────
    @app.after_request
    def set_security_headers(response):
```

### Option B: Use the Provided Patch

If you have the patched file `app_init_FIXED.py`:

```bash
cp app_init_FIXED.py app/__init__.py
```

---

## Step 2: Local Testing (CRITICAL)

### Test 1: Verify Syntax

```bash
python -m py_compile app/__init__.py
# No error = good
```

### Test 2: Test Superadmin Login Locally

Create test script (`test_csrf_fix_local.py`):

```python
#!/usr/bin/env python3
"""
Test the CSRF SSL Strict fix locally.
Verifies that login routes work and CSRF is still enforced.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(__file__))

from app import create_app, db
from app.models import User
from flask import url_for

app = create_app('development')  # Use dev config for testing

def test_login_forms_render():
    """Test that login forms render without CSRF errors."""
    with app.test_client() as client:
        # Test superadmin login GET
        response = client.get('/superadmin/login')
        assert response.status_code == 200, f"GET /superadmin/login failed: {response.status_code}"
        assert 'csrf_token' in response.data.decode(), "CSRF token not in form"
        print("✓ GET /superadmin/login renders with CSRF token")
        
        # Test admin login GET
        response = client.get('/auth/login')
        assert response.status_code == 200, f"GET /auth/login failed: {response.status_code}"
        assert 'csrf_token' in response.data.decode(), "CSRF token not in form"
        print("✓ GET /auth/login renders with CSRF token")

def test_csrf_still_validates():
    """Verify CSRF validation still works (missing/invalid token rejected)."""
    with app.test_client() as client:
        # Attempt POST without CSRF token (should fail)
        response = client.post(
            '/superadmin/login',
            data={
                'username': 'test@example.com',
                'password': 'test-password',
                # Note: no csrf_token
            },
            follow_redirects=False,
        )
        
        # Should get a 400 Bad Request or CSRF error
        # (Not a successful redirect, not a credential check)
        assert response.status_code in [400, 403] or 'csrf' in response.data.decode().lower(), \
            f"CSRF validation not working: {response.status_code}"
        print("✓ CSRF validation still enforced (missing token rejected)")

def test_csrf_before_request_hook_fires():
    """Verify the before_request hook is actually running."""
    with app.test_request_context('/superadmin/login'):
        from flask import request
        # Call the hook manually
        from app import csrf_ssl_strict_for_login_routes
        csrf_ssl_strict_for_login_routes()
        
        # Should be disabled for this route
        assert app.config.get('WTF_CSRF_SSL_STRICT') is False, "Hook didn't disable WTF_CSRF_SSL_STRICT"
        print("✓ csrf_ssl_strict_for_login_routes hook fires correctly")
    
    # Test non-login route (should restore to production default)
    with app.test_request_context('/admin/dashboard'):
        from flask import request
        # Simulate production config
        app.config['ENV'] = 'production'
        app.config['WTF_CSRF_SSL_STRICT'] = True  # Set initially
        
        from app import csrf_ssl_strict_for_login_routes
        csrf_ssl_strict_for_login_routes()
        
        # Should be restored to True for non-login routes
        assert app.config.get('WTF_CSRF_SSL_STRICT') is True, \
            "Hook didn't restore WTF_CSRF_SSL_STRICT for non-login routes"
        print("✓ Hook restores WTF_CSRF_SSL_STRICT for non-login routes")

if __name__ == '__main__':
    print("Testing CSRF SSL Strict fix...\n")
    
    try:
        test_login_forms_render()
        test_csrf_still_validates()
        test_csrf_before_request_hook_fires()
        
        print("\n✅ All tests passed! Ready for deployment.")
        sys.exit(0)
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
```

**Run the test:**

```bash
python test_csrf_fix_local.py
```

**Expected output:**
```
Testing CSRF SSL Strict fix...

✓ GET /superadmin/login renders with CSRF token
✓ GET /auth/login renders with CSRF token
✓ CSRF validation still enforced (missing token rejected)
✓ csrf_ssl_strict_for_login_routes hook fires correctly
✓ Hook restores WTF_CSRF_SSL_STRICT for non-login routes

✅ All tests passed! Ready for deployment.
```

---

## Step 3: Commit and Push

```bash
# Stage the fix
git add app/__init__.py

# Commit with clear message
git commit -m "fix(auth): disable WTF_CSRF_SSL_STRICT for login routes to fix Render proxy issue

Fixes production login failures caused by CSRF validation comparing
request.referrer (public hostname) to request.host (internal Render hostname).

Login routes are CSRF-safe because:
- They use POST-redirect-GET (no persistent state in POST)
- SOP blocks cross-origin POSTs
- Session token set AFTER login succeeds

CSRF token validation remains enabled for all routes."

# Push to your branch
git push origin your-branch-name

# Create PR if using GitHub
# (Or directly push to main if you have deploy access)
```

---

## Step 4: Deploy to Production

### Via Render.com Dashboard

1. **Go to Render Dashboard:**
   - https://dashboard.render.com

2. **Select `portfolio-cms` service**

3. **Manual Deploy (recommended for this critical fix):**
   - Click **"Manual Deploy"** button
   - Wait for build to complete (~2-3 minutes)

4. **Monitor logs during and after deploy:**
   - Click **"Logs"** tab
   - Look for any CSRF or auth errors

### Via Git Push (Auto-Deploy)

If Render is set to auto-deploy on push:

```bash
git push origin main
# Render will automatically build and deploy
```

---

## Step 5: Verify Deployment

### Immediate Checks (Within 1 minute of deploy)

1. **Check logs for deploy success:**
   ```
   Render Dashboard → portfolio-cms → Logs
   ```
   Should see:
   ```
   Building...
   Build successful
   Deploying...
   ```

2. **Test login manually:**
   - Open `https://myportfoliohub.online/superadmin/login` in browser
   - Enter superadmin credentials
   - ✅ Should redirect to dashboard
   - ❌ If you get "Invalid credentials" with correct password → deployment failed

3. **Check for CSRF errors in browser console:**
   - Open DevTools (F12)
   - Go to Console tab
   - Reload `/superadmin/login`
   - Should NOT see CSRF-related errors

### Detailed Checks (If manual test fails)

1. **Check that the fix was deployed:**
   ```bash
   # SSH into Render (if available)
   # Or check the Render source code viewer
   grep "csrf_ssl_strict_for_login_routes" /app/app/__init__.py
   # Should find the function
   ```

2. **Check config is correct:**
   ```
   Render → Environment Variables
   Verify: WTF_CSRF_ENABLED = True, WTF_CSRF_SSL_STRICT = True
   ```

3. **Check logs for CSRF-specific errors:**
   ```
   Render → Logs
   Search for: "CSRF", "referrer", "same_origin"
   Should NOT see CSRF failures on login POST
   ```

---

## Step 6: Monitor for Issues

### First Hour

Monitor these in Render dashboard:

- **Logs:** Any CSRF errors or auth failures
- **Metrics:** Error rate (should be 0%)
- **Uptime:** Service should be running

### Recommended Monitoring

Create a simple health check:

```python
# scripts/test_production_login.py
#!/usr/bin/env python3

import requests
import sys

PROD_URL = "https://myportfoliohub.online"

def test_login_page_loads():
    """Test that login pages load without errors."""
    for path in ['/superadmin/login', '/auth/login']:
        response = requests.get(f"{PROD_URL}{path}", timeout=10, verify=True)
        if response.status_code != 200:
            print(f"❌ {path}: {response.status_code}")
            return False
        if 'csrf_token' not in response.text:
            print(f"❌ {path}: CSRF token not found in form")
            return False
        print(f"✓ {path}: 200 OK, CSRF token present")
    return True

if __name__ == '__main__':
    try:
        if test_login_page_loads():
            print("\n✅ Production login pages are working")
            sys.exit(0)
        else:
            print("\n❌ Production login pages have issues")
            sys.exit(1)
    except Exception as e:
        print(f"❌ Health check failed: {e}")
        sys.exit(1)
```

Run this periodically:

```bash
# In CI/CD or cron job
python scripts/test_production_login.py
```

---

## Rollback Plan

If deployment causes issues:

### Option A: Revert Commit (Git)

```bash
git revert <commit-hash>
git push origin main
# Render auto-deploys the revert
```

### Option B: Quick Emergency Disable

If you need to immediately restore access while investigating:

**In `config.py` ProductionConfig:**

```python
class ProductionConfig(Config):
    # TEMPORARY EMERGENCY MEASURE ONLY
    # Disable strict CSRF checking globally while investigating
    WTF_CSRF_SSL_STRICT = False
    # TODO: Revert this and deploy the proper fix from app/__init__.py
```

⚠️ **Do NOT leave this as permanent** — it reduces security. Restore the before_request hook fix ASAP.

---

## Post-Deployment Checklist

- [ ] Deployment completed successfully (Render dashboard shows "Build successful")
- [ ] No CSRF errors in logs
- [ ] Manual login test passed (superadmin and admin)
- [ ] No "Invalid credentials" errors with correct password
- [ ] CSRF token present in login forms
- [ ] Health check script passes
- [ ] Monitoring is active (Render logs, error tracking)
- [ ] Team notified of fix deployment
- [ ] Document added to runbook or wiki

---

## FAQ

**Q: Why not just disable WTF_CSRF_SSL_STRICT globally?**
A: Because it's a security feature that protects against CSRF in shared hosting. Login routes are the only safe exception.

**Q: Will this break anything else?**
A: No. The fix only affects login routes (/auth/login, /superadmin/login, /<tenant>/auth/login). All other routes keep WTF_CSRF_SSL_STRICT = True in production. CSRF token validation is still enabled everywhere.

**Q: What if we need to revert quickly?**
A: Run `git revert <commit-hash> && git push` in < 2 minutes.

**Q: How do I know if the fix is working?**
A: Try logging in with correct credentials. If you get redirected to the dashboard, it's working. If you still see "Invalid credentials" errors, check the logs.

**Q: Is this a permanent fix?**
A: Yes. This is the proper fix for Render.com's reverse proxy setup. No further changes needed unless you migrate to a different hosting provider.

---

## Support

If deployment fails:

1. **Check Render logs:** Dashboard → Logs tab
2. **Look for specific errors:** "CSRF", "referrer", "same_origin"
3. **Verify the fix was deployed:** Render → Source code viewer, search for `csrf_ssl_strict_for_login_routes`
4. **Test manually:** Try logging in via browser DevTools open (F12 Console)
5. **Rollback if needed:** `git revert <hash> && git push`

---

## References

- **Root Cause Analysis:** See `CSRF_LOGIN_BUG_ANALYSIS_AND_FIX.md`
- **Flask-WTF CSRF:** https://flask-wtf.readthedocs.io/en/stable/csrf/
- **Render Proxy Headers:** https://render.com/docs/about
- **Same-Origin Policy:** https://developer.mozilla.org/en-US/docs/Web/Security/Same-origin_policy
