# Alembic 0055 deployment fix

The Dodo migration must use the complete previous revision identifier:

```python
revision = '0055'
down_revision = '0054_oauth_local_account_setup'
```

Render previously deployed an older Git commit containing `down_revision = '0054'`.
Commit and push this release before redeploying.

Verify in GitHub after pushing:

```bash
grep -n "down_revision" migrations/versions/0055_add_dodo_payments_fields.py
```

Expected:

```text
10:down_revision = '0054_oauth_local_account_setup'
```
