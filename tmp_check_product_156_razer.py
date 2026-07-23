import sqlite3
con = sqlite3.connect('/var/www/tienda/instance/tienda.db')
con.row_factory = sqlite3.Row
cur = con.cursor()
r = cur.execute("SELECT id,nombre,usa_razer,razer_paquete,usa_api,activo FROM productos WHERE id=156").fetchone()
print(dict(r) if r else None)
con.close()
