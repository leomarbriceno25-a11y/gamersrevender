import sqlite3

ORDER_ID = 1414
DB_PATH = "/var/www/tienda/instance/tienda.db"

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cols = [r[1] for r in cur.execute("PRAGMA table_info(pedidos)").fetchall()]
print({"pedidos_columns": cols})

base_cols = [
    "p.id",
    "p.usuario_id",
    "p.producto_id",
    "pr.nombre AS producto",
    "p.total",
    "p.estado",
    "p.fecha_pedido",
    "p.referencia_externa",
    "p.id_juego",
]

for optional_col in ["detalle", "error", "mensaje", "motivo", "actualizado_en", "fecha_actualizacion"]:
    if optional_col in cols:
        base_cols.append(f"p.{optional_col}")

sql = f"SELECT {', '.join(base_cols)} FROM pedidos p JOIN productos pr ON pr.id = p.producto_id WHERE p.id = ?"
cur.execute(sql, (ORDER_ID,))
row = cur.fetchone()
print(dict(row) if row else {"error": "pedido_no_encontrado", "id": ORDER_ID})

if row:
    pid = row["producto_id"]
    pcols = [r[1] for r in cur.execute("PRAGMA table_info(productos)").fetchall()]
    print({"productos_columns": pcols})
    prod_cols = ["id", "nombre"]
    for optional_col in ["recarga_manual", "categoria_id", "pin_origen_producto_id", "activo"]:
        if optional_col in pcols:
            prod_cols.append(optional_col)
    cur.execute(f"SELECT {', '.join(prod_cols)} FROM productos WHERE id = ?", (pid,))
    prod = cur.fetchone()
    print({"producto": dict(prod) if prod else None})

# Try to fetch transaction movements related to this order if table has pedido_id
try:
    cur.execute(
        """
        SELECT id, usuario_id, tipo, monto, descripcion, fecha
        FROM transacciones
        WHERE descripcion LIKE ?
        ORDER BY fecha DESC
        LIMIT 20
        """,
        (f"%{ORDER_ID}%",),
    )
    tx = [dict(r) for r in cur.fetchall()]
    print({"transacciones_relacionadas": tx})
except Exception as e:
    print({"transacciones_relacionadas_error": str(e)})

conn.close()
