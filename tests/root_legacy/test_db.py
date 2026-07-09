import os
import sys
from config import DevelopmentConfig
import sqlite3

print(f'Database URI: {DevelopmentConfig.SQLALCHEMY_DATABASE_URI}')
print(f'Instance folder exists: {os.path.exists("instance")}')
print(f'DB file exists: {os.path.exists("instance/portfolio_dev.db")}')
print()

# Test SQLite connection
try:
    conn = sqlite3.connect('instance/portfolio_dev.db')
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    print(f'✅ SQLite connection successful')
    print(f'✅ Tables found: {len(tables)}')
    for table in tables:
        print(f'  - {table[0]}')
    conn.close()
except Exception as e:
    print(f'❌ Error: {e}')
    sys.exit(1)
