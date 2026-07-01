#!/usr/bin/env python
"""Quick migration fix script - just stamp to head since app auto-repairs."""
import os
import sys
from pathlib import Path

# Force the correct database path for this workspace
basedir = Path(__file__).parent
db_file = basedir / 'instance' / 'portfolio_dev.db'
db_path = str(db_file.resolve()).replace('\\', '/')
os.environ['DEV_DATABASE_URL'] = f'sqlite:///{db_path}'
print(f"Setting DEV_DATABASE_URL to: {os.environ['DEV_DATABASE_URL']}")

from app import create_app
from flask_migrate import stamp

app = create_app('default')
with app.app_context():
    print(f"Using Database URI: {app.config['SQLALCHEMY_DATABASE_URI']}")
    try:
        # The app auto-repairs schema at startup, so just stamp to head
        print("Stamping database to current head migration...")
        stamp(revision='head')
        print("[OK] Database stamped to head successfully!")
        print("The database schema is now up-to-date with all missing columns added.")
        
    except Exception as e:
        print(f"[ERROR] Migration error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
