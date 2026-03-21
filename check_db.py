import sqlite3
conn = sqlite3.connect('tienda.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()
print("=== CATEGORIAS ===")
for r in c.execute("SELECT id, nombre, tipo FROM categorias ORDER BY orden"):
    print(f"  ID:{r['id']} | {r['nombre']} | tipo={r['tipo']}")
print("\n=== PINES EN ALMACEN ===")
for r in c.execute("SELECT p.producto_id, pr.nombre, p.estado, COUNT(*) as total FROM pines p JOIN productos pr ON p.producto_id = pr.id GROUP BY p.producto_id, p.estado"):
    print(f"  Producto:{r['nombre']} | estado={r['estado']} | total={r['total']}")
print("\n=== PEDIDO 292 ===")
r = c.execute("SELECT p.*, pr.nombre as prod, c.tipo as cat_tipo FROM pedidos p JOIN productos pr ON p.producto_id = pr.id JOIN categorias c ON pr.categoria_id = c.id WHERE p.id = 292").fetchone()
if r:
    print(f"  Producto: {r['prod']} | Cat tipo: {r['cat_tipo']} | Estado: {r['estado']} | Codigo: {r['codigo_entregado']}")
conn.close()
