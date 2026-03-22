import sqlite3
conn = sqlite3.connect('/var/www/tienda/instance/tienda.db')
# Fix pedido #33 - add referencia_externa from nombre_jugador which has the ref
conn.execute("UPDATE pedidos SET referencia_externa='GP7DEBA0C9C794531103' WHERE id=33")
conn.commit()
r = conn.execute("SELECT id, estado, nombre_jugador, referencia_externa FROM pedidos WHERE id=33").fetchone()
print(f'Pedido #{r[0]}: estado={r[1]}, jugador={r[2]}, ref={r[3]}')
conn.close()
