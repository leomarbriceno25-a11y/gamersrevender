import sqlite3

con = sqlite3.connect('/var/www/tienda/instance/tienda.db')
con.row_factory = sqlite3.Row
cur = con.cursor()

cols = [r['name'] for r in cur.execute("PRAGMA table_info(productos)").fetchall()]
print('PRODUCTOS_COLS:', cols)

row = cur.execute("SELECT * FROM productos WHERE id = 48").fetchone()
print('\nPRODUCTO_48:')
if row:
    d = dict(row)
    keys = [
        'id','nombre','activo','stock','stock_minimo','stock_objetivo','precio','monto_recarga',
        'usa_pincentral','pincentral_product_code','pin_origen_producto_id','proveedor_api','gamepoint_id'
    ]
    for k in keys:
        if k in d:
            print(k, '=', d[k])
    # print all provider-ish fields
    print('\nPROVIDER_FIELDS:')
    for k,v in d.items():
        lk = k.lower()
        if any(x in lk for x in ['api','hype','razer','delta','gamepoint','pincentral','pin_origen','proveedor','extern']):
            print(k, '=', v)
else:
    print(None)

con.close()
