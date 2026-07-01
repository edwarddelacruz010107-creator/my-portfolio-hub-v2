"""
AUTH __init__.py ADDITIONS — apply exactly these two edits.

────────────────────────────────────────────────────────────────────────────
EDIT 1 — Bottom of app/auth/__init__.py

Append the following single line as the last executable statement of the
module so the new signup / verify / google routes attach to the `auth`
blueprint before the blueprint is registered on the app.
────────────────────────────────────────────────────────────────────────────
"""

# Register additional auth routes (signup, verify, google). Import must be
# LAST — the module references the `auth` blueprint defined above.
from app.auth import routes_signup  # noqa: E402,F401


"""
────────────────────────────────────────────────────────────────────────────
EDIT 2 — app/__init__.py (create_app), immediately BEFORE
   app.register_blueprint(auth_blueprint, url_prefix='/auth')

Wire the OAuth registry so Authlib clients are ready before the auth
blueprint mounts.
────────────────────────────────────────────────────────────────────────────
"""

# ── Google OAuth (Authlib) ───────────────────────────────────────────────────
from app.auth.oauth import init_oauth
init_oauth(app)
