# Portfolio CMS v5 — MailerSend Per-Tenant Migration Guide

## Overview

This migration converts **per-tenant email configuration** from SMTP (deprecated) to **MailerSend** as the primary provider, while maintaining backward compatibility through encrypted storage and graceful field deprecation.

**Status:**
- ✅ Global MailerSend config (v5.0): Already implemented
- ✅ Per-tenant MailerSend config (v5.0+): New in this migration
- ⚠️ Legacy SMTP fields: Retained for rollback safety, non-functional

---

## Database Schema Changes

### Migration: `0024_tenant_mailersend_migration`

**Target Table:** `tenant_communication_settings`

**New Columns:**
```sql
ALTER TABLE tenant_communication_settings ADD COLUMN mailersend_api_key TEXT;
ALTER TABLE tenant_communication_settings ADD COLUMN mailersend_from_email VARCHAR(200);
ALTER TABLE tenant_communication_settings ADD COLUMN mailersend_from_name VARCHAR(200);
```

**Encryption:** All three fields follow the same encryption pattern as existing secret fields:
- API key stored as Fernet-encrypted text in database
- Never transmitted to browser
- Accessible only via decrypted property in application

---

## Model Changes

### `app/models/core.py` — TenantCommunicationSettings

**NEW FIELDS:**
```python
_mailersend_api_key = db.Column('mailersend_api_key', db.Text, default='', nullable=True)
mailersend_from_email = db.Column(db.String(200), default='', nullable=True)
mailersend_from_name = db.Column(db.String(200), default='', nullable=True)
```

**NEW PROPERTIES:**
```python
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
    """Check if MailerSend is fully configured."""
    return bool(
        self._mailersend_api_key 
        and self.mailersend_from_email 
        and self.mailersend_from_name
    )

def effective_mailersend_config(self) -> dict:
    """Return MailerSend config dict for email dispatch."""
    return {
        'api_key': self.mailersend_api_key,
        'from_email': self.mailersend_from_email or '',
        'from_name': self.mailersend_from_name or '',
    }
```

**BACKWARD COMPATIBILITY:**
- Existing SMTP fields (`smtp_host`, `mail_username`, `_mail_password`, etc.) retained but unused
- `has_smtp` property deprecated (will always return False if MailerSend is configured)
- `is_configured` property updated to check MailerSend first, then SMTP, then Web3Forms

---

## Template Changes

### `templates/superadmin/tenant_communication.html`

**SECTION 1: Contact Form Provider (Unchanged)**
- Basin / Internal CMS selection

**SECTION 2: MailerSend Configuration (REPLACES SMTP)**
```html
<div class="form-card">
  <h3>MailerSend Sender Configuration</h3>
  
  <!-- MailerSend API Key (password field) -->
  <input type="password" name="mailersend_api_key" 
         placeholder="Paste your MailerSend API key">
  
  <!-- From Email -->
  <input type="email" name="mailersend_from_email"
         placeholder="noreply@yourdomain.com">
  
  <!-- From Name -->
  <input type="text" name="mailersend_from_name"
         placeholder="e.g., Support Team">
</div>
```

**SECTION 3: Legacy SMTP (HIDDEN IN DETAILS TAG)**
- All SMTP fields shown as **read-only**, disabled
- Wrapped in `<details>` collapsible section marked as deprecated
- Purpose: Migration transparency, data preservation

---

## Route Changes

### `app/superadmin/__init__.py` — tenant_communication()

**OLD CODE:**
```python
def tenant_communication(tenant_id):
    # ... form POST handling ...
    comm.mail_username = request.form.get('mail_username', '').strip()
    comm.smtp_host = request.form.get('smtp_host', '').strip()
    comm.smtp_port = int(request.form.get('smtp_port', 587))
    comm.smtp_tls = request.form.get('smtp_tls') == '1'
    pw = request.form.get('mail_password', '').strip()
    if pw and pw != '\u2022' * 8:
        comm.mail_password = pw
```

**NEW CODE:**
```python
def tenant_communication(tenant_id):
    # ... form POST handling ...
    # MailerSend API key (password field: empty = keep existing)
    api_key = request.form.get('mailersend_api_key', '').strip()
    if api_key and api_key != '●' * 8:
        comm.mailersend_api_key = api_key
    
    # From email and name can be cleared
    comm.mailersend_from_email = request.form.get('mailersend_from_email', '').strip()
    comm.mailersend_from_name = request.form.get('mailersend_from_name', '').strip()
    
    # Validation: if any field is set, all must be set
    has_email_config = (
        api_key or comm._mailersend_api_key or
        comm.mailersend_from_email or
        comm.mailersend_from_name
    )
    if has_email_config:
        if not (comm._mailersend_api_key and comm.mailersend_from_email and comm.mailersend_from_name):
            flash('MailerSend configuration incomplete...', 'warning')
            return redirect(...)
    
    # Reset option clears MailerSend fields too
    if request.form.get('reset_to_defaults'):
        comm.mailersend_api_key = ''
        comm.mailersend_from_email = ''
        comm.mailersend_from_name = ''
```

**TEMPLATE CONTEXT:**
```python
return render_template(
    'superadmin/tenant_communication.html',
    profile=profile,
    tenant=tenant,
    comm=comm,
    has_mailersend=comm.has_mailersend,  # ← NEW (was has_smtp)
    page_title=f'Communication — {profile.tenant_slug}',
)
```

---

## Email Dispatch Priority (Service Layer)

When sending tenant emails, apply this priority:

```python
def get_email_config_for_tenant(tenant: Tenant) -> dict:
    """Resolve which email provider to use."""
    comm = TenantCommunicationSettings.get_or_create(tenant.id, tenant.slug)
    
    # 1. Per-tenant MailerSend (highest priority)
    if comm.has_mailersend:
        return {
            'provider': 'mailersend',
            'config': comm.effective_mailersend_config(),
        }
    
    # 2. Global MailerSend
    from app.models.core import GlobalEmailConfig
    global_cfg = GlobalEmailConfig.get()
    if global_cfg.has_mailersend:
        return {
            'provider': 'mailersend',
            'config': {
                'api_key': global_cfg.mailersend_api_key,
                'from_email': global_cfg.sender_email,
                'from_name': global_cfg.sender_name,
            },
        }
    
    # 3. Web3Forms fallback
    if comm.has_web3forms:
        return {
            'provider': 'web3forms',
            'config': {'api_key': comm.web3forms_key},
        }
    
    # 4. No email config (error)
    raise ValueError(f'No email provider configured for tenant {tenant.id}')
```

---

## Implementation Checklist

### Phase 1: Database Migration
- [ ] Run migration `0024_tenant_mailersend_migration`
  ```bash
  flask db upgrade
  ```
- [ ] Verify columns added to `tenant_communication_settings`:
  ```sql
  SELECT mailersend_api_key, mailersend_from_email, mailersend_from_name 
  FROM tenant_communication_settings LIMIT 1;
  ```

### Phase 2: Model Updates
- [ ] Update `app/models/core.py`:
  - Add three new columns to `TenantCommunicationSettings`
  - Add encryption properties (`mailersend_api_key` getter/setter)
  - Add `has_mailersend` property
  - Add `effective_mailersend_config()` method
  - Update `is_configured` property to check MailerSend first

### Phase 3: Template Updates
- [ ] Update `templates/superadmin/tenant_communication.html`:
  - Replace SMTP form section with MailerSend section
  - Move SMTP fields to read-only `<details>` block
  - Update status indicator from `has_smtp` to `has_mailersend`

### Phase 4: Route Handler Updates
- [ ] Update `app/superadmin/__init__.py` — `tenant_communication()` function:
  - Add MailerSend field extraction from POST
  - Add validation (all or nothing)
  - Update template context variable
  - Update reset logic to clear MailerSend fields

### Phase 5: Email Service Integration
- [ ] Update email service dispatch logic:
  - Check per-tenant MailerSend config first
  - Fallback to global MailerSend
  - Fallback to Web3Forms
  - Apply priority order when rendering email templates

### Phase 6: Testing
- [ ] Test superadmin can set per-tenant MailerSend config
- [ ] Test API key encryption/decryption
- [ ] Test validation (incomplete configs rejected)
- [ ] Test reset to defaults clears MailerSend fields
- [ ] Test email dispatch respects priority order
- [ ] Test rollback: SMTP fields still readable (for audit trail)

### Phase 7: Documentation
- [ ] Update DEPLOYMENT_GUIDE.md with MailerSend per-tenant setup instructions
- [ ] Document for tenants:
  - How to get MailerSend account
  - How to generate API token
  - How to verify sender domain
  - How to configure in superadmin panel

---

## Backward Compatibility & Rollback

### If Rollback Needed

The migration uses `batch_alter_table`, which works with both SQLite and PostgreSQL:

```python
def downgrade():
    with op.batch_alter_table('tenant_communication_settings', schema=None) as batch:
        batch.drop_column('mailersend_from_name')
        batch.drop_column('mailersend_from_email')
        batch.drop_column('mailersend_api_key')
```

**Rollback steps:**
```bash
flask db downgrade -1
```

**Result:**
- Three columns dropped cleanly
- No data loss (no existing data in these columns)
- SMTP fields untouched (safe for future re-migration)
- UI reverts to old templates

### Data Preservation

- Existing SMTP configurations **retained in database**
- If per-tenant MailerSend not configured, system falls back to global MailerSend
- Old SMTP fields visible in admin panel (read-only) for audit trail

---

## Migration Timeline

| Phase | Duration | Risk Level |
|-------|----------|-----------|
| Database migration | <1 min | 🟢 Low |
| Code deployment | <5 min | 🟡 Medium (requires restart) |
| Testing email dispatch | 10-30 min | 🟡 Medium |
| Tenant documentation | 1-2 hours | 🟢 Low |
| **Total** | **~2 hours** | |

---

## Files Included in This Migration

1. **Migration Script:**
   - `0024_tenant_mailersend_migration.py` → Copy to `migrations/versions/`

2. **Model Update:**
   - `TenantCommunicationSettings_model_excerpt.py` → Reference for updating `app/models/core.py`

3. **Template:**
   - `tenant_communication.html` → Replace `templates/superadmin/tenant_communication.html`

4. **Route Handler:**
   - `tenant_communication_route_updated.py` → Reference for updating `app/superadmin/__init__.py`

5. **This Guide:**
   - `MAILERSEND_TENANT_MIGRATION.md` → For team reference

---

## Security Considerations

✅ **Encrypted Storage:** API keys encrypted with Fernet (same as existing secrets)
✅ **No Browser Exposure:** Password fields never pre-filled, not echoed in responses
✅ **Audit Trail:** `updated_at` and `updated_by` fields track changes
✅ **CSRF Protection:** All forms use Flask-WTF CSRF tokens
✅ **Access Control:** Only superadmin can modify tenant communication settings
✅ **Rollback Safety:** SMTP columns retained, MailerSend fields dropped cleanly

---

## Support & Troubleshooting

**Issue:** "MailerSend configuration incomplete" warning
**Solution:** Ensure all three fields (API Key, From Email, From Name) are filled together or all empty

**Issue:** Emails still using old SMTP config
**Solution:** Check email service dispatch logic applies correct priority order (per-tenant MailerSend → global MailerSend → Web3Forms)

**Issue:** Migration fails on SQLite
**Solution:** SQLite's `batch_alter_table` recreates table — ensure no locks, and backup database first

---

**Version:** v5.0+
**Updated:** 2026-06-16
**Status:** Ready for deployment
