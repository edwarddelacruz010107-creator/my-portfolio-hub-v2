"""
tools/debug_admin_otp.py — Admin OTP Delivery Diagnostic (v6.0)

Run this INSIDE the Flask application context to trace the exact provider
resolution path for a given admin email.  Does NOT send a real OTP — it
dry-runs the provider chain and reports each step.

Usage:
    flask shell < tools/debug_admin_otp.py

    # Or with email argument:
    python -c "
    import sys; sys.argv = ['', 'admin@example.com']
    exec(open('tools/debug_admin_otp.py').read())
    "

Output legend:
    [PASS] — step succeeded / provider found
    [FAIL] — step failed, moving to fallback
    [INFO] — informational, no action needed
    [WARN] — soft warning
"""
import sys
import json
import traceback

EMAIL = sys.argv[1] if len(sys.argv) > 1 else 'admin@yourdomain.com'

print(f'\n{"=" * 60}')
print(f'  Admin OTP Diagnostic — target: {EMAIL}')
print(f'{"=" * 60}\n')


def _check(label, fn):
    try:
        result = fn()
        print(f'  [PASS] {label}: {result}')
        return result
    except Exception as exc:
        print(f'  [FAIL] {label}: {exc}')
        traceback.print_exc()
        return None


# ── 1. User lookup ──────────────────────────────────────────────────────────
print('STEP 1 — User Lookup')
from app.models.core import User
user = User.query.filter_by(email=EMAIL, is_superadmin=False).first()
if not user:
    print(f'  [FAIL] No non-superadmin user found with email={EMAIL}')
    print('         Check: email spelling, is_superadmin flag, DB connection')
    sys.exit(1)

print(f'  [PASS] user.id={user.id}  tenant_id={user.tenant_id}  '
      f'tenant_slug={user.tenant_slug!r}')

# ── 2. Tenant resolution ─────────────────────────────────────────────────────
print('\nSTEP 2 — Tenant Resolution')
from app.models.core import Tenant
tenant = Tenant.query.get(user.tenant_id)
if not tenant:
    print(f'  [FAIL] No Tenant row for id={user.tenant_id}')
    sys.exit(1)

print(f'  [PASS] tenant.slug={tenant.slug!r}  tenant.plan={tenant.plan!r}  '
      f'tenant.status={tenant.status!r}')

# ── 3. TenantEmailProvider lookup ─────────────────────────────────────────────
print('\nSTEP 3 — TenantEmailProvider Records')
from app.models.core import TenantEmailProvider
providers = TenantEmailProvider.query.filter_by(tenant_id=user.tenant_id).all()
if not providers:
    print('  [WARN] No TenantEmailProvider rows found for this tenant.')
    print('         Admin needs to visit /admin/email-services and configure SMTP.')
else:
    for p in providers:
        print(f'  [INFO] provider={p.provider_name!r}  active={p.active}  '
              f'status={p.status!r}  priority={p.priority}')

# ── 4. Active providers ───────────────────────────────────────────────────────
print('\nSTEP 4 — Active Providers (ordered by priority)')
active = TenantEmailProvider.get_ordered_active(user.tenant_id)
if not active:
    print('  [WARN] No ACTIVE providers. OTP will fall through to GlobalEmailConfig.')
else:
    for p in active:
        print(f'  [PASS] {p.provider_name!r} priority={p.priority}')

# ── 5. TenantSmtpSettings configured check ────────────────────────────────────
print('\nSTEP 5 — TenantSmtpSettings')
from app.models.core import TenantSmtpSettings
smtp = TenantSmtpSettings.query.filter_by(tenant_id=user.tenant_id).first()
if not smtp:
    print('  [WARN] No TenantSmtpSettings row. Provider bootstrap may be needed.')
    print('         Call: from app.services.tenant_email_service import bootstrap_tenant_providers')
    print(f'         bootstrap_tenant_providers({user.tenant_id})')
else:
    print(f'  [INFO] smtp_host={smtp.smtp_host!r}  smtp_port={smtp.smtp_port}  '
          f'smtp_username={smtp.smtp_username!r}  configured={smtp.is_configured}')
    if not smtp.is_configured:
        print('  [WARN] SMTP is present but NOT fully configured (missing host/user/pass).')

# ── 6. GlobalEmailConfig fallback state ───────────────────────────────────────
print('\nSTEP 6 — GlobalEmailConfig (Fallback)')
from app.models.core import GlobalEmailConfig
gcfg = GlobalEmailConfig.get()
if gcfg:
    ms_key = bool(gcfg.mailersend_api_key)
    sa_smtp = bool(getattr(gcfg, 'sa_smtp_host', ''))
    print(f'  [INFO] MailerSend key present: {ms_key}')
    print(f'  [INFO] SA SMTP host present:   {sa_smtp}')
    if not ms_key and not sa_smtp:
        print('  [WARN] GlobalEmailConfig has no configured providers. '
              'Final fallback (env vars) is last resort.')
else:
    print('  [WARN] No GlobalEmailConfig singleton found.')

# ── 7. Env-var fallback ───────────────────────────────────────────────────────
print('\nSTEP 7 — Environment Variable Fallback')
import os
smtp_host = os.environ.get('SMTP_HOST', '')
ms_api    = os.environ.get('MAILERSEND_API_KEY', '')
print(f'  [INFO] SMTP_HOST env: {"SET" if smtp_host else "NOT SET"}')
print(f'  [INFO] MAILERSEND_API_KEY env: {"SET" if ms_api else "NOT SET"}')

if not smtp_host and not ms_api:
    print('  [FAIL] ALL fallbacks exhausted — OTP WILL NOT BE DELIVERED.')
    print('         Resolution options:')
    print('           A. Configure SMTP at /admin/email-services (preferred)')
    print('           B. Set SMTP_HOST/SMTP_USERNAME/SMTP_PASSWORD env vars')
    print('           C. Configure MailerSend in GlobalEmailConfig via /superadmin')
else:
    print('  [PASS] At least one env-var fallback is configured.')

# ── 8. Recovery enabled? ──────────────────────────────────────────────────────
print('\nSTEP 8 — Password Recovery Enabled?')
recovery_enabled = GlobalEmailConfig.get().recovery_enabled if gcfg else True
print(f'  [{"PASS" if recovery_enabled else "FAIL"}] recovery_enabled={recovery_enabled}')

# ── SUMMARY ───────────────────────────────────────────────────────────────────
print(f'\n{"=" * 60}')
print('DIAGNOSTIC SUMMARY')
print(f'{"=" * 60}')
print(f'  User ID:          {user.id}')
print(f'  Tenant ID:        {user.tenant_id}')
print(f'  Tenant Slug:      {tenant.slug!r}')
print(f'  Active Providers: {len(active)}')
print(f'  SMTP Configured:  {smtp.is_configured if smtp else False}')
print(f'  Recovery Enabled: {recovery_enabled}')
print()
if active and (smtp and smtp.is_configured):
    print('  ✅ EXPECTED: OTP should deliver via tenant SMTP.')
elif active:
    print('  ⚠️  Providers exist but SMTP not configured — may fail to send.')
elif gcfg and (ms_key or sa_smtp):
    print('  ⚠️  No tenant providers — will use GlobalEmailConfig fallback.')
elif smtp_host or ms_api:
    print('  ⚠️  No tenant/global providers — using bare env-var fallback.')
else:
    print('  ❌ NO DELIVERY PATH AVAILABLE — configure /admin/email-services.')
print()
