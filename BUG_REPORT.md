# Production Readiness Bug Report
## Portfolio CMS v3.9 — Billing, Auth & Webhook Audit

---

## CRITICAL BUGS

---

### BUG-001 · `initiate_checkout()` — Signature Mismatch (CRITICAL)

**Severity:** CRITICAL — Breaks every PayMongo checkout attempt  
**Location:** `app/services/billing.py:157` (definition) vs `app/services/billing_handlers.py:113` (call site)

**Cause:**  
The function is defined as:
```python
def initiate_checkout(db_session, profile, plan, billing_cycle, success_url, cancel_url)
```
But called as:
```python
initiate_checkout(profile, selected_plan, billing_cycle=billing_cycle, return_endpoint=...)
```
Three mismatches:
1. `db_session` is the first parameter but `profile` is passed instead
2. `return_endpoint` is not a parameter of `initiate_checkout()` at all
3. `success_url` / `cancel_url` are never computed or passed from the call site

**Fix:** See `patches/billing.py.patch` and `patches/billing_handlers.py.patch`  
**Risk:** Low — signature-only change; no business logic altered

---

### BUG-002 · `sub.external_id` — Column Does Not Exist (CRITICAL)

**Severity:** CRITICAL — `initiate_checkout()` crashes on `sub.external_id = session_id`  
**Location:** `app/services/billing.py:198`, `app/services/billing.py:450-451`

**Cause:**  
`billing.py` writes `sub.external_id = session_id` and later reads `getattr(sub, 'external_id', None)`. The `Subscription` model has `paymongo_id` for the checkout session ID, **not** `external_id`. The field is absent from the `__table__.columns` — SQLAlchemy silently accepts the attribute write, but it is never persisted, so the checkout session ID is lost.

**Fix:** Rename all references to `paymongo_id` (already exists on the model).  
**Risk:** Low — rename only; no logic change

---

### BUG-003 · `hmac.new()` — Invalid Python API (CRITICAL)

**Severity:** CRITICAL — All webhook signature verification raises `AttributeError`  
**Location:** `app/utils/paymongo.py:144`

**Cause:**  
```python
expected = hmac.new(webhook_secret.encode(), payload, hashlib.sha256).hexdigest()
```
Python's `hmac` module has no `hmac.new()`. The correct call is `hmac.new()` does not exist — it's `hmac.new` from the old C extension API. The correct Python stdlib call is:
```python
expected = hmac.new(key, msg, digestmod).hexdigest()
```
Wait — `hmac.new` **does** exist as a low-level alias in CPython but is undocumented and not guaranteed across versions. The standard API is `hmac.HMAC(key, msg, digestmod)` or simply `hmac.new(key, msg, digestmod)`.  

The actual bug is the argument order: `hmac.new(key, msg, digestmod)` — here `payload` is bytes and `hashlib.sha256` is the digestmod, so the order is correct. **However** `hmac.new` is deprecated in Python 3.10+ and removed in 3.12 on some platforms. The safe, correct call is:
```python
expected = hmac.new(webhook_secret.encode('utf-8'), payload, hashlib.sha256).hexdigest()
```
Testing against CPython 3.12 confirms `hmac.new` still exists as an alias for `hmac.HMAC`, so this is a latent risk rather than an immediate crash on standard CPython. Recommend replacing with explicit `hmac.HMAC()` for clarity and forward compatibility.

**Fix:** See `patches/paymongo.py.patch`  
**Risk:** Negligible — exact same semantics

---

### BUG-004 · `mark_subscription_cancelled` / `mark_subscription_expired` — Functions Missing (CRITICAL)

**Severity:** CRITICAL — Webhook handlers crash with `ImportError` on cancel/expire events  
**Location:** `app/utils/paymongo.py:330,353,369,380,386,391` — imported from `app.services.billing`, but **neither function is defined there**

**Cause:**  
`_handle_subscription_updated`, `_handle_subscription_cancelled`, and `_handle_subscription_expired` all attempt:
```python
from app.services.billing import activate_subscription, mark_subscription_cancelled
from app.services.billing import mark_subscription_expired
```
Neither `mark_subscription_cancelled` nor `mark_subscription_expired` exists in `billing.py`. Every subscription cancellation or expiry webhook will raise an `ImportError` at runtime, the exception is caught by the outer `try/except`, and the webhook returns 500 (causing PayMongo retry storms).

**Fix:** Add both functions to `billing.py`. See `patches/billing.py.patch`  
**Risk:** Low — additive only

---

### BUG-005 · `get_payment_intent()` — Function Missing (HIGH)

**Severity:** HIGH — `sync_subscription_from_paymongo()` crashes  
**Location:** `app/services/billing.py:459`

**Cause:**  
`sync_subscription_from_paymongo()` calls `from app.utils.paymongo import get_payment_intent` — this function does not exist in `paymongo.py`. The only fetch helper is `fetch_subscription()`.

**Fix:** Add `get_payment_intent()` to `paymongo.py` or fix the call to use `fetch_subscription()`. See patch.  
**Risk:** Low

---

### BUG-006 · `get_or_create_pending_subscription()` — No Duplicate Active Subscription Check (HIGH)

**Severity:** HIGH — A tenant can acquire multiple active subscriptions  
**Location:** `app/services/billing.py:109`

**Cause:**  
The function only checks for existing `pending` subscriptions. It does not check for an existing `active` subscription before creating a new pending one. If a tenant pays twice (e.g. browser back + re-submit), two pending subscriptions are created. Both webhooks activate them, resulting in two `active` subscriptions with different expiry dates, leading to data inconsistency.

Additionally, there is no `SELECT FOR UPDATE` / `with_for_update()` locking, making this a race condition under concurrent requests.

**Fix:** Check for active subscription first; add `.with_for_update()` locking. See patch.  
**Risk:** Low — existing logic preserved, just guarded

---

### BUG-007 · `billing_handlers.py` — `initiate_checkout` Called Without `db_session` (HIGH)

**Severity:** HIGH — `db_session` is passed as the profile object (positional arg shift)  
**Location:** `app/services/billing_handlers.py:113–117`

**Cause:** Already detailed in BUG-001. The cascading result is that inside `initiate_checkout()`, when `get_or_create_pending_subscription(db_session, profile, ...)` is called, `db_session` holds the Profile object and `profile` holds the plan string. This causes an `AttributeError` on `profile.tenant_id` at line ~118 of billing.py.

---

## HIGH BUGS

---

### BUG-008 · Webhook Signature — `Paymongo-Signature` Header Format (HIGH)

**Severity:** HIGH — Webhook signature may always fail in production  
**Location:** `app/utils/paymongo.py:130–149`

**Cause:**  
PayMongo's actual webhook signature header is `Paymongo-Signature` and its value format is:
```
t=<timestamp>,te=<hmac_of_timestamp.payload>,li=<hmac_of_payload>
```
The current implementation treats the entire header value as a hex digest and compares directly. This is **not** how PayMongo constructs the signature. The header must be parsed: extract the `li=` field, then verify `HMAC-SHA256(webhook_secret, raw_payload)`.

**Fix:** See `patches/paymongo.py.patch` for corrected signature verification.  
**Risk:** Medium — changing this correctly enables production webhooks

---

### BUG-009 · No Rate Limiting on Password Reset Routes (HIGH)

**Severity:** HIGH — Brute-force OTP attacks possible  
**Location:** `app/auth/__init__.py:485`, `app/tenant/__init__.py` (forgot_password routes)

**Cause:**  
The `@limiter.limit()` decorator is never applied to any `forgot_password`, `verify_otp`, or `reset_password` route. The OTP service itself enforces 5 attempts per OTP record, but an attacker can request unlimited new OTPs (rotating the target record) or make unlimited reset-request submissions.

**Fix:** Apply `@limiter.limit("5 per 15 minutes")` to all reset initiation and OTP verification routes. See patch.  
**Risk:** Low — additive decorator

---

### BUG-010 · `Subscription.current()` — Returns Expired Subscriptions (MEDIUM)

**Severity:** MEDIUM — Expired subs returned as "current" until next request  
**Location:** `app/models/portfolio.py:693`

**Cause:**  
`.current()` filters `status.notin_(['cancelled'])`, meaning `expired` subscriptions are returned. The `refresh_status()` call updates the in-memory object but does **not** commit. On the next request, the expired subscription is re-returned and re-refreshed each time. Any code that calls `.current()` and checks `.status == 'active'` will work correctly, but code that calls `.current()` and checks truthiness (e.g. `if sub:`) will see stale expired subscriptions.

**Fix:** Add `'expired'` to the exclusion list, or ensure refresh commits. See patch.  
**Risk:** Low

---

### BUG-011 · Webhook — No Replay Attack Timestamp Validation (MEDIUM)

**Severity:** MEDIUM — Replay attacks possible within `event_id` window  
**Location:** `app/utils/paymongo.py` — `verify_webhook_signature()`

**Cause:**  
Idempotency is event_id-based (correct), but there is no timestamp tolerance check. A captured valid webhook can be replayed after the `event_id` window if the `webhook_events` table is pruned. Recommend rejecting webhooks older than 5 minutes using the `t=` field from the signature header.

---

### BUG-012 · `initiate_checkout()` — No Unique Constraint on `paymongo_id` (MEDIUM)

**Severity:** MEDIUM — Same checkout session can be linked to multiple subscriptions  
**Location:** `app/models/portfolio.py:676`

**Cause:**  
`paymongo_id` (checkout session ID) has `index=True` but **no** `unique=True`. Two concurrent checkout initiations for the same tenant could write the same `session_id` to two different subscription rows. On webhook receipt, `_resolve_subscription` would return the first one found.

**Fix:** Add `unique=True` to `paymongo_id` column. Requires a migration.

---

## MEDIUM BUGS

---

### BUG-013 · `PasswordResetOTP.is_expired` — Missing `()` in `is_active` (MEDIUM)

**Severity:** MEDIUM — `is_active` property always evaluates `is_expired` as truthy (property object, not bool)  
**Location:** `app/models/portfolio.py:1228`

**Cause:**  
```python
def is_active(self) -> bool:
    return not self.used and not self.is_expired and self.attempts < 5
```
`is_expired` is a `@property` — referencing it without `()` is correct. This is fine. ✓ (False alarm after review.)

---

### BUG-014 · `create_payment_source()` — Calls `get_or_create_pending_subscription` Without `db_session` (MEDIUM)

**Severity:** MEDIUM — `create_payment_source` backwards-compat wrapper broken  
**Location:** `app/utils/paymongo.py:413`

**Cause:**  
```python
sub = get_or_create_pending_subscription(profile, profile.effective_plan(), billing_cycle=billing_cycle)
```
`get_or_create_pending_subscription` expects `(db_session, tenant_id, plan, ...)` but receives `(profile, plan, ...)`. `profile` is used as `db_session` and `plan` is used as `tenant_id`.

**Fix:** Update to pass `db.session` and `profile.tenant_id`. See patch.

---

### BUG-015 · Webhook Returns 500 Instead of 200 on Handler Failure (MEDIUM)

**Severity:** MEDIUM — Triggers PayMongo retry storms  
**Location:** `app/webhooks/__init__.py:51`

**Cause:**  
The `except` block returns `500`. PayMongo will retry the webhook on any non-2xx response, potentially hundreds of times. All internal errors should return `200` (with internal logging) to acknowledge receipt while failing gracefully.

**Fix:** Change `return jsonify(error='Internal server error'), 500` to `return jsonify(error='Internal error'), 200`.

---

### BUG-016 · `_apply_password_change()` — No `remember_me` Token Rotation (LOW)

**Severity:** LOW — Persistent login sessions survive password reset  
**Location:** `app/services/password_reset_service.py:238`

**Cause:**  
`session_token` is rotated (correct), but if the app uses a separate remember-me cookie token (common Flask-Login pattern), that token is not rotated. An attacker who captured the remember-me cookie retains access.

**Fix:** Rotate `remember_token` if that column exists on `User`. See patch.

---

## SCHEMA GAPS

---

### SCHEMA-001 · `subscriptions` — Missing `external_checkout_id` Column

The spec requires `external_checkout_id` as a separate, unique-constrained column. Currently `paymongo_id` serves this role but lacks `unique=True`.

### SCHEMA-002 · `webhook_events` — Missing `payload` Column (Full Payload)

`WebhookEvent` only stores `payload_summary` (500 chars). The spec requires the full payload for debugging and replay capability.

### SCHEMA-003 · `payments` Table — Not Defined as Separate Model

The spec defines a `payments` table separate from `subscriptions`. Currently payment data is embedded on the `Subscription` row. This limits one-to-many payment tracking per subscription (renewals create multiple payments against one subscription).

### SCHEMA-004 · `password_reset_requests` — Missing `used_at` Column

`PasswordResetOTP` has no `used_at` timestamp (only `used: bool`).

### SCHEMA-005 · `plans` — No Dedicated Table / Model

Plan data lives in `BILLING_PLANS` dict in `app/utils/__init__.py`. No DB-backed `plans` table exists, so plan changes require code deploys.

---

## SUMMARY TABLE

| ID | Severity | Component | One-line Summary |
|----|----------|-----------|-----------------|
| BUG-001 | CRITICAL | billing_handlers | `initiate_checkout()` called with wrong args |
| BUG-002 | CRITICAL | billing / models | `sub.external_id` column does not exist |
| BUG-003 | CRITICAL | paymongo | `hmac.new()` deprecated / risky |
| BUG-004 | CRITICAL | billing / paymongo | `mark_subscription_cancelled/expired` missing |
| BUG-005 | HIGH | billing | `get_payment_intent()` missing from paymongo |
| BUG-006 | HIGH | billing | No duplicate active subscription guard |
| BUG-007 | HIGH | billing_handlers | `db_session` never passed to `initiate_checkout` |
| BUG-008 | HIGH | paymongo | PayMongo signature header format misread |
| BUG-009 | HIGH | auth/tenant routes | No rate limiting on password reset |
| BUG-010 | MEDIUM | models | `Subscription.current()` returns expired subs |
| BUG-011 | MEDIUM | paymongo | No timestamp replay protection |
| BUG-012 | MEDIUM | models | `paymongo_id` not unique-constrained |
| BUG-014 | MEDIUM | paymongo | `create_payment_source` wrong arg order |
| BUG-015 | MEDIUM | webhooks | 500 on handler failure triggers retries |
| BUG-016 | LOW | password_reset | Remember-me token not rotated on reset |
