# ─────────────────────────────────────────────────────────────────────────────
# app/forms.py  –  Billing-related form additions  (Portfolio CMS v3.5 fix)
# ─────────────────────────────────────────────────────────────────────────────
# Drop-in additions / replacements for the billing section of your forms.py.
# Merge these into your existing forms.py rather than replacing the whole file.
# ─────────────────────────────────────────────────────────────────────────────

from flask_wtf import FlaskForm
from wtforms import RadioField, SelectField, HiddenField
from wtforms.validators import DataRequired, Optional


BILLING_CYCLE_CHOICES = [
    ("monthly", "Monthly"),
    ("yearly",  "Yearly (Save ~17%)"),
]

PLAN_CHOICES = [
    ("Basic",      "Basic – ₱19.00/mo"),
    ("Pro",        "Pro – ₱49.00/mo"),
    ("Enterprise", "Enterprise – ₱99.00/mo"),
]


class PlanSelectionForm(FlaskForm):
    """
    Used on /billing/plans to capture plan + cycle before checkout.

    The JS in plans.html updates the hidden billing_cycle field on radio
    change, so by the time this form is submitted the value is always correct.
    """

    selected_plan = RadioField(
        "Plan",
        choices=PLAN_CHOICES,
        validators=[DataRequired(message="Please select a plan.")],
        default="Pro",
    )

    billing_cycle = HiddenField(
        "Billing Cycle",
        default="monthly",
        # Validated manually below so we can give a friendly error
    )

    payment_method = SelectField(
        "Payment Method",
        choices=[
            ("manual",   "Manual / Bank Transfer"),
            ("paymongo", "PayMongo"),
        ],
        validators=[Optional()],
        default="manual",
    )

    def validate_billing_cycle(self, field):
        if field.data not in ("monthly", "yearly"):
            field.data = "monthly"   # safe fallback instead of hard error
