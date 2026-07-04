# ─────────────────────────────────────────────────────────────────────────────
# app/superadmin/billing_plans.py  –  Superadmin plan-settings view
# Portfolio CMS v3.5 fix
#
# Mount this blueprint in app/superadmin/__init__.py:
#
#     from app.superadmin.billing_plans import bp as billing_plans_bp
#     superadmin_bp.register_blueprint(billing_plans_bp)
#
# Route will be accessible at  /superadmin/billing/plans
# ─────────────────────────────────────────────────────────────────────────────

from flask import Blueprint, render_template, request, flash, redirect, url_for
from app.utils import BILLING_PLANS, get_plan_price, get_plan_price_label, normalize_plan_name
from app.superadmin.blueprint import superadmin_required

bp = Blueprint("superadmin_billing_plans", __name__, url_prefix="/billing")


@bp.route("/plans", methods=["GET"])
@superadmin_required
def view_plans():
    """
    Display all plans with prices for BOTH monthly and yearly cycles.
    Uses the same BILLING_PLANS dict as the tenant card view → consistent ₱ symbol.
    """
    plan_data = {}
    for key, plan in BILLING_PLANS.items():
        plan_data[key] = {
            **plan,
            "monthly_label": get_plan_price_label(key, "monthly"),
            "yearly_label":  get_plan_price_label(key, "yearly"),
            "price_monthly": get_plan_price(key, "monthly"),
            "price_yearly":  get_plan_price(key, "yearly"),
        }

    return render_template(
        "superadmin/billing/plans.html",
        plans=plan_data,
        page_title="Plan Settings",
    )


@bp.route("/plans/edit/<plan_key>", methods=["GET", "POST"])
@superadmin_required
def edit_plan(plan_key: str):
    """
    Simple override editor.  Persists to PlatformSetting (key-value store).
    Extend this if you add a BillingPlanConfig model.

    KNOWN LIMITATION (unchanged by FIX [RBAC-01], flagging for visibility):
    the POST handler below mutates the module-level BILLING_PLANS dict
    in-process. This is NOT persisted to the database and will NOT be
    consistent across multiple gunicorn worker processes (each worker has
    its own copy of this dict) and will NOT survive a restart/deploy.
    Auth is now enforced (this fix); durable, cross-worker-consistent
    persistence still requires the BillingPlanConfig model this docstring
    already calls out — do not treat this route as production-safe for
    actual price changes until that lands.
    """
    norm = normalize_plan_name(plan_key)
    plan = BILLING_PLANS.get(norm)
    if not plan:
        flash(f"Plan '{plan_key}' not found.", "error")
        return redirect(url_for(".view_plans"))

    if request.method == "POST":
        # In production: validate + persist to DB / PlatformSetting
        new_monthly = request.form.get("price_monthly", type=float)
        new_duration = request.form.get("duration_days", type=int)

        # Apply in-memory (restart-persistent storage left to implementer)
        if new_monthly is not None and new_monthly > 0:
            BILLING_PLANS[norm]["price_monthly"] = new_monthly
            BILLING_PLANS[norm]["price"] = new_monthly
            # Recalculate yearly with same discount
            from app.utils import YEARLY_DISCOUNT
            BILLING_PLANS[norm]["price_yearly"] = round(new_monthly * 12 * YEARLY_DISCOUNT, 2)

        if new_duration is not None and new_duration > 0:
            BILLING_PLANS[norm]["duration_days"] = new_duration

        flash(f"{norm} plan updated successfully.", "success")
        return redirect(url_for(".view_plans"))

    return render_template(
        "superadmin/billing/edit_plan.html",
        plan=plan,
        plan_key=norm,
        monthly_label=get_plan_price_label(norm, "monthly"),
        yearly_label=get_plan_price_label(norm, "yearly"),
        page_title=f"Edit {norm} Plan",
    )
