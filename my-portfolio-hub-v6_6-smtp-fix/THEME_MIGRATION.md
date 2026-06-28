# Theme Engine Migration Steps

Run these commands to ensure the `selected_theme` column exists in your database:

```bash
flask db migrate -m "add selected_theme to profile"
flask db upgrade
```

If migration conflicts occur, add the column manually:
```sql
ALTER TABLE profile ADD COLUMN IF NOT EXISTS selected_theme VARCHAR(64) NOT NULL DEFAULT 'default';
```

Then verify:
```sql
SELECT tenant_slug, selected_theme, plan FROM profile LIMIT 10;
```
