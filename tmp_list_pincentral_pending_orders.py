import sqlite3

con = sqlite3.connect('/var/www/tienda/instance/tienda.db')
con.row_factory = sqlite3.Row
cur = con.cursor()

rows = cur.execute(
    "SELECT id, fecha, producto_id, product_code, order_id, detalle "
    "FROM pincentral_incidentes "
    "WHERE contexto = 'restock_auth' "
    "AND (transaction_id IS NULL OR trim(transaction_id) = '') "
    "ORDER BY id"
).fetchall()

print('total=', len(rows))
for r in rows:
    print(f"inc#{r['id']} | fecha={r['fecha']} | producto_id={r['producto_id']} | code={r['product_code']} | order_id={r['order_id']} | detalle={r['detalle']}")

con.close()
