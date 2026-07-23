import sqlite3

con = sqlite3.connect('/var/www/tienda/instance/tienda.db')
con.row_factory = sqlite3.Row
cur = con.cursor()

pedido_id = 18989

print('TRANSACCIONES POR DESCRIPCION:')
for r in cur.execute("SELECT id,tipo,monto,descripcion,pedido_id,fecha FROM transacciones WHERE descripcion LIKE ? ORDER BY id", (f'%{pedido_id}%',)):
    print(dict(r))

print('\nPINES USADOS EN PEDIDO:')
for r in cur.execute("SELECT id,producto_id,estado,usado_por,pedido_id,fecha_usado FROM pines WHERE pedido_id=?", (pedido_id,)):
    print(dict(r))

print('\nSTOCK PRODUCTO 156 DISPONIBLE:')
row = cur.execute("SELECT COUNT(1) c FROM pines WHERE producto_id=156 AND estado='disponible'").fetchone()
print(row['c'])

print('\nULTIMOS PEDIDOS PRODUCTO 156:')
for r in cur.execute("SELECT id,estado,fecha_pedido,total,id_juego,codigo_entregado FROM pedidos WHERE producto_id=156 ORDER BY id DESC LIMIT 12"):
    print(dict(r))

con.close()
