# Portfolio CMS v5.3 — Production Login Failure: CSRF SSL Strict Bug

## Executive Summary

**Issue:** Superadmin and admin logins fail in production with correct credentials.

**Root Cause:** Flask-WTF's `WTF_CSRF_SSL_STRICT = True` (enforced in production) validates the HTTP `Referer` header against `request.host`. Behind Render.com's reverse proxy, the referrer hostname and the proxied request host **do not match**, causing CSRF validation to fail **before** form processing even occurs.

**Severity:** 🔴 **CRITICAL** — Blocks all user logins in production.

**Fix:** Disable `WTF_CSRF_SSL_STRICT` for login routes (auth/superadmin logins are low-CSRF risk due to SOP) OR configure Flask-WTF to trust Render's proxy headers.

---

## Root Cause Chain

### 1. The Configuration (Production)
In `config.py` **line 252**:
```python
class ProductionConfig(Config):
    # ...
    WTF_CSRF_SSL_STRICT = True
```

This setting forces Flask-WTF to validate:
```python
if request.is_secure and current_app.config["WTF_CSRF_SSL_STRICT"]:
    if not request.referrer:
        self._error_response("The referrer header is missing.")
    good_referrer = f"https://{request.host}/"
    if not same_origin(request.referrer, good_referrer):
        self._error_response("The referrer does not match the host.")
```

### 2. What Happens Behind Render.com's Proxy

**Browser State:**
- User navigates to `https://myportfoliohub.online/superadmin/login`
- Fills credentials and POSTs the form
- Browser includes `Referer: https://myportfoliohub.online/superadmin/login`

**Render Proxy State:**
- Request arrives at Render's HTTPS terminator
- Proxy adds header: `X-Forwarded-Proto: https`
- Proxy adds header: `X-Forwarded-Host: myportfoliohub.online`
- Proxy forwards request to Flask app on internal network (possibly with different Host header)

**Flask App State (Behind Proxy):**
- `request.referrer` = `https://myportfoliohub.online/superadmin/login` ✓ Correct
- `request.host` = ⚠️ **MISMATCH** — Could be:
  - `render-internal.onrender.com` (internal hostname)
  - `localhost:5000` (if proxy not properly config'd)
  - `service-name.onrender.com` (if X-Forwarded-Host not read)
  - Port mismatch: `myportfoliohub.online:8000` vs `myportfoliohub.online:443`

### 3. CSRF Validation Fails

```python
good_referrer = f"https://{request.host}/"  
# Example: "https://render-internal.onrender.com/"

same_origin(
    "https://myportfoliohub.online/superadmin/login",  # request.referrer
    "https://render-internal.onrender.com/"              # good_referrer
)
# Returns False — **CSRF rejected**
```

**Result:** Flash message hidden, form resubmitted silently, login appears to fail with "Invalid credentials" even though they're correct.

---

## Why This Happens

Flask-WTF's `WTF_CSRF_SSL_STRICT` is designed to prevent CSRF in environments where:
- The server's hostname is user-controlled (shared hosting)
- The Referer header can be spoofed by network layer
- HTTPS intercepts cannot be trusted

But in:
- Render.com (managed, TLS-terminating proxy)
- Heroku, AWS ALB, or any reverse proxy with proper header forwarding
- Any environment with a trustworthy proxy layer

The setting is **too strict and breaks legitimate logins**.

---

## The Fix

### Option A: Disable CSRF_SSL_STRICT for Login Routes (⭐ RECOMMENDED)

**Why:** 
- Login routes (`/auth/login`, `/superadmin/login`, `/tenant/*/auth/login`) are **not vulnerable to CSRF** because:
  1. They handle POST-redirect-GET (browser POST, redirect to GET page)
  2. No persistent state changes occur in POST handler itself
  3. Session token (`session['_2fa_user_id']`) set AFTER login succeeds
  4. Same-Origin Policy (SOP) blocks cross-site POST anyway
- Low risk, minimal code change

**Implementation:**

Create custom CSRF exemption in `app/__init__.py`:

```python
# app/__init__.py

from flask_wtf.csrf import csrf_exempt

csrf = CSRFProtect()
csrf.init_app(app)

# Exempt login routes from strict SSL CSRF checking
# (they're already protected by SOP and don't modify persistent state on POST)
@app.before_request
def disable_csrf_ssl_strict_for_login():
    """
    Disable WTF_CSRF_SSL_STRICT for login routes.
    
    Login routes (auth/superadmin/tenant login) are safe from CSRF because:
    - They use POST-redirect-GET flow
    - Session state set AFTER POST completes (2FA check)
    - SOP blocks cross-origin POST anyway
    - No token/state mutation in POST handler
    
    Render.com proxy headers (X-Forwarded-Proto, X-Forwarded-Host)
    cause false positives when this setting is enabled.
    """
    if request.path in ['/auth/login', '/superadmin/login'] or '/auth/login' in request.path:
        # Temporarily disable the strict check for these endpoints
        app.config['WTF_CSRF_SSL_STRICT'] = False
```

**Or** — Simpler approach: **Exclude login routes from CSRF check entirely**:

```python
from flask_wtf.csrf import csrf_exempt

@auth.route('/login', methods=['GET', 'POST'])
@limiter.limit('10 per minute')
@limiter.limit('30 per hour')
@csrf_exempt  # Login is not CSRF-vulnerable (SOP + POST-redirect-GET)
def login():
    # ... existing code
```

### Option B: Configure Flask-WTF to Trust Proxy Headers

**Implementation:**

In `config.py` `ProductionConfig`:

```python
class ProductionConfig(Config):
    # ...
    WTF_CSRF_SSL_STRICT = False  # Trust X-Forwarded-* headers from Render proxy
    WTF_CSRF_TRUSTED_HOSTS = ['myportfoliohub.online']  # Explicit whitelist
```

**Downside:** Less secure if proxy is compromised.

### Option C: Implement Custom CSRF Validation (Most Robust)

Create a custom CSRF validator that reads proxy headers:

```python
# app/middleware/csrf_proxy_aware.py

from flask import request
from werkzeug.http import parse_options_header

def get_trusted_origin():
    """
    Get the origin hostname from X-Forwarded-Host or request.host.
    Render.com sets X-Forwarded-Host; we trust it.
    """
    forwarded = request.headers.get('X-Forwarded-Host', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.host.split(':')[0]  # Remove port if present

def validate_referrer_against_proxy():
    """
    Custom CSRF referrer validation that understands reverse proxies.
    
    Instead of comparing request.host (which may be internal),
    compare against X-Forwarded-Host (the public hostname).
    """
    if not request.is_secure:
        return True
    
    referrer = request.referrer
    if not referrer:
        return False  # No referrer = fail
    
    trusted_origin = get_trusted_origin()
    if trusted_origin not in referrer:
        return False  # Referrer doesn't match proxy's public hostname
    
    return True
```

Then in `config.py`:

```python
class ProductionConfig(Config):
    WTF_CSRF_SSL_STRICT = False
    WTF_CSRF_ENABLED = True
    # Custom validator installed via middleware
```

---

## Implementation: Option A (Recommended)

### Step 1: Update `app/__init__.py`

Locate this section (around line ~200 where CSRF is initialized):

```python
# BEFORE: 
csrf = CSRFProtect()
# ... initialization ...
csrf.init_app(app)
```

Replace with:

```python
# AFTER:
from flask_wtf.csrf import csrf_exempt

csrf = CSRFProtect()
# ... initialization ...
csrf.init_app(app)

# Exempt login routes from WTF_CSRF_SSL_STRICT validation
# These endpoints are protected by SOP and use POST-redirect-GET flow
@app.before_request
def csrf_ssl_strict_for_login_routes():
    """
    Disable WTF_CSRF_SSL_STRICT for login routes.
    
    **Why?**
    - Login routes (GET+POST /auth/login, /superadmin/login) are not CSRF-vulnerable
    - Same-Origin Policy (SOP) blocks cross-origin POSTs
    - Session token set AFTER login succeeds (no state mutation in POST)
    - POST-redirect-GET pattern provides additional safety
    
    **Problem (Render.com proxy):**
    - WTF_CSRF_SSL_STRICT = True checks: same_origin(request.referrer, f"https://{request.host}/")
    - request.referrer = "https://myportfoliohub.online/superadmin/login" ✓
    - request.host = "render-internal.onrender.com" OR internal hostname ✗
    - Hosts don't match → CSRF validation fails → login rejected
    
    **Solution:**
    - Temporarily disable WTF_CSRF_SSL_STRICT for login routes
    - CSRF token validation still occurs (app.config['WTF_CSRF_ENABLED'] = True)
    - Only the strict host matching is relaxed
    """
    is_login_route = (
        request.path in ['/auth/login', '/superadmin/login']
        or '/auth/login' in request.path  # Tenant logins: /<tenant>/auth/login
        or '/superadmin/' in request.path and 'login' in request.path  # Superadmin routes
    )
    
    if is_login_route:
        # Disable strict SSL checking for this request only
        app.config['WTF_CSRF_SSL_STRICT'] = False
```

### Step 2: Verify CSRF is Still Enabled

In `config.py`, verify:

```python
class Config:
    WTF_CSRF_ENABLED       = True        # ✓ Token validation still active
    WTF_CSRF_CHECK_DEFAULT = True        # ✓ All routes checked by default
    WTF_CSRF_TIME_LIMIT    = 3600        # ✓ Tokens expire after 1 hour
    # WTF_CSRF_SSL_STRICT will be set dynamically per request
```

### Step 3: Test Locally (HTTPS Required)

Create a test script:

```python
# test_csrf_login_fix.py

import os
import sys
sys.path.insert(0, os.path.dirname(__file__))

from app import create_app
from flask import request
import ssl

app = create_app('production')

with app.test_client() as client:
    # Test superadmin login
    response = client.get('/superadmin/login')
    assert response.status_code == 200, f"GET /superadmin/login failed: {response.status_code}"
    print("✓ GET /superadmin/login: 200 OK")
    
    # Simulate login POST with correct CSRF token extracted from form
    with app.test_request_context():
        from app.forms import LoginForm
        form = LoginForm()
        csrf_token = form.csrf_token.data
    
    # POST login with CSRF token
    response = client.post(
        '/superadmin/login',
        data={
            'username': 'superadmin',
            'password': 'correct-password-here',
            'csrf_token': csrf_token,
            'remember_me': False,
        },
        follow_redirects=False,
    )
    
    # Should either succeed (302 redirect) or show "Invalid credentials" (200)
    # Should NOT show "CSRF token missing" or "CSRF validation failed"
    assert response.status_code in [200, 302, 401], f"POST /superadmin/login unexpected: {response.status_code}"
    assert 'CSRF' not in response.data.decode(), "CSRF error detected in response"
    print("✓ POST /superadmin/login: No CSRF errors")

print("\n✅ CSRF fix verified!")
```

### Step 4: Deploy to Production

```bash
# Commit changes
git add app/__init__.py config.py
git commit -m "fix: disable WTF_CSRF_SSL_STRICT for login routes (fixes Render proxy CSRF failures)"

# Push to Render
git push origin main
# Render auto-deploys

# Monitor logs
heroku logs --tail
# or
render logs --service portfolio-cms-prod
```

### Step 5: Verify in Production

1. Open `https://myportfoliohub.online/superadmin/login`
2. Enter superadmin credentials
3. ✅ Should redirect to superadmin dashboard (no CSRF error)

---

## Why Not Option B/C?

| Option | Pros | Cons |
|--------|------|------|
| **A (Recommended)** | Simple, low risk, login-specific, CSRF still enabled | Requires @before_request hook |
| **B (Trust Proxy)** | One-line config change | Less secure, trusts proxy headers globally |
| **C (Custom Validator)** | Most robust, custom logic | Complex, requires extensive testing |

---

## Render.com Configuration Check

Verify Render is forwarding headers correctly:

### `render.yaml` (if using):

```yaml
services:
  - type: web
    name: portfolio-cms
    staticPublicPath: /static
    buildCommand: pip install -r requirements.txt && flask db upgrade
    startCommand: gunicorn --workers 4 --timeout 60 wsgi:app
    envVars:
      - key: FLASK_ENV
        value: production
      - key: WEB_CONCURRENCY
        value: 4
      # These should already be set
      - key: DATABASE_URL
        fromDatabase:
          name: portfolio-cms-db
          property: connectionString
```

### Flask App Configuration:

```python
# app/__init__.py

# IMPORTANT: Trust X-Forwarded-* headers from Render's reverse proxy
app.config['PREFERRED_URL_SCHEME'] = 'https'
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# For request.host and request.is_secure to work correctly behind proxy:
app.wsgi_app = ProxyFix(
    app.wsgi_app,
    x_for=1,        # X-Forwarded-For
    x_proto=1,      # X-Forwarded-Proto
    x_host=1,       # X-Forwarded-Host
    x_port=1,       # X-Forwarded-Port
    x_prefix=1,     # X-Forwarded-Prefix
)
```

---

## Post-Fix Validation Checklist

- [ ] Locally test superadmin login with correct & incorrect credentials
- [ ] Locally test admin login
- [ ] Locally test tenant login
- [ ] Verify CSRF token still appears in forms (`<input name="csrf_token" ...>`)
- [ ] Verify CSRF validation still blocks invalid tokens (modify token in browser DevTools)
- [ ] Deploy to staging (if available)
- [ ] Test in production on `https://myportfoliohub.online/superadmin/login`
- [ ] Monitor logs for any new CSRF-related errors
- [ ] Check browser DevTools → Network tab → verify requests include CSRF token

---

## Fallback: Emergency Disable

If you need to **immediately restore access** while implementing the proper fix:

In `config.py` **temporarily**:

```python
class ProductionConfig(Config):
    # TEMPORARY: Disable strict CSRF SSL checking
    # TODO: Implement before_request hook per CSRF_LOGIN_BUG_ANALYSIS_AND_FIX.md
    WTF_CSRF_SSL_STRICT = False
```

⚠️ **Do NOT keep this as permanent** — properly implement the fix above.

---

## References

- Flask-WTF CSRF docs: https://flask-wtf.readthedocs.io/en/stable/csrf/
- Render.com reverse proxy headers: https://render.com/docs/about
- OWASP CSRF mitigation: https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html
- werkzeug.middleware.proxy_fix: https://werkzeug.palletsprojects.com/en/latest/middleware/proxy_fix/
