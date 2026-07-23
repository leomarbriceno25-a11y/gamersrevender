import sys
import sqlite3
import os

sys.path.insert(0, '/var/www/tienda')


def _load_pincentral_env_from_systemd():
    unit_path = '/etc/systemd/system/tienda.service'
    if not os.path.exists(unit_path):
        return
    try:
        with open(unit_path, 'r', encoding='utf-8') as f:
            for raw in f:
                line = raw.strip()
                if not line.startswith('Environment=PINCENTRAL_'):
                    continue
                kv = line.split('Environment=', 1)[1]
                if '=' not in kv:
                    continue
                key, val = kv.split('=', 1)
                if key.startswith('PINCENTRAL_') and key not in os.environ:
                    os.environ[key] = val
    except Exception:
        pass


_load_pincentral_env_from_systemd()

from pincentral_api import capturar_pins
from models import encrypt_pin, decrypt_pin

DB = '/var/www/tienda/instance/tienda.db'


def norm_status(s):
    return str(s or '').strip().lower().replace(' ', '')


con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
cur = con.cursor()

rows = cur.execute(
    "SELECT id, producto_id, order_id, transaction_id FROM pincentral_incidentes "
    "WHERE id IN (3,6) ORDER BY id"
).fetchall()

print('=== reintento captura tx ===')
insertados_total = 0

for r in rows:
    inc_id = r['id']
    producto_id = int(r['producto_id'] or 0)
    order_id = r['order_id'] or ''
    tx_id = (r['transaction_id'] or '').strip()
    print(f"\\n[incidente {inc_id}] producto={producto_id} order={order_id} tx={tx_id}")
    if not tx_id:
        print('-> sin tx_id, no se puede capturar')
        continue

    resp = capturar_pins(tx_id)
    data = resp.get('data', {}) if isinstance(resp.get('data', {}), dict) else {}
    status = norm_status(data.get('status', ''))
    pins = data.get('pins', []) if isinstance(data.get('pins', []), list) else []
    msg = resp.get('error') or data.get('message') or ''

    print('status_code=', resp.get('status_code'))
    print('ok_http=', resp.get('ok'))
    print('status=', data.get('status'))
    print('message=', msg)
    print('pins_recibidos=', len(pins))

    if not (resp.get('ok') and status in {'captured', 'capturado'} and pins):
        print('-> no recuperable ahora')
        continue

    existentes = set()
    for pr in cur.execute("SELECT pin FROM pines WHERE producto_id = ?", (producto_id,)).fetchall():
        try:
            existentes.add(decrypt_pin(pr['pin']))
        except Exception:
            pass

    nuevos = 0
    duplicados = 0
    vacios = 0
    for p in pins:
        if not isinstance(p, dict):
            continue
        key = str(p.get('key', '') or '').strip()
        if not key:
            vacios += 1
            continue
        if key in existentes:
            duplicados += 1
            continue
        cur.execute(
            "INSERT INTO pines (producto_id, pin, estado) VALUES (?, ?, 'disponible')",
            (producto_id, encrypt_pin(key)),
        )
        existentes.add(key)
        nuevos += 1

    con.commit()
    insertados_total += nuevos
    print(f"-> insertados={nuevos}, duplicados={duplicados}, vacios={vacios}")

print('\\n=== stock despues ===')
for row in cur.execute("SELECT producto_id, COUNT(*) c FROM pines WHERE estado='disponible' AND producto_id IN (121,124) GROUP BY producto_id ORDER BY producto_id"):
    print((row['producto_id'], row['c']))

print('insertados_total=', insertados_total)
con.close()
