import sqlite3

con = sqlite3.connect('/var/www/tienda/instance/tienda.db')
con.row_factory = sqlite3.Row
cur = con.cursor()
order_id = 20204

pedido = cur.execute(
    "SELECT p.id, p.usuario_id, p.producto_id, p.estado, p.total, p.id_juego, p.nombre_jugador, p.referencia_externa, p.codigo_entregado, p.fecha_pedido, pr.nombre as producto_nombre, pr.usa_pincentral, pr.pincentral_product_code FROM pedidos p LEFT JOIN productos pr ON pr.id=p.producto_id WHERE p.id=?",
    (order_id,),
).fetchone()
print('PEDIDO:', dict(pedido) if pedido else None)

print('\nAUDITORIA:')
rows = cur.execute("SELECT id, proveedor, etapa, estado, detalle, referencia, fecha FROM recargas_auditoria WHERE pedido_id=? ORDER BY id DESC LIMIT 8", (order_id,)).fetchall()
for r in rows:
    print(dict(r))

print('\nTRANSACCIONES_PEDIDO:')
for r in cur.execute("SELECT id, tipo, monto, descripcion, fecha FROM transacciones WHERE pedido_id=? ORDER BY id DESC LIMIT 5", (order_id,)).fetchall():
    print(dict(r))

print('\nULTIMO_PROCESANDO:')
for r in cur.execute("SELECT id, estado, referencia_externa, fecha_pedido FROM pedidos WHERE estado='procesando' ORDER BY id DESC LIMIT 5").fetchall():
    print(dict(r))

con.close()
