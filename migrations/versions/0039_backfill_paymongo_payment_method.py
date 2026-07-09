"""
0039 — Backfill subscriptions.payment_method for historical PayMongo rows

CONTEXT
───────
initiate_checkout() (app/services/billing/billing.py) called
get_or_create_pending_subscription() without payment_method='paymongo',
so every subscription that ever went through PayMongo checkout — success,
failure, retry, superadmin resync — inherited the function's default of
'manual' and kept it for its entire lifecycle. Nothing downstream
(webhook handler, sync_subscription_from_paymongo) ever corrected it.
Fixed at the source in the same change that introduces this migration.

This migration is DATA-ONLY. It corrects existing rows written before the
source fix; it does not touch schema (payment_method already exists and
already accepts 'paymongo' per PAYMENT_METHOD_TYPES in app/models/core.py).

SCOPING — why this WHERE clause and not a broader one
───────────────────────────────────────────────────────
A row is only backfilled if BOTH are true:
  1. payment_method = 'manual' (exactly the function's untouched default —
     never a real method name written by manual_billing.py, which always
     overwrites with the actual PaymentMethod.name, e.g. 'GCash').
  2. At least one paymongo_* identifier is populated (paymongo_id is set
     at checkout-session creation time; paymongo_subscription_id /
     paymongo_payment_id are set only on confirmed activation).

This deliberately does NOT touch rows where payment_method is anything
other than the literal default 'manual' — including a subscription that
has a stale paymongo_id from an abandoned checkout attempt but was later
paid manually and correctly overwritten by manual_billing.py. Overwriting
those would destroy a correct, already-verified manual payment method
label. If any of your PaymentMethod rows happen to be literally named
"manual" (case-sensitive match), this backfill cannot distinguish that
from the bug's default — check `SELECT DISTINCT name FROM payment_methods`
before running against prod if that's a possibility.

SAFE: no columns/tables altered or dropped. Idempotent — re-running finds
nothing to update on a second pass.
"""
from alembic import op
import sqlalchemy as sa

revision      = '0039_backfill_paymongo_payment_method'
down_revision = '0038_discount_activation_wiring'
branch_labels = None
depends_on    = None


def upgrade():
    op.execute(
        sa.text(
            """
            UPDATE subscriptions
            SET payment_method = 'paymongo'
            WHERE payment_method = 'manual'
              AND (
                    paymongo_id IS NOT NULL
                 OR paymongo_subscription_id IS NOT NULL
                 OR paymongo_payment_id IS NOT NULL
              )
            """
        )
    )


def downgrade():
    # Lossy by necessity: the pre-migration value was always the buggy
    # 'manual' default for every row this migration touches (that's the
    # WHERE clause), so reverting to 'manual' exactly restores pre-upgrade
    # state for this migration's scope. Does not affect any row this
    # migration didn't touch.
    op.execute(
        sa.text(
            """
            UPDATE subscriptions
            SET payment_method = 'manual'
            WHERE payment_method = 'paymongo'
              AND (
                    paymongo_id IS NOT NULL
                 OR paymongo_subscription_id IS NOT NULL
                 OR paymongo_payment_id IS NOT NULL
              )
            """
        )
    )
