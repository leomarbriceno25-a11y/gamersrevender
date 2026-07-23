import json
import sqlite3

con = sqlite3.connect('/var/www/tienda/instance/tienda.db')
con.row_factory = sqlite3.Row
cur = con.cursor()

print('schema solicitudes_recarga:')
for r in cur.execute("PRAGMA table_info(solicitudes_recarga)"):
    print(dict(r))

print('\nultimas 20 solicitudes_recarga:')
for r in cur.execute("SELECT * FROM solicitudes_recarga ORDER BY id DESC LIMIT 20"):
    d = dict(r)
    for k, v in list(d.items()):
        if isinstance(v, str) and len(v) > 800:
            d[k] = v[:800] + '...'
    print(json.dumps(d, ensure_ascii=False))

con.close()
