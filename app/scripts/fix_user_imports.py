#!/usr/bin/env python3
"""
fix_user_imports.py

Automatically fix all imports of User from app.models.user to use app.models instead.
This resolves the SQLAlchemy "Table 'users' is already defined" error.

Usage:
    python fix_user_imports.py
"""

import os
import re
import sys
from pathlib import Path

# List of files that import User from app.models.user
FILES_TO_FIX = [
    'app/auth/__init__.py',
    'app/auth/totp.py',
    'app/services/password_reset_service.py',
    'app/services/tenant_admin.py',
    'app/__init__.py',
    'app/superadmin/__init__.py',
    'app/forms/__init__.py',
    'migrations/env.py',
    'scripts/create_superadmin.py',
    'scripts/add_tenant_columns.py',
    'scripts/reset_admin_password.py',
    'scripts/update_admin_email.py',
    'scripts/create_admin.py',
    'scripts/seed_default_tenant.py',
    'test_tenant_fix.py',
    'test_default_admin_isolation.py',
    'run.py',
    'test_tenant_security_v37.py',
    'tests/test_default_tenant_hardening.py',
]


def fix_imports_in_file(filepath):
    """
    Replace all occurrences of:
        from app.models.user import User
    
    With:
        from app.models import User
    
    Returns True if changes were made, False otherwise.
    """
    if not os.path.exists(filepath):
        return False, "File not found"
    
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            original_content = f.read()
    except Exception as e:
        return False, f"Read error: {e}"
    
    # Replace the import statement
    new_content = re.sub(
        r'from app\.models\.user import User\b',
        'from app.models import User',
        original_content
    )
    
    # Check if changes were made
    if new_content == original_content:
        return False, "No changes needed"
    
    # Write the fixed content back
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)
        return True, "Fixed"
    except Exception as e:
        return False, f"Write error: {e}"


def main():
    """Main entry point."""
    print("=" * 80)
    print("FIX USER MODEL IMPORTS")
    print("=" * 80)
    print()
    print("This script fixes imports of User from app.models.user → app.models")
    print()
    
    fixed_count = 0
    failed_count = 0
    skipped_count = 0
    
    for filepath in FILES_TO_FIX:
        success, message = fix_imports_in_file(filepath)
        
        if success:
            print(f"✓ {filepath:<50} {message}")
            fixed_count += 1
        elif message == "File not found":
            print(f"- {filepath:<50} (not present)")
            skipped_count += 1
        elif message == "No changes needed":
            print(f"- {filepath:<50} (no changes needed)")
            skipped_count += 1
        else:
            print(f"✗ {filepath:<50} ERROR: {message}")
            failed_count += 1
    
    print()
    print("=" * 80)
    print(f"SUMMARY: {fixed_count} fixed, {skipped_count} skipped, {failed_count} errors")
    print("=" * 80)
    
    if fixed_count > 0:
        print()
        print("✓ Next steps:")
        print("  1. Delete the duplicate file: rm app/models/user.py")
        print("  2. Verify exports: python -c \"from app.models import User\"")
        print("  3. Test startup: python -m flask run")
        print("  4. Commit changes: git add -A && git commit -m \"fix: remove duplicate User model\"")
        print()
    
    return 0 if failed_count == 0 else 1


if __name__ == '__main__':
    sys.exit(main())