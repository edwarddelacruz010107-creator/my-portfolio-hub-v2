# Discount Checkout Integration — Patch Instructions

This patch wires the **already-built** discount system
(`DiscountCampaign`, `discount_service`, superadmin CRUD) into the
tenant-facing **checkout flow**, which was the only piece missing.

The rest of the discount system in your project is already complete:
model, migration `0037_discount_campaigns.py`, repository, service,
`DiscountCampaignForm`, superadmin routes, and 3 templates.

---

## What this patch changes

| File | Change | Risk |
| --- | --- | --- |
| `app/services/billing/discount_checkout.py` | **NEW** — session stash + activation hook | none (new file) |
| `app/services/billing/billing_handlers.py` | **REPLACE** — adds coupon field handling to existing handlers, no signature changes | low |
| `app/templates/billing/_coupon_partial.html` | **NEW** — coupon input + totals block | none |
| `app/templates/billing/_plans.html` | **1-line include** | trivial |
| Webhook / manual-approval handler | **1 function call** — see below | trivial |

Nothing else is touched. `discount_service.py`, `initiate_checkout()`,
PayMongo signature verification, `activate_subscription()`, and the
superadmin CRUD are all left unchanged.

---

## Step 1 — Drop in the new & replaced files

Copy from this patch bundle into your project (paths mirror your repo):

```
app/services/billing/discount_checkout.py       (new)
app/services/billing/billing_handlers.py        (replace existing)
app/templates/billing/_coupon_partial.html      (new)
```

No new dependencies, no new migrations. The existing
`0037_discount_campaigns.py` migration already provides the tables.

---

## Step 2 — Show the coupon input on the plans page

Edit `app/templates/billing/_plans.html`. Find the billing-cycle block
(around line 91, just after the yearly `<label>`) and add this ONE line
inside the same `<form>`, before the payment methods section:

```jinja
    </label>
  </div>

+ {% include 'billing/_coupon_partial.html' %}

  {# Step 3: Payment methods #}
```

That's it — the partial reads `discount_quote` and `coupon_code` from
the context, both of which are now supplied by `billing_plans_context()`.

---

## Step 3 — Redeem on activation (the ONE call your app needs)

The stashed coupon is redeemed exactly when a subscription becomes
active. Add one call inside each activation path you have.

### 3a. PayMongo webhook (automatic activations)

Find the place in your PayMongo webhook handler where you call
`activate_subscription(...)` after verifying `payment.paid`. Add these
four lines right after activation, **before** the outer commit:

```python
from app.services.billing import discount_checkout

discount_checkout.apply_on_activation(
    tenant_id=subscription.tenant_id,
    subscription=subscription,
    plan=subscription.plan,
    billing_cycle=subscription.billing_cycle,
    commit=False,   # let the webhook's outer transaction commit
)
```

`apply_on_activation` never raises. If the coupon is expired, over
limit, or the tenant's session is gone, it logs a warning and returns
`None` — the subscription still activates at full price.

> **Session caveat for webhooks.** Webhooks come from PayMongo, not
> from the tenant's browser, so `flask.session` won't have the stash
> in that request. Two safe options:
>
> **Preferred:** persist the coupon on the pending Subscription row
> when checkout starts. Add a `pending_coupon_code` column (nullable
> string) and:
>
> 1. In `handle_billing_plans_post`, after `stash_coupon`, also do
>    `sub.pending_coupon_code = raw_code or None` on the pending
>    subscription and commit.
> 2. In the webhook, read `subscription.pending_coupon_code` and
>    call `discount_service.quote_discount(code=...)` +
>    `discount_service.redeem_discount(...)` directly instead of
>    `apply_on_activation`. Clear `pending_coupon_code` after.
>
> **Quick option:** for now, only the manual-approval path (3b)
> redeems coupons. PayMongo checkouts fall through to full price
> until you add the column. This keeps the patch minimal and
> guarantees no billing regression.

### 3b. Manual payment approval (superadmin approves a submission)

Find the superadmin action that approves a `PaymentSubmission` and
calls `activate_subscription(...)`. Add the same four lines right
after activation. Because approval happens in a superadmin request
(not the tenant's), you also need to persist the coupon —
recommendation is the same `pending_coupon_code` column approach
above.

---

## Step 4 — Verify without touching production

1. `flask db upgrade` — no-op (all discount tables already exist).
2. Log in as superadmin, go to **Discounts & Promotions**, create a
   coupon: 20% off, monthly, active.
3. Log in as a tenant, go to **Billing → Plans**, enter the code,
   click Apply. You should see the Original / Discount / Total block.
4. Click a manual payment method and confirm the suggested amount on
   the payment page matches Total.
5. After activation (webhook or approval), check
   `discount_redemptions` — a row should exist with `amount_after`
   equal to what the tenant paid, and `DiscountCampaign.usage_count`
   should have incremented.

Invalid / expired / over-limit codes should flash a warning and let
checkout proceed at full price.

---

## Rollback

Delete `discount_checkout.py`, revert `billing_handlers.py` and the
one-line `_plans.html` include. The database rows created by any
redemptions are harmless — they can stay or be truncated manually.
No schema changes to undo.
