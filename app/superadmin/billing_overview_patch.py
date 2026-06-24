"""
PATCH FILE — billing_overview fix for Portfolio CMS v3.6.1
=============================================================
Bug:   jinja2.exceptions.UndefinedError: 'dict object' has no attribute 'profile'
File:  templates/superadmin/billing_overview.html:58
Cause: billing_overview() passed flat dicts from tenant_billing_summary() but
       the template expects row.profile (ORM object), row.subscription, etc.

HOW TO APPLY
------------
1. Open  app/superadmin/__init__.py
2. Find the existing billing_overview() function (decorated with
   @superadmin.route('/billing') and @superadmin_required)
3. Replace the ENTIRE function body with the one below.
4. Confirm get_plan_price_label is already imported from app.utils.
   If not, add it to the import block at the top of __init__.py:
       from app.utils import ..., get_plan_price_label

Do NOT copy the decorator lines — they already exist in __init__.py.
Only replace the def billing_overview(): block.
"""

# ── REPLACE THIS BLOCK IN app/superadmin/__init__.py ─────────────────────────

@superadmin.route('/billing')
@superadmin_required
def billing_overview():
    """Subscription dashboard: MRR, active subs, webhook log, tenant billing table."""
    metrics = compute_billing_metrics()

    profiles = Profile.query.order_by(Profile.tenant_slug).all()

    tenant_rows = []
    for profile in profiles:
        try:
            sub = profile.current_subscription()
            billing_summary = tenant_billing_summary(profile)

            row = {
                # ORM object — template uses .tenant_slug, .name, .id
                'profile': profile,
                # Subscription ORM object — template calls .paymongo_dashboard_url()
                'subscription': sub,
                # Flat billing fields consumed directly in the table
                'plan': billing_summary.get(
                    'plan', normalize_plan_name(profile.plan or 'Basic')
                ),
                'plan_price_label': (
                    get_plan_price_label(
                        billing_summary.get('plan', profile.plan or 'Basic')
                    )
                    if sub else ''
                ),
                'status': billing_summary.get('status', 'inactive'),
                'next_billing': billing_summary.get('expires_at'),
            }
        except Exception:
            # Defensive fallback: one bad profile must never crash the whole page
            row = {
                'profile': profile,
                'subscription': None,
                'plan': normalize_plan_name(profile.plan or 'Basic'),
                'plan_price_label': '',
                'status': 'unknown',
                'next_billing': None,
            }

        tenant_rows.append(row)

    recent_webhooks = (
        WebhookEvent.query
        .order_by(WebhookEvent.received_at.desc())
        .limit(25)
        .all()
    )

    return render_template(
        'superadmin/billing_overview.html',
        metrics=metrics,
        tenants=tenant_rows,
        recent_webhooks=recent_webhooks,
        billing_plans=BILLING_PLANS,
        page_title='Subscription Overview',
    )

# ── END REPLACEMENT BLOCK ─────────────────────────────────────────────────────
