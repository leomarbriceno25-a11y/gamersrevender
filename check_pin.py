import sqlite3
conn = sqlite3.connect('/var/www/tienda/instance/tienda.db')
r = conn.execute("SELECT id, pin, estado, usado_por, pedido_id FROM pines WHERE id=20").fetchone()
print(f'ID: {r[0]}, PIN: {r[1]}, Estado: {r[2]}, Usado_por: {r[3]}, Pedido: {r[4]}')
conn.close()
