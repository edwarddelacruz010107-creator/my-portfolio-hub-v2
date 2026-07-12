# Analytics Peso Symbol Fix

- Platform Overview and Subscription Monitor now read the reporting currency directly from Plan Settings via the shared currency service.
- When Plan Settings uses PHP, all main analytics cards, provider totals, and revenue chart labels display the Philippine peso symbol (`₱`) and `PHP` consistently.
- Prevents legacy plan dictionaries from pairing a PHP-converted amount with a stale dollar symbol.
- No database migration required.
