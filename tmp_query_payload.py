import sqlite3
import json

DB = '/var/www/tienda/instance/tienda.db'
IDS = (2, 3, 4, 5, 6, 7, 8)

con = sqlite3.connect(DB)
cur = con.cursor()
q = (
    "SELECT id, fecha, contexto, product_code, order_id, transaction_id, detalle, payload "
    "FROM pincentral_incidentes WHERE id IN (2,3,4,5,6,7,8) ORDER BY id"
)
for row in cur.execute(q):
    i, fecha, contexto, pcode, order_id, tx, detalle, payload = row
    print(f"\n--- incidente {i} ---")
    print(f"fecha={fecha}")
    print(f"contexto={contexto}")
    print(f"product_code={pcode}")
    print(f"order_id={order_id}")
    print(f"tx={tx}")
    print(f"detalle={detalle}")
    if payload:
        print("payload_raw=", payload)
        try:
            obj = json.loads(payload)
            print("payload_keys=", sorted(obj.keys()) if isinstance(obj, dict) else type(obj).__name__)
            if isinstance(obj, dict):
                for key in ("ok", "status_code", "error", "message", "status", "raw_text"):
                    if key in obj:
                        print(f"payload_{key}={obj.get(key)}")
        except Exception as e:
            print("payload_json_error=", e)

con.close()
