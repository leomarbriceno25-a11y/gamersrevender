import sqlite3

DB = '/var/www/tienda/instance/tienda.db'

con = sqlite3.connect(DB)
cur = con.cursor()
print('=== pincentral_incidentes (ultimos 20) ===')
for row in cur.execute(
    "SELECT id, fecha, contexto, producto_id, product_code, pedido_id, order_id, transaction_id, detalle FROM pincentral_incidentes ORDER BY id DESC LIMIT 20"
):
    print('|'.join('' if v is None else str(v) for v in row))

print('\n=== pincentral_restock_auditoria (ultimos 20) ===')
for row in cur.execute(
    "SELECT id, fecha, producto_id, product_code, order_id, transaction_id, auth_status, capture_status, cantidad_solicitada, pines_recibidos, pines_agregados, detalle FROM pincentral_restock_auditoria ORDER BY id DESC LIMIT 20"
):
    print('|'.join('' if v is None else str(v) for v in row))

con.close()
