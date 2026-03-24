import sqlite3
db = sqlite3.connect("/var/www/tienda/instance/tienda.db")
db.row_factory = sqlite3.Row
# Ultimos 10 pedidos GamePoint
rows = db.execute("""
    SELECT p.id, p.estado, p.referencia_externa, p.nombre_jugador, p.fecha_pedido, pr.nombre as producto
    FROM pedidos p JOIN productos pr ON p.producto_id = pr.id
    WHERE pr.gamepoint_product_id > 0
    ORDER BY p.id DESC LIMIT 15
""").fetchall()
for r in rows:
    ref = r['referencia_externa'] or '(sin ref)'
    print(f"#{r['id']} | {r['estado']:12} | ref: {ref:25} | {r['producto']} | {r['fecha_pedido']}")
db.close()
