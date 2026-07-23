import sys

sys.path.insert(0, '/var/www/tienda')

from app import app, api_webhook
from models import get_db
from flask import request


db = get_db()
user = db.execute("SELECT * FROM usuarios WHERE activo = 1 ORDER BY id LIMIT 1").fetchone()
db.close()

with app.test_request_context('/api/v1/webhook', method='GET'):
    request.api_user = user
    resp = api_webhook.__wrapped__()
    print('status=', resp.status_code)
    print('json=', resp.get_json())
