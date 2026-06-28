# Production Readiness Report
## Portfolio CMS v3.9 — Final Assessment

---

## Scorecard

| Domain | Score | Status |
|--------|-------|--------|
| **Security** | 6/10 | ⚠️ ISSUES |
| **Reliability** | 5/10 | ⚠️ ISSUES |
| **Payments** | 3/10 | 🔴 CRITICAL |
| **Subscriptions** | 5/10 | ⚠️ ISSUES |
| **Authentication** | 7/10 | ⚠️ ISSUES |
| **Database** | 6/10 | ⚠️ ISSUES |
| **Performance** | 7/10 | ✅ ACCEPTABLE |

---

## Verdict

# 🔴 NOT READY

**The system cannot be deployed to production in its current state.**

---

## Justification

### Why NOT READY

**1. The PayMongo checkout flow is completely broken (BUG-001, BUG-002, BUG-007)**

Every single PayMongo checkout attempt will fail before a payment page is shown:

- `billing_handlers.py` calls `initiate_checkout(profile, plan, ...)` — missing `db_session` as first arg and passing `return_endpoint=...` which is not a parameter
- Inside `initiate_checkout`, it writes `sub.external_id = session_id` — this column does not exist on the model; the checkout session ID is silently lost
- Result: 100% of PayMongo checkout initiations fail with either `AttributeError` or silent data loss

**2. All subscription cancellation and expiry webhooks crash (BUG-004)**

`mark_subscription_cancelled` and `mark_subscription_expired` are imported from `app.services.billing` in three webhook handlers but are defined nowhere. Every `subscription.cancelled` and `subscription.expired` webhook from PayMongo raises `ImportError`, is caught by the outer try/except, and returns HTTP 500 — triggering PayMongo retry storms.

**3. Webhook HMAC signature verification will fail in production (BUG-008)**

The PayMongo webhook signature header has the format `t=...,te=...,li=...`. The current code treats the entire header value as a bare hex digest and compares it directly — this will always return `False` in production, meaning all valid webhooks are rejected with 401 and PayMongo retries indefinitely.

**4. `hmac.new()` is deprecated and removed in some Python 3.12+ builds (BUG-003)**

The webhook signing code uses `hmac.new()` rather than `hmac.HMAC()`. While CPython 3.12 still supports it as an undocumented alias, it is removed in some alternative Python distributions and will break without warning on upgrade.

**5. No rate limiting on password reset routes (BUG-009)**

The forgot-password and OTP verification routes have no `@limiter.limit()` decorators. An attacker can submit unlimited reset requests per account per minute and make unlimited OTP guesses by rotating OTP records, bypassing the 5-attempt-per-record guard.

---

## Domain Breakdown

### Security (6/10)

**Passed:**
- OTP hashed with SHA-256 — raw OTP never stored ✅
- Generic error messages prevent account enumeration ✅  
- Session token rotated on password change ✅
- Tenant isolation enforced in OTP verification ✅
- CSRF exempt only on webhook endpoint, correctly scoped ✅

**Failed:**
- No rate limiting on reset initiation or OTP verification routes ❌ (BUG-009)
- Webhook HMAC verification broken in production ❌ (BUG-008)
- No timestamp replay protection on webhooks ❌ (BUG-011)
- Remember-me tokens not rotated on password reset ❌ (BUG-016)

---

### Reliability (5/10)

**Passed:**
- Webhook handler wrapped in try/except ✅
- PayMongo event deduplication via `event_id` ✅
- `activate_subscription` is idempotent for same payment_id ✅

**Failed:**
- Webhook returns 500 on internal error → triggers PayMongo retry storm ❌ (BUG-015)
- `mark_subscription_cancelled/expired` missing → crash on cancel/expire webhooks ❌ (BUG-004)
- No `SELECT FOR UPDATE` on subscription creation → race condition ❌ (BUG-006)

---

### Payments (3/10)

**Passed:**
- Checkout session creates pending subscription before redirect ✅
- Amount stored in centavos, correctly converted ✅

**Failed:**
- Checkout initiation crashes 100% of the time due to signature mismatch ❌ (BUG-001, BUG-007)
- Checkout session ID (`session_id`) silently discarded ❌ (BUG-002)
- `create_payment_source()` backward-compat wrapper broken ❌ (BUG-014)
- `paymongo_id` column not unique-constrained ❌ (BUG-012)
- No dedicated `payments` table ❌ (SCHEMA-003)

---

### Subscriptions (5/10)

**Passed:**
- State flow documented and enforced partially ✅
- `Subscription.current()` with refresh logic ✅
- `get_or_create_pending_subscription` prevents some duplicates ✅
- Additive renewal logic correct ✅

**Failed:**
- No guard against creating pending sub when active sub exists ❌ (BUG-006)
- `mark_subscription_cancelled/expired` missing ❌ (BUG-004)
- `Subscription.current()` includes expired subs in query ❌ (BUG-010)
- `trialing` and `past_due` states missing from model ❌ (SCHEMA gap)

---

### Authentication (7/10)

**Passed:**
- Three-flow password reset architecture (superadmin/admin/tenant) ✅
- OTP lifecycle management solid ✅
- Generic error messages throughout ✅
- Session token rotation on reset ✅
- Tenant isolation via `contact_email` validation ✅

**Failed:**
- No rate limiting on any reset route ❌ (BUG-009)
- Remember-me token not rotated ❌ (BUG-016)

---

### Database (6/10)

**Passed:**
- Foreign keys present on most relationships ✅
- Key indexes defined ✅
- Timezone-aware datetime columns used ✅
- `event_id` unique constraint on `webhook_events` ✅

**Failed:**
- `paymongo_id` not unique-constrained ❌ (BUG-012)
- No `external_checkout_id` column (spec requirement) ❌ (SCHEMA-001)
- No `payments` table ❌ (SCHEMA-003)
- No `plans` table ❌ (SCHEMA-005)
- `webhook_events` missing full payload column ❌ (SCHEMA-002)
- `password_reset_otps` missing `used_at` ❌ (SCHEMA-004)

---

### Performance (7/10)

**Passed:**
- Composite indexes on hot query paths ✅
- `current()` query uses `notin_` with index-covered columns ✅
- `compute_billing_metrics()` loads all subs once, iterates in Python ✅

**Watch:** `compute_billing_metrics()` loads all subscriptions into memory. At scale (10k+ tenants), this needs a single aggregated SQL query.

---

## Remediation Priority

### P0 — Deploy Blocker (fix before any production traffic)

1. **BUG-001/007** — Fix `initiate_checkout()` signature and call site
2. **BUG-002** — Replace `sub.external_id` with `sub.paymongo_id`
3. **BUG-004** — Add `mark_subscription_cancelled()` and `mark_subscription_expired()` to `billing.py`
4. **BUG-008** — Fix PayMongo compound signature header parsing

### P1 — Fix Before First Payment Transaction

5. **BUG-003** — Replace `hmac.new()` with `hmac.HMAC()`
6. **BUG-009** — Add rate limiting to all password reset routes
7. **BUG-015** — Return 200 on internal webhook errors

### P2 — Fix Within First Sprint Post-Launch

8. **BUG-005** — Fix `sync_subscription_from_paymongo()` to use `fetch_subscription()`
9. **BUG-006** — Add duplicate active subscription guard + `with_for_update()`
10. **BUG-014** — Fix `create_payment_source()` argument order
11. **BUG-010** — Exclude expired subs from `Subscription.current()`
12. Run `upgrade.sql` migration

### P3 — Technical Debt (schedule within 30 days)

13. **BUG-011** — Webhook timestamp replay protection
14. **BUG-012** — Add `UNIQUE` constraint to `paymongo_id`
15. **BUG-016** — Remember-me token rotation on password reset
16. **SCHEMA-003** — Dedicated `payments` table
17. **SCHEMA-005** — DB-backed `plans` table
18. Full test suite execution against staging

---

## Path to READY

After applying all P0 + P1 patches from this report and running `upgrade.sql`:

| Domain | Projected Score |
|--------|----------------|
| Security | 8/10 |
| Reliability | 8/10 |
| Payments | 8/10 |
| Subscriptions | 8/10 |
| Authentication | 9/10 |
| Database | 8/10 |
| Performance | 7/10 |

**Projected Verdict: ✅ READY (pending P0+P1 patches and regression testing)**
