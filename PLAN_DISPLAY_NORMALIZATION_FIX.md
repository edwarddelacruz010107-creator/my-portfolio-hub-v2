# Plan display normalization fix

- Keeps legacy database codes such as `starter` and `business` unchanged for compatibility.
- Registers a global Jinja `plan_display` filter.
- Displays only Trial, Basic, Pro, Enterprise, and Administrator in user-facing billing screens.
- Updated Studio billing, public billing, Superadmin subscription management, license views, and shared payment templates.
