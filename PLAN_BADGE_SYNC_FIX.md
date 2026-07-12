# Paid Plan Badge Synchronization Fix

Updated the tenant admin dashboard so a verified active subscription takes priority over stale trial-state values.

## Result
- Basic subscription displays `Basic Plan`
- Pro subscription displays `Pro Plan`
- Enterprise subscription displays `Enterprise Plan`
- Trial countdown appears only while the subscription is actually in trial state
- Topbar no longer shows generic `Licensed`; it shows the active plan
- Dashboard hero badge uses the active paid-plan style
- Active subscribers see `Manage Plan` instead of `Upgrade Plan`

The legacy internal codes (`starter`, `business`) remain compatible through the global `plan_display` filter.
