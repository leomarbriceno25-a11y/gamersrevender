import sqlite3

con = sqlite3.connect('/var/www/tienda/instance/tienda.db')
con.row_factory = sqlite3.Row
cur = con.cursor()

queue_table = cur.execute("SELECT COUNT(1) c FROM sqlite_master WHERE type='table' AND name='pincentral_capture_queue'").fetchone()['c']
pines_cols = [r['name'] for r in cur.execute("PRAGMA table_info(pines)").fetchall()]
queue_rows = cur.execute("SELECT COUNT(1) c FROM pincentral_capture_queue").fetchone()['c'] if queue_table else -1

print('queue_table=', queue_table)
print('pin_hash_col=', 'pin_hash' in pines_cols)
print('queue_rows=', queue_rows)

con.close()
