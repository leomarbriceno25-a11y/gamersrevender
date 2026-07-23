import json
import sqlite3

con = sqlite3.connect('/var/www/tienda/instance/tienda.db')
con.row_factory = sqlite3.Row
cur = con.cursor()

rows = cur.execute(
    "SELECT id, fecha, contexto, producto_id, product_code, order_id, transaction_id, detalle "
    "FROM pincentral_incidentes "
    "WHERE contexto IN ('restock_capture','restock_capture_retry') "
    "ORDER BY id DESC LIMIT 20"
).fetchall()

print('total=', len(rows))
for r in rows:
    print(json.dumps(dict(r), ensure_ascii=False))

con.close()
