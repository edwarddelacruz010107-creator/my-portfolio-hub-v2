# Project Like Notifications Upgrade

- Creates a tenant dashboard notification after an authenticated user likes a public project.
- Skips self-likes from the same tenant.
- Adds a visible notification bell to the Studio top bar.
- Polls unread counts every 30 seconds and refreshes when the tab becomes visible.
- Adds a heart icon for project-like notifications.
- Uses the existing subscription_notifications table; no migration is required.
