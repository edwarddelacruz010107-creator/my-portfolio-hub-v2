"""
tools/precheck_paymongo_backfill.py

Run this against prod BEFORE `flask db upgrade` applies
0039_backfill_paymongo_payment_method.

That migration flips subscriptions.payment_method from 'manual' to
'paymongo' wherever a paymongo_* identifier is present AND
payment_method is still the literal default 'manual'. It cannot tell
that default apart from a real manual PaymentMethod that happens to be
named "manual" (case-sensitive). This script surfaces that ambiguity so
you can check it in ~5 seconds instead of finding out after the backfill
runs.

Usage:
    python tools/precheck_paymongo_backfill.py

Exits non-zero if any risk is found, so it's safe to wire into a
pre-deploy check if you want.
"""
import sys


def main() -> int:
    from app import create_app, db
    from app.models.core import PaymentMethod, Subscription

    app = create_app()
    risk = 0

    with app.app_context():
        # 1. Any PaymentMethod literally named 'manual'?
        collisions = (
            db.session.query(PaymentMethod)
            .filter(PaymentMethod.name.ilike('manual'))
            .all()
        )
        if collisions:
            risk += 1
            print(f"⚠ {len(collisions)} PaymentMethod row(s) named 'manual' "
                  f"(case-insensitive) — the backfill cannot distinguish "
                  f"these from the bug's default:")
            for c in collisions:
                print(f"    id={c.id} tenant_id={c.tenant_id} name={c.name!r}")
        else:
            print("✓ No PaymentMethod rows literally named 'manual'.")

        # 2. Preview: how many rows would the backfill touch?
        candidates = (
            db.session.query(Subscription)
            .filter(
                Subscription.payment_method == 'manual',
                db.or_(
                    Subscription.paymongo_id.isnot(None),
                    Subscription.paymongo_subscription_id.isnot(None),
                    Subscription.paymongo_payment_id.isnot(None),
                ),
            )
            .all()
        )
        print(f"\nRows the backfill would update: {len(candidates)}")
        for s in candidates[:20]:
            print(f"    subscription_id={s.id} tenant_id={s.tenant_id} "
                  f"status={s.status} paymongo_id={s.paymongo_id!r}")
        if len(candidates) > 20:
            print(f"    ... and {len(candidates) - 20} more")

    if risk:
        print("\n✗ Resolve the collisions above before running the "
              "backfill migration, or accept the (small) risk of "
              "mislabeling those specific rows.")
        return 1

    print("\n✓ Safe to proceed with 0039_backfill_paymongo_payment_method.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
