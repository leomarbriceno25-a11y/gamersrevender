import sqlite3
import json

DB = '/var/www/tienda/instance/tienda.db'
order_id = 20204

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
cur = con.cursor()

pedido = cur.execute(
    """
    SELECT p.*, pr.nombre as producto_nombre, pr.usa_pincentral, pr.pincentral_product_code
    FROM pedidos p
    LEFT JOIN productos pr ON pr.id = p.producto_id
    WHERE p.id = ?
    """,
    (order_id,),
).fetchone()

print('=== PEDIDO ===')
print(dict(pedido) if pedido else None)

print('\n=== TRANSACCIONES (pedido_id) ===')
for r in cur.execute("SELECT * FROM transacciones WHERE pedido_id = ? ORDER BY id DESC LIMIT 10", (order_id,)).fetchall():
    print(dict(r))

uid = pedido['usuario_id'] if pedido else None
if uid:
    print('\n=== TRANSACCIONES (usuario recientes) ===')
    for r in cur.execute("SELECT * FROM transacciones WHERE usuario_id = ? ORDER BY id DESC LIMIT 10", (uid,)).fetchall():
        print(dict(r))

print('\n=== RECARGAS AUDITORIA ===')
for r in cur.execute("SELECT * FROM recargas_auditoria WHERE pedido_id = ? ORDER BY id DESC LIMIT 20", (order_id,)).fetchall():
    d = dict(r)
    payload = d.get('payload')
    if isinstance(payload, str) and len(payload) > 300:
        d['payload'] = payload[:300] + '...'
    print(d)

print('\n=== PINCENTRAL INCIDENTES (order/tx) ===')
if pedido:
    ref = str(pedido['referencia_externa'] or '').strip()
    rows = cur.execute(
        "SELECT * FROM pincentral_incidentes WHERE pedido_id = ? OR transaction_id = ? ORDER BY id DESC LIMIT 20",
        (order_id, ref),
    ).fetchall()
    for r in rows:
        d = dict(r)
        payload = d.get('payload')
        if isinstance(payload, str) and len(payload) > 300:
            d['payload'] = payload[:300] + '...'
        print(d)

print('\n=== SOLICITUDES RECARGA (si existe) ===')
try:
    rows = cur.execute("SELECT * FROM solicitudes_recarga WHERE id = ?", (order_id,)).fetchall()
    for r in rows:
        print(dict(r))
except Exception as e:
    print('No solicitudes_recarga match / table issue:', e)

con.close()
