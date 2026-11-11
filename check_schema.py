import sys
sys.path.insert(0, r'E:\Dude\dude')

from core.associations import get_associations

ab = get_associations()

# Check database schema
with ab._db_lock:
    tables = ab.conn.execute('SELECT name FROM sqlite_master WHERE type="table"').fetchall()
    print('Tables:', [t[0] for t in tables])
    
    for table in ['insights', 'links', 'experiences']:
        try:
            cols = ab.conn.execute('PRAGMA table_info(' + table + ')').fetchall()
            print(table + ' columns:', [c[1] for c in cols])
        except Exception as e:
            print(table + ':', e)