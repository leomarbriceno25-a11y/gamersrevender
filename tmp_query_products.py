import sqlite3

DB = '/var/www/tienda/instance/tienda.db'
con = sqlite3.connect(DB)
cur = con.cursor()

print('=== productos 121/124 ===')
for row in cur.execute("SELECT id, nombre, pincentral_product_code, usa_pincentral, stock_minimo, stock_objetivo, activo FROM productos WHERE id IN (121,124)"):
    print(row)

print('\n=== stock disponible local ===')
for row in cur.execute("SELECT producto_id, COUNT(*) FROM pines WHERE estado='disponible' AND producto_id IN (121,124) GROUP BY producto_id"):
    print(row)

print('\n=== incidentes por minuto (17:13-17:18) ===')
for row in cur.execute("SELECT substr(fecha,1,16) as minuto, contexto, COUNT(*) FROM pincentral_incidentes WHERE fecha >= '2026-04-07 17:13:00' AND fecha <= '2026-04-07 17:18:00' GROUP BY minuto, contexto ORDER BY minuto, contexto"):
    print(row)

con.close()
