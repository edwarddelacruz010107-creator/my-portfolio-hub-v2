"""
scripts/update_admin_email.py
──────────────────────────
Update the `admin` user's email address.

Usage:
  python scripts/update_admin_email.py
Or set `ADMIN_USERNAME` and `ADMIN_EMAIL` env vars.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app
from app import db
from app.models import User

USERNAME = os.environ.get('ADMIN_USERNAME', 'admin')
NEW_EMAIL = os.environ.get('ADMIN_EMAIL', 'admin@portfolio.local')

def main():
    with app.app_context():
        u = User.query.filter_by(username=USERNAME).first()
        if not u:
            print(f'No user found with username: {USERNAME}')
            return
        old = u.email
        u.email = NEW_EMAIL
        db.session.add(u)
        db.session.commit()
        print(f"Updated user '{USERNAME}' email: {old} -> {NEW_EMAIL}")

if __name__ == '__main__':
    main()
