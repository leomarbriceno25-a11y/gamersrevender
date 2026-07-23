import sqlite3

con = sqlite3.connect('/var/www/tienda/instance/tienda.db')
con.row_factory = sqlite3.Row
cur = con.cursor()

for pid in (16737, 16738):
    print(f"\n=== movimientos relacionados pedido {pid} ===")
    rows = cur.execute(
        "SELECT id, usuario_id, tipo, monto, saldo_anterior, saldo_nuevo, descripcion, pedido_id, fecha "
        "FROM transacciones "
        "WHERE pedido_id = ? OR descripcion LIKE ? "
        "ORDER BY id",
        (pid, f"%{pid}%"),
    ).fetchall()
    if not rows:
        print('sin movimientos')
    for r in rows:
        print(dict(r))

con.close()
