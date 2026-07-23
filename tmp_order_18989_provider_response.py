import json
import sqlite3

con = sqlite3.connect('/var/www/tienda/instance/tienda.db')
con.row_factory = sqlite3.Row
cur = con.cursor()

print('TX linked by pedido_id:')
for r in cur.execute("SELECT id,tipo,monto,descripcion,fecha,pedido_id FROM transacciones WHERE pedido_id=? ORDER BY id", (18989,)):
    print(json.dumps(dict(r), ensure_ascii=False))

print('TX refund by descripcion contains 18989:')
for r in cur.execute("SELECT id,tipo,monto,descripcion,fecha,pedido_id FROM transacciones WHERE descripcion LIKE ? ORDER BY id", ('%18989%',)):
    print(json.dumps(dict(r), ensure_ascii=False))

print('solicitudes_recarga rows for pedido 18989:')
for r in cur.execute("SELECT * FROM solicitudes_recarga WHERE pedido_id=? ORDER BY id", (18989,)):
    d = dict(r)
    for k, v in list(d.items()):
        if isinstance(v, str) and len(v) > 1200:
            d[k] = v[:1200] + '...'
    print(json.dumps(d, ensure_ascii=False))

print('recargas_auditoria rows for pedido 18989:')
for r in cur.execute("SELECT * FROM recargas_auditoria WHERE pedido_id=? ORDER BY id", (18989,)):
    d = dict(r)
    if isinstance(d.get('payload'), str) and len(d['payload']) > 1200:
        d['payload'] = d['payload'][:1200] + '...'
    print(json.dumps(d, ensure_ascii=False))

con.close()
