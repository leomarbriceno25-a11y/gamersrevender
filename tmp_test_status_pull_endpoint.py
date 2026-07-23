import sys

sys.path.insert(0, '/var/www/tienda')

from app import app, api_recarga_status_por_referencia  # noqa: E402
from models import get_db  # noqa: E402
from flask import request


db = get_db()
user = db.execute("SELECT * FROM usuarios WHERE activo = 1 ORDER BY id LIMIT 1").fetchone()
pedido = db.execute(
    "SELECT id, referencia_cliente FROM pedidos WHERE usuario_id = ? ORDER BY id DESC LIMIT 1",
    (user['id'],),
).fetchone()
cols = [r['name'] for r in db.execute("PRAGMA table_info(pedidos)").fetchall()]
db.close()

print('has_referencia_cliente_col=', 'referencia_cliente' in cols)

with app.test_request_context('/api/v1/recargas/status', method='GET'):
    request.api_user = user
    resp = api_recarga_status_por_referencia.__wrapped__()
    print('status_missing_params=', resp[1] if isinstance(resp, tuple) else resp.status_code)

if pedido:
    path = f"/api/v1/recargas/status?pedido_id={int(pedido['id'])}"
    with app.test_request_context(path, method='GET'):
        request.api_user = user
        resp = api_recarga_status_por_referencia.__wrapped__()
        if isinstance(resp, tuple):
            body, status = resp
            print('status_by_pedido=', status)
            print('json_by_pedido=', body.get_json())
        else:
            print('status_by_pedido=', resp.status_code)
            print('json_by_pedido=', resp.get_json())
