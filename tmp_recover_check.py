import sqlite3

DB = '/var/www/tienda/instance/tienda.db'
con = sqlite3.connect(DB)
cur = con.cursor()

print('=== stock antes ===')
for row in cur.execute("SELECT producto_id, COUNT(*) FROM pines WHERE estado='disponible' AND producto_id IN (121,124) GROUP BY producto_id ORDER BY producto_id"):
    print(row)

print('\n=== incidentes con tx_id (ids 3,6) ===')
for row in cur.execute("SELECT id, contexto, producto_id, order_id, transaction_id, detalle FROM pincentral_incidentes WHERE id IN (3,6) ORDER BY id"):
    print(row)

con.close()
