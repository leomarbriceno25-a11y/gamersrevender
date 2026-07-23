import sqlite3

con = sqlite3.connect('/var/www/tienda/instance/tienda.db')
con.row_factory = sqlite3.Row
cur = con.cursor()

rows = cur.execute(
    """
    SELECT p.id, p.fecha_pedido, p.total, p.id_juego, pr.nombre, pr.recarga_manual, p.referencia_externa
    FROM pedidos p
    JOIN productos pr ON pr.id = p.producto_id
    WHERE p.estado='procesando'
      AND pr.gamepoint_product_id > 0
      AND (p.referencia_externa IS NULL OR p.referencia_externa = '')
    ORDER BY p.id DESC
    LIMIT 20
    """
).fetchall()

print('count_recent=', len(rows))
for r in rows:
    print(dict(r))

con.close()
