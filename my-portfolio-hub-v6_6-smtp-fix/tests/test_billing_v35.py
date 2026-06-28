# ─────────────────────────────────────────────────────────────────────────────
# tests/test_billing_v35.py  –  Billing tests  (Portfolio CMS v3.5)
# ─────────────────────────────────────────────────────────────────────────────
# Run with:  pytest tests/test_billing_v35.py -v
# ─────────────────────────────────────────────────────────────────────────────

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

# Adjust imports to match your real project layout
from app.utils import (
    normalize_plan_name,
    get_plan_price,
    get_plan_price_label,
    BILLING_PLANS,
    YEARLY_DISCOUNT,
)
from app.services.billing import (
    plan_duration_days,
    activate_subscription,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_subscription(**kwargs):
    """Return a mock Subscription with sensible defaults."""
    sub = MagicMock()
    sub.plan          = kwargs.get("plan",          "Pro")
    sub.billing_cycle = kwargs.get("billing_cycle", "monthly")
    sub.is_active     = kwargs.get("is_active",     False)
    sub.expires_at    = kwargs.get("expires_at",    None)
    sub.started_at    = kwargs.get("started_at",    None)
    return sub


NOW = datetime(2026, 6, 7, 12, 0, 0, tzinfo=timezone.utc)


# ── normalize_plan_name ───────────────────────────────────────────────────────

class TestNormalizePlanName:
    def test_lowercase(self):
        assert normalize_plan_name("pro")        == "Pro"
        assert normalize_plan_name("basic")      == "Basic"
        assert normalize_plan_name("enterprise") == "Enterprise"

    def test_alias(self):
        assert normalize_plan_name("professional") == "Pro"
        assert normalize_plan_name("ent")          == "Enterprise"

    def test_empty(self):
        assert normalize_plan_name("") == "Basic"


# ── get_plan_price ────────────────────────────────────────────────────────────

class TestGetPlanPrice:
    def test_monthly_basic(self):
        assert get_plan_price("Basic", "monthly") == 1.00

    def test_monthly_pro(self):
        assert get_plan_price("Pro", "monthly") == 49.00

    def test_monthly_enterprise(self):
        assert get_plan_price("Enterprise", "monthly") == 99.00

    def test_yearly_is_discounted(self):
        monthly = get_plan_price("Pro", "monthly")
        yearly  = get_plan_price("Pro", "yearly")
        expected = round(monthly * 12 * YEARLY_DISCOUNT, 2)
        assert yearly == pytest.approx(expected, abs=0.01)

    def test_yearly_cheaper_than_monthly_x12(self):
        assert get_plan_price("Pro", "yearly") < get_plan_price("Pro", "monthly") * 12


# ── get_plan_price_label ──────────────────────────────────────────────────────

class TestGetPlanPriceLabel:
    def test_monthly_label_format(self):
        label = get_plan_price_label("Pro", "monthly")
        assert label == "₱49.00/mo"

    def test_yearly_label_contains_yr_and_save(self):
        label = get_plan_price_label("Pro", "yearly")
        assert "/yr" in label
        assert "Save ~17%" in label

    def test_currency_symbol_consistent(self):
        """Tenant card and superadmin view must use the same symbol."""
        for plan_key in BILLING_PLANS:
            m_label = get_plan_price_label(plan_key, "monthly")
            y_label = get_plan_price_label(plan_key, "yearly")
            assert m_label.startswith("₱"), f"{plan_key} monthly missing ₱"
            assert y_label.startswith("₱"), f"{plan_key} yearly missing ₱"


# ── plan_duration_days ────────────────────────────────────────────────────────

class TestPlanDurationDays:
    def test_monthly_is_30(self):
        assert plan_duration_days("Pro", "monthly") == 30

    def test_yearly_is_360(self):
        assert plan_duration_days("Pro", "yearly") == 360

    def test_basic_monthly(self):
        assert plan_duration_days("Basic", "monthly") == 30


# ── activate_subscription ─────────────────────────────────────────────────────

class TestActivateSubscription:

    def test_new_subscription_sets_dates(self):
        sub = make_subscription(expires_at=None)
        activate_subscription(sub, "Pro", "monthly", now=NOW)
        assert sub.started_at == NOW
        assert sub.expires_at == NOW + timedelta(days=30)

    def test_expired_subscription_resets_from_now(self):
        expired_at = NOW - timedelta(days=5)
        sub = make_subscription(expires_at=expired_at)
        activate_subscription(sub, "Pro", "monthly", now=NOW)
        assert sub.started_at == NOW
        assert sub.expires_at == NOW + timedelta(days=30)

    def test_active_renewal_extends_from_expiry(self):
        """Paying again while still active should extend from expires_at, not now."""
        future_expiry = NOW + timedelta(days=10)  # still active
        sub = make_subscription(expires_at=future_expiry)
        activate_subscription(sub, "Pro", "monthly", now=NOW)
        # Should add 30 days on top of the existing 10-day buffer
        expected = future_expiry + timedelta(days=30)
        assert sub.expires_at == expected

    def test_yearly_renewal_adds_360_days(self):
        future_expiry = NOW + timedelta(days=10)
        sub = make_subscription(expires_at=future_expiry)
        activate_subscription(sub, "Pro", "yearly", now=NOW)
        assert sub.expires_at == future_expiry + timedelta(days=360)

    def test_double_renewal_stacks(self):
        """Two monthly renewals in a row should add 60 days total to an active sub."""
        future_expiry = NOW + timedelta(days=5)
        sub = make_subscription(expires_at=future_expiry)
        activate_subscription(sub, "Pro", "monthly", now=NOW)
        activate_subscription(sub, "Pro", "monthly", now=NOW)
        assert sub.expires_at == future_expiry + timedelta(days=60)

    def test_plan_and_cycle_stored(self):
        sub = make_subscription(expires_at=None)
        activate_subscription(sub, "pro", "yearly", now=NOW)
        assert sub.plan == "Pro"
        assert sub.billing_cycle == "yearly"

    def test_price_paid_matches_helper(self):
        sub = make_subscription(expires_at=None)
        activate_subscription(sub, "Enterprise", "yearly", now=NOW)
        assert sub.price_paid == pytest.approx(get_plan_price("Enterprise", "yearly"), abs=0.01)
