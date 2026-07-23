import sqlite3

con = sqlite3.connect('/var/www/tienda/instance/tienda.db')
con.row_factory = sqlite3.Row
cur = con.cursor()

print('TABLES:')
for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
    print('-', r['name'])

pedido_id = 18989
print('\nTRANSACCIONES pedido_id=18989:')
for r in cur.execute("SELECT id, tipo, monto, descripcion, pedido_id, fecha FROM transacciones WHERE pedido_id=? ORDER BY id", (pedido_id,)):
    print(dict(r))

print('\nTRANSACCIONES descripcion like 18989:')
for r in cur.execute("SELECT id, tipo, monto, descripcion, pedido_id, fecha FROM transacciones WHERE descripcion LIKE ? ORDER BY id", (f'%{pedido_id}%',)):
    print(dict(r))

if any(x['name'] == 'recargas_auditoria' for x in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")):
    print('\nRECARGAS_AUDITORIA pedido_id=18989:')
    for r in cur.execute("SELECT id, proveedor, etapa, estado, detalle, payload, fecha FROM recargas_auditoria WHERE pedido_id=? ORDER BY id", (pedido_id,)):
        d = dict(r)
        if d.get('payload') and len(d['payload']) > 1000:
            d['payload'] = d['payload'][:1000] + '...'
        print(d)

# buscar tablas que puedan tener respuesta proveedor
print('\nTABLAS POSIBLES DE LOG/API:')
for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND (name LIKE '%incidente%' OR name LIKE '%audit%' OR name LIKE '%log%' OR name LIKE '%razer%' OR name LIKE '%api%') ORDER BY name"):
    print('-', r['name'])

con.close()
