import sqlite3

DB = '/var/www/tienda/instance/tienda.db'
PID = 17425

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
cur = con.cursor()

ped = cur.execute(
    "SELECT p.*, pr.nombre AS producto_nombre, pr.categoria_id, c.tipo AS categoria_tipo, "
    "pr.usa_api, pr.gamepoint_product_id, pr.gamepoint_package_id, pr.recarga_manual "
    "FROM pedidos p "
    "JOIN productos pr ON pr.id = p.producto_id "
    "LEFT JOIN categorias c ON c.id = pr.categoria_id "
    "WHERE p.id = ?",
    (PID,),
).fetchone()

print('=== pedido ===')
print(dict(ped) if ped else None)

if ped:
    uid = ped['usuario_id']
    nearby = cur.execute(
        "SELECT id, producto_id, cantidad, total, id_juego, nombre_jugador, codigo_entregado, estado, referencia_externa, fecha_pedido "
        "FROM pedidos WHERE usuario_id = ? AND id >= ? ORDER BY id DESC LIMIT 10",
        (uid, PID - 20),
    ).fetchall()
    print('\n=== pedidos recientes del usuario ===')
    for r in nearby:
        d = dict(r)
        if d.get('codigo_entregado'):
            d['codigo_entregado'] = str(d['codigo_entregado'])[:120]
        print(d)

con.close()
