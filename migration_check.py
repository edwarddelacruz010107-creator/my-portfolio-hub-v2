import sqlite3
from pathlib import Path
p = Path('storage/portfolio_core_dev.db')
print('db exists:', p.exists())
conn = sqlite3.connect(p)
c = conn.cursor()
print('\ntables:')
for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall():
    print(' -', r[0])
try:
    c.execute('SELECT version_num FROM alembic_version')
    rows = c.fetchall()
    print('\nalembic_version:')
    for row in rows:
        print(' -', row[0])
except Exception as e:
    print('\nfailed reading alembic_version:', e)

print('\nPRAGMA table_info(inquiries):')
for col in c.execute("PRAGMA table_info('inquiries')"):
    print(' -', col[1], col[2], 'nullable=' + str(not col[3]))
conn.close()
