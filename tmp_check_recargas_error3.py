import sqlite3

con = sqlite3.connect('/var/www/tienda/instance/tienda.db')
con.row_factory = sqlite3.Row
cur = con.cursor()

for pid in (16738, 16737):
    print(f"\n=== transacciones pedido {pid} ===")
    trs = cur.execute(
        "SELECT id, tipo, monto, saldo_anterior, saldo_nuevo, descripcion, pedido_id, fecha "
        "FROM transacciones WHERE pedido_id = ? ORDER BY id",
        (pid,),
    ).fetchall()
    if not trs:
        print('sin transacciones')
    for t in trs:
        print(dict(t))

print('\n=== stock producto 156 (Tarjeta Semanal) ===')
total = cur.execute("SELECT COUNT(*) c FROM pines WHERE producto_id = 156").fetchone()['c']
disp = cur.execute("SELECT COUNT(*) c FROM pines WHERE producto_id = 156 AND estado = 'disponible'").fetchone()['c']
print({'total': total, 'disponible': disp})

print('\n=== últimos pines producto 156 ===')
rows = cur.execute(
    "SELECT id, estado, usado_por, pedido_id, fecha_agregado, fecha_usado "
    "FROM pines WHERE producto_id = 156 ORDER BY id DESC LIMIT 5"
).fetchall()
for r in rows:
    print(dict(r))

con.close()
