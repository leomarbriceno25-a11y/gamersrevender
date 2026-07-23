import sqlite3

con = sqlite3.connect('/var/www/tienda/instance/tienda.db')
con.row_factory = sqlite3.Row
cur = con.cursor()

exists = cur.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name='recargas_auditoria'"
).fetchone()
print('table_exists=', bool(exists))

if exists:
    cols = [r['name'] for r in cur.execute("PRAGMA table_info(recargas_auditoria)").fetchall()]
    print('cols=', cols)
    rows = cur.execute(
        "SELECT id, pedido_id, proveedor, etapa, estado, fecha FROM recargas_auditoria ORDER BY id DESC LIMIT 5"
    ).fetchall()
    print('last_rows=', [dict(r) for r in rows])

con.close()
