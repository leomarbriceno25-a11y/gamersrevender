import sqlite3
conn = sqlite3.connect('/var/www/tienda/instance/tienda.db')
conn.execute("UPDATE pines SET estado='disponible', usado_por=NULL, pedido_id=NULL, fecha_usado=NULL WHERE pin='408C012C-FE8B-4E05-8A80-EA8A2CB885FD'")
conn.commit()
r = conn.execute("SELECT id, pin, estado FROM pines WHERE pin='408C012C-FE8B-4E05-8A80-EA8A2CB885FD'").fetchone()
print(f'PIN {r[1]} -> estado: {r[2]}')
conn.close()
