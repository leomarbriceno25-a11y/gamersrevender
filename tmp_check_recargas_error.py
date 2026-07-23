import sqlite3

DB = '/var/www/tienda/instance/tienda.db'
TARGET_IDS = [16738, 16737, 16736]

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
cur = con.cursor()

print('=== columnas pedidos ===')
for row in cur.execute("PRAGMA table_info(pedidos)"):
    print(f"- {row['name']} ({row['type']})")

print('\n=== pedidos objetivo ===')
for pid in TARGET_IDS:
    r = cur.execute(
        "SELECT p.*, pr.nombre AS producto_nombre, u.email AS usuario_email "
        "FROM pedidos p "
        "LEFT JOIN productos pr ON pr.id = p.producto_id "
        "LEFT JOIN usuarios u ON u.id = p.usuario_id "
        "WHERE p.id = ?",
        (pid,),
    ).fetchone()
    if not r:
        print(f"pedido {pid}: no existe")
        continue
    print(f"\n[pedido {pid}] usuario={r['usuario_email']} producto={r['producto_nombre']} estado={r['estado']} fecha={r['fecha_pedido']}")
    for k in r.keys():
        if any(x in k.lower() for x in ['error', 'api', 'status', 'response', 'mensaje', 'motivo', 'detalle', 'refer']):
            print(f"  {k}={r[k]}")

print('\n=== transacciones ligadas ===')
for pid in TARGET_IDS:
    trs = cur.execute(
        "SELECT id, tipo, monto, descripcion, fecha FROM transacciones WHERE pedido_id = ? ORDER BY id",
        (pid,),
    ).fetchall()
    if not trs:
        print(f"pedido {pid}: sin transacciones")
        continue
    print(f"pedido {pid}:")
    for t in trs:
        print(f"  - tx#{t['id']} tipo={t['tipo']} monto={t['monto']} fecha={t['fecha']} desc={t['descripcion']}")

# Buscar tablas de auditoría con pedido_id o referencia
print('\n=== tablas potenciales de auditoria ===')
tables = [r['name'] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
for t in tables:
    cols = [c['name'] for c in cur.execute(f"PRAGMA table_info({t})").fetchall()]
    if 'pedido_id' in cols or 'order_id' in cols or 'referenceno' in cols or 'reference' in cols:
        print(f"- {t}: {', '.join(cols)}")

con.close()
