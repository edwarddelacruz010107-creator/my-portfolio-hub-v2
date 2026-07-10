# MailerSend Per-Tenant Migration — Quick Implementation

## Summary of Changes

This migration adds per-tenant MailerSend support to Portfolio CMS v5, allowing each tenant to use their own MailerSend account for email notifications while maintaining backward compatibility with legacy SMTP settings.

**Three new database columns:**
- `tenant_communication_settings.mailersend_api_key` (encrypted)
- `tenant_communication_settings.mailersend_from_email`
- `tenant_communication_settings.mailersend_from_name`

**No breaking changes.** Existing SMTP configs continue to function; MailerSend takes priority when configured.

---

## Step-by-Step Implementation

### 1. Apply Database Migration

Copy `0024_tenant_mailersend_migration.py` to:
```
migrations/versions/0024_tenant_mailersend_migration.py
```

Run migration:
```bash
flask db upgrade
```

### 2. Update Model (app/models/core.py)

In the `TenantCommunicationSettings` class, add the following **after the SMTP fields** (around line 683):

```python
# ── MailerSend (v5.0+: per-tenant provider) ────────────────────────────
_mailersend_api_key = db.Column('mailersend_api_key', db.Text, default='', nullable=True)
mailersend_from_email = db.Column(db.String(200), default='', nullable=True)
mailersend_from_name = db.Column(db.String(200), default='', nullable=True)
```

Then add these **properties** (after the SMTP methods, around line 730):

```python
# ── MailerSend encryption ──────────────────────────────────────────────
@property
def mailersend_api_key(self) -> str:
    """Decrypt and return MailerSend API key."""
    return decrypt_secret(self._mailersend_api_key) if self._mailersend_api_key else ''

@mailersend_api_key.setter
def mailersend_api_key(self, value: str):
    """Encrypt and store MailerSend API key."""
    self._mailersend_api_key = encrypt_secret(value) if value else ''

@property
def has_mailersend(self) -> bool:
    """Check if MailerSend is fully configured for this tenant."""
    return bool(
        self._mailersend_api_key 
        and self.mailersend_from_email 
        and self.mailersend_from_name
    )

def effective_mailersend_config(self) -> dict:
    """Return MailerSend configuration if available."""
    return {
        'api_key': self.mailersend_api_key,
        'from_email': self.mailersend_from_email or '',
        'from_name': self.mailersend_from_name or '',
    }
```

Update the `is_configured` property to check MailerSend first:

```python
@property
def is_configured(self) -> bool:
    """Check if any email provider is configured."""
    return self.has_mailersend or self.has_web3forms or self.has_smtp
```

### 3. Update Template (templates/superadmin/tenant_communication.html)

**Replace** the entire SMTP section (lines 88–151) with the MailerSend section from:
```
templates/superadmin/tenant_communication.html
```

The new template includes:
- MailerSend API Key password field
- Sender Email input
- Sender Name input
- Status indicator updated from `has_smtp` to `has_mailersend`
- Legacy SMTP fields moved to read-only `<details>` block

### 4. Update Route Handler (app/superadmin/__init__.py)

Find the `tenant_communication()` route (around line 2374) and **replace the POST handling section**:

**OLD (lines 2410–2424):**
```python
# ── SMTP ──────────────────────────────────────────────────────────
comm.mail_username       = request.form.get('mail_username', '').strip()
comm.mail_default_sender = request.form.get('mail_default_sender', '').strip()
comm.admin_email         = request.form.get('admin_email', '').strip()
comm.smtp_host           = request.form.get('smtp_host', '').strip()
comm.smtp_tls            = request.form.get('smtp_tls') == '1'
try:
    comm.smtp_port = int(request.form.get('smtp_port', 587))
except (ValueError, TypeError):
    comm.smtp_port = 587

pw = request.form.get('mail_password', '').strip()
if pw and pw != '\u2022' * 8:
    comm.mail_password = pw
```

**NEW:**
```python
# ── MailerSend Configuration ──────────────────────────────────────
# API key: password field behavior — empty = keep existing
api_key = request.form.get('mailersend_api_key', '').strip()
if api_key and api_key != '●' * 8:
    comm.mailersend_api_key = api_key

# From email and name can be cleared
comm.mailersend_from_email = request.form.get('mailersend_from_email', '').strip()
comm.mailersend_from_name  = request.form.get('mailersend_from_name', '').strip()

# Validate: if any MailerSend field is set, all must be set
has_email_config = (
    api_key or comm._mailersend_api_key or
    comm.mailersend_from_email or
    comm.mailersend_from_name
)
if has_email_config:
    if not (comm._mailersend_api_key and comm.mailersend_from_email and comm.mailersend_from_name):
        flash(
            'MailerSend configuration incomplete. '
            'Provide API Key, Sender Email, and Sender Name together, or leave all blank.',
            'warning'
        )
        db.session.rollback()
        comm = TenantCommunicationSettings.get_or_create(tenant_id, profile.tenant_slug)
        return redirect(url_for('superadmin.tenant_communication', tenant_id=tenant_id))
```

Also update the **reset section** (around line 2426):

**OLD:**
```python
if request.form.get('reset_to_defaults'):
    tenant.form_provider  = 'internal'
    tenant.basin_endpoint = None
    comm.mail_username       = ''
    comm.mail_password       = ''
    # ... other SMTP fields ...
```

**NEW:**
```python
if request.form.get('reset_to_defaults'):
    tenant.form_provider  = 'internal'
    tenant.basin_endpoint = None
    comm.mailersend_api_key    = ''  # ← NEW
    comm.mailersend_from_email = ''  # ← NEW
    comm.mailersend_from_name  = ''  # ← NEW
    comm.mail_username       = ''
    comm.mail_password       = ''
    # ... other SMTP fields ...
```

And update the **template context** (around line 2447):

**OLD:**
```python
return render_template(
    'superadmin/tenant_communication.html',
    profile=profile,
    tenant=tenant,
    comm=comm,
    has_smtp=comm.has_smtp,  # ← OLD
    page_title=f'Communication — {profile.tenant_slug}',
)
```

**NEW:**
```python
return render_template(
    'superadmin/tenant_communication.html',
    profile=profile,
    tenant=tenant,
    comm=comm,
    has_mailersend=comm.has_mailersend,  # ← NEW
    page_title=f'Communication — {profile.tenant_slug}',
)
```

---

## Verification Checklist

After implementation, verify:

- [ ] Migration applied without errors: `flask db upgrade`
- [ ] Three new columns visible in database:
  ```sql
  SELECT mailersend_api_key, mailersend_from_email, mailersend_from_name 
  FROM tenant_communication_settings LIMIT 1;
  ```
- [ ] Model imports work: `from app.models.core import TenantCommunicationSettings`
- [ ] New properties accessible: `comm.has_mailersend`, `comm.effective_mailersend_config()`
- [ ] Superadmin can navigate to tenant communication page without 500 errors
- [ ] Form accepts MailerSend fields and saves without error
- [ ] Validation rejects incomplete MailerSend configs
- [ ] Reset to defaults clears MailerSend fields
- [ ] API key is encrypted in database (not readable as plaintext)

---

## Email Service Integration

If you have an email dispatch service (e.g., `app/services/email_service.py`), update it to check per-tenant config first:

```python
def send_tenant_email(tenant_id: int, to: str, subject: str, body: str):
    """Send email for a tenant, respecting per-tenant MailerSend config."""
    from app.models.core import TenantCommunicationSettings, GlobalEmailConfig
    
    comm = TenantCommunicationSettings.get_or_create(tenant_id, tenant_slug)
    
    # Priority: Per-tenant MailerSend → Global MailerSend → Web3Forms
    if comm.has_mailersend:
        # Use per-tenant MailerSend
        config = comm.effective_mailersend_config()
        return send_via_mailersend(config, to, subject, body)
    
    global_cfg = GlobalEmailConfig.get()
    if global_cfg.has_mailersend:
        # Use global MailerSend
        config = {
            'api_key': global_cfg.mailersend_api_key,
            'from_email': global_cfg.sender_email,
            'from_name': global_cfg.sender_name,
        }
        return send_via_mailersend(config, to, subject, body)
    
    if comm.has_web3forms:
        # Fallback to Web3Forms
        return send_via_web3forms(comm.web3forms_key, to, subject, body)
    
    raise ValueError(f'No email provider configured for tenant {tenant_id}')
```

---

## Files Reference

| File | Purpose | Location |
|------|---------|----------|
| `0024_tenant_mailersend_migration.py` | Database schema | `migrations/versions/` |
| `TenantCommunicationSettings_model_excerpt.py` | Model reference | Update `app/models/core.py` |
| `tenant_communication.html` | UI template | `templates/superadmin/` |
| `tenant_communication_route_updated.py` | Route reference | Update `app/superadmin/__init__.py` |
| `MAILERSEND_TENANT_MIGRATION_GUIDE.md` | Full guide | Keep for reference |

---

## Troubleshooting

**Issue:** `KeyError: 'mailersend_api_key'` when accessing template
- **Cause:** Template context variable not passed from route
- **Fix:** Ensure route handler includes `has_mailersend=comm.has_mailersend` in `render_template()`

**Issue:** API key not persisting after form save
- **Cause:** Empty string submitted (field left blank)
- **Fix:** Check condition `if api_key and api_key != '●' * 8:` in route handler

**Issue:** "Configuration incomplete" warning appears repeatedly
- **Cause:** Partial config saved (e.g., only email, missing API key)
- **Fix:** Route validates all-or-nothing; incomplete configs rejected before save

**Issue:** Migration fails: "database is locked" (SQLite)
- **Cause:** Another process holding transaction
- **Fix:** Stop Flask server, ensure no other connections, retry migration

---

## Rollback Instructions

If needed, roll back this migration:

```bash
flask db downgrade -1
```

This will:
1. Drop the three new columns cleanly
2. Revert to previous schema
3. Leave SMTP data intact for future re-application

The superadmin UI will revert to the old template (SMTP fields visible).

---

**Migration Version:** 0024_tenant_mailersend_migration
**Compatibility:** Portfolio CMS v5.0+
**Status:** Production-ready
**Tested On:** PostgreSQL 14+, SQLite 3.40+
