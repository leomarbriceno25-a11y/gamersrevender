import sqlite3

DB = '/var/www/tienda/instance/tienda.db'
PEDIDO_ID = 17646

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
cur = con.cursor()

ped = cur.execute(
    "SELECT p.*, u.nombre AS usuario_nombre, u.email AS usuario_email, pr.nombre AS producto_nombre, c.tipo AS categoria_tipo, "
    "pr.usa_api, pr.monto_api, pr.usa_razer, pr.usa_deltaforce, pr.usa_pincentral, pr.gamepoint_product_id, pr.gamepoint_package_id "
    "FROM pedidos p "
    "JOIN usuarios u ON u.id = p.usuario_id "
    "JOIN productos pr ON pr.id = p.producto_id "
    "LEFT JOIN categorias c ON c.id = pr.categoria_id "
    "WHERE p.id = ?",
    (PEDIDO_ID,),
).fetchone()

print('=== pedido ===')
print(dict(ped) if ped else None)

print('\n=== pines ligados al pedido ===')
pins = cur.execute(
    "SELECT id, producto_id, estado, usado_por, pedido_id, fecha_agregado, fecha_usado "
    "FROM pines WHERE pedido_id = ? ORDER BY id",
    (PEDIDO_ID,),
).fetchall()
for p in pins:
    print(dict(p))

print('\n=== transacciones relacionadas ===')
trs = cur.execute(
    "SELECT id, tipo, monto, saldo_anterior, saldo_nuevo, descripcion, pedido_id, fecha "
    "FROM transacciones WHERE pedido_id = ? OR descripcion LIKE ? ORDER BY id",
    (PEDIDO_ID, f'%{PEDIDO_ID}%'),
).fetchall()
for t in trs:
    print(dict(t))

print('\n=== pedidos del mismo usuario cerca de la hora ===')
if ped:
    rows = cur.execute(
        "SELECT id, producto_id, cantidad, total, id_juego, nombre_jugador, estado, referencia_externa, fecha_pedido "
        "FROM pedidos WHERE usuario_id = ? AND id BETWEEN ? AND ? ORDER BY id DESC",
        (ped['usuario_id'], PEDIDO_ID - 15, PEDIDO_ID + 15),
    ).fetchall()
    for r in rows:
        print(dict(r))

con.close()
