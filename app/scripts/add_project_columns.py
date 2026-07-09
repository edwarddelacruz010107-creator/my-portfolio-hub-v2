import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'portfolio_dev.db')
DB_PATH = os.path.normpath(DB_PATH)

print('DB path:', DB_PATH)
if not os.path.exists(DB_PATH):
    print('Database file not found:', DB_PATH)
    raise SystemExit(1)

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

cur.execute("PRAGMA table_info('projects')")
cols = [r[1] for r in cur.fetchall()]
print('Existing columns:', cols)

added = False
if 'framework' not in cols:
    print('Adding column: framework')
    cur.execute("ALTER TABLE projects ADD COLUMN framework VARCHAR(120) DEFAULT ''")
    added = True
else:
    print('Column framework already exists')

if 'language' not in cols:
    print('Adding column: language')
    cur.execute("ALTER TABLE projects ADD COLUMN language VARCHAR(120) DEFAULT ''")
    added = True
else:
    print('Column language already exists')

if added:
    conn.commit()
    print('Columns added and committed.')
else:
    print('No changes made.')

conn.close()
