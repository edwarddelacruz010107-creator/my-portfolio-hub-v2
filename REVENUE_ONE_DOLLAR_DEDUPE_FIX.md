# Revenue one-dollar deduplication fix

The shared analytics service now counts only one active provider subscription per tenant.
Duplicate Dodo rows created by checkout retries or webhook retries no longer double the
configured Basic monthly amount from USD 1.00 to USD 2.00.

Expected result for one active Basic Monthly tenant:
- MRR: USD 1.00
- Recorded normalized Dodo revenue: USD 1.00
- Active Dodo subscriptions: 1
- Original localized provider collection remains available separately (for example PHP 65.98)
