"""
scripts/reset_admin_password.py
───────────────────────────────
Reset password for admin accounts (default portfolio or superadmin).

Usage:
  - Interactive (choose which account to reset):
      python scripts/reset_admin_password.py
  - Reset specific account with generated password:
      python scripts/reset_admin_password.py --type default
      python scripts/reset_admin_password.py --type superadmin
  - Reset specific account with custom password:
      python scripts/reset_admin_password.py --type default --password YourPassword123

Environment variables (for automation):
  ADMIN_TYPE       - 'default' or 'superadmin' (skips interactive prompt)
  ADMIN_PASSWORD   - Password to set (auto-generated if not provided)
"""
import os
import sys
import getpass
import secrets
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app
from app import db
from app.models import User
from app.repositories import user_repository


def get_account_choice():
    """Interactive menu to choose which account to reset."""
    print("\n" + "="*50)
    print("  RESET ADMIN PASSWORD")
    print("="*50)
    print("\nWhich account would you like to reset?\n")
    print("  1️⃣  Default Portfolio Admin  (username: admin)")
    print("  2️⃣  Superadmin Portal       (username: superadmin)")
    print("\n" + "-"*50)
    
    while True:
        choice = input("\nEnter choice (1 or 2): ").strip()
        if choice == '1':
            return 'default'
        elif choice == '2':
            return 'superadmin'
        else:
            print("❌ Invalid choice. Enter 1 or 2.")


def get_default_username(account_type):
    """Get default username for account type."""
    return 'admin' if account_type == 'default' else 'superadmin'


def reset_password(account_type, custom_password=None):
    """Reset password for the specified account type."""
    username = get_default_username(account_type)
    display_name = 'Default Portfolio Admin' if account_type == 'default' else 'Superadmin'
    
    with app.app_context():
        # Find the user
        user = user_repository.get_by_username(username)
        if not user:
            print(f"\n❌ User '{username}' not found in database.")
            print(f"   Make sure {display_name} account exists before resetting.")
            return False

        # Generate or use provided password
        generated = False
        password = custom_password
        if not password:
            password = secrets.token_urlsafe(12)
            generated = True

        # Reset password
        user.password = password
        db.session.commit()

        print(f"\n✅ Password reset for {display_name}:")
        print(f"   Username: {username}")
        if generated:
            print(f"   New temporary password: {password}")
            print("\n   ⚠️  IMPORTANT: Change this password immediately after login!")
            print(f"   Login at: http://localhost:5000/auth/login")
        else:
            print(f"   Password updated successfully!")
            print(f"   Login at: http://localhost:5000/auth/login")
        print()
        return True


def main():
    parser = argparse.ArgumentParser(
        description='Reset admin account password',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/reset_admin_password.py
  python scripts/reset_admin_password.py --type default
  python scripts/reset_admin_password.py --type superadmin --password MyNewPass123
        """
    )
    parser.add_argument('--type', choices=['default', 'superadmin'],
                        help='Account type to reset (default: interactive prompt)')
    parser.add_argument('--password', help='Password to set (auto-generated if not provided)')
    
    args = parser.parse_args()
    
    # Determine account type
    account_type = args.type or os.environ.get('ADMIN_TYPE')
    if not account_type:
        account_type = get_account_choice()
    
    # Determine password
    password = args.password or os.environ.get('ADMIN_PASSWORD')
    
    # Reset the password
    success = reset_password(account_type, password)
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
