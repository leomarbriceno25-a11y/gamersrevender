import sqlite3

DB = '/var/www/tienda/instance/tienda.db'
TARGET_IDS = [16738, 16737, 16736]

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
cur = con.cursor()

for pid in TARGET_IDS:
    r = cur.execute(
        "SELECT p.*, pr.* "
        "FROM pedidos p "
        "JOIN productos pr ON pr.id = p.producto_id "
        "WHERE p.id = ?",
        (pid,),
    ).fetchone()
    if not r:
        print(f"pedido {pid}: no existe")
        continue
    print(f"\n=== pedido {pid} ===")
    print(f"estado={r['estado']} fecha={r['fecha_pedido']} producto_id={r['producto_id']} nombre={r['nombre']}")
    print(f"id_juego={r['id_juego']} nombre_jugador={r['nombre_jugador']} codigo_entregado={r['codigo_entregado']}")
    # Campos de integración del producto (si existen)
    for k in [
        'usa_api', 'usa_hype_games', 'usa_razer_gold', 'usa_deltaforce',
        'usa_pincentral', 'pincentral_entrega_directa', 'recarga_manual',
        'api_id_juego', 'api_nombre_juego', 'api_producto_origen_id',
        'pincentral_product_code', 'razer_sku', 'deltaforce_sku',
    ]:
        if k in r.keys():
            print(f"{k}={r[k]}")

    inc = cur.execute(
        "SELECT id, contexto, detalle, payload, fecha FROM pincentral_incidentes WHERE pedido_id = ? ORDER BY id",
        (pid,),
    ).fetchall()
    print(f"pincentral_incidentes={len(inc)}")
    for i in inc:
        print(f"  - inc#{i['id']} ctx={i['contexto']} fecha={i['fecha']} detalle={i['detalle']}")

# Buscar posibles tablas de incidentes/errores
print('\n=== tablas con nombre incidente/error/log ===')
tables = [x['name'] for x in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
for t in tables:
    tl = t.lower()
    if any(s in tl for s in ['incidente', 'error', 'log', 'auditoria']):
        print('-', t)

con.close()
