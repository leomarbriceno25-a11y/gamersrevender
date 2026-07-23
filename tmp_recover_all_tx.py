import os
import sys
import sqlite3

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

rows_tx = cur.execute(
    "SELECT id, contexto, producto_id, product_code, order_id, transaction_id "
    "FROM pincentral_incidentes "
    "WHERE contexto IN ('restock_capture', 'restock_auth', 'restock') "
    "AND transaction_id IS NOT NULL AND trim(transaction_id) != '' "
    "ORDER BY id"
).fetchall()

rows_no_tx = cur.execute(
    "SELECT id, contexto, producto_id, product_code, order_id, detalle "
    "FROM pincentral_incidentes "
    "WHERE contexto IN ('restock_capture', 'restock_auth', 'restock') "
    "AND (transaction_id IS NULL OR trim(transaction_id) = '') "
    "ORDER BY id"
).fetchall()

print('=== incidencias restock con tx_id ===')
print('total=', len(rows_tx))
for r in rows_tx:
    print(f"id={r['id']} ctx={r['contexto']} prod={r['producto_id']} code={r['product_code']} order={r['order_id']} tx={r['transaction_id']}")

print('\n=== incidencias restock SIN tx_id ===')
print('total=', len(rows_no_tx))
for r in rows_no_tx:
    print(f"id={r['id']} ctx={r['contexto']} prod={r['producto_id']} code={r['product_code']} order={r['order_id']} detalle={r['detalle']}")

insertados_total = 0
procesadas = 0
capturadas_ok = 0

for r in rows_tx:
    procesadas += 1
    inc_id = r['id']
    producto_id = int(r['producto_id'] or 0)
    tx_id = (r['transaction_id'] or '').strip()

    resp = capturar_pins(tx_id)
    data = resp.get('data', {}) if isinstance(resp.get('data', {}), dict) else {}
    status = norm_status(data.get('status', ''))
    pins = data.get('pins', []) if isinstance(data.get('pins', []), list) else []

    if not (resp.get('ok') and status in {'captured', 'capturado'} and pins):
        msg = resp.get('error') or data.get('message') or ''
        print(f"[NO] inc={inc_id} tx={tx_id} status={data.get('status')} msg={msg}")
        continue

    capturadas_ok += 1
    existentes = set()
    for pr in cur.execute("SELECT pin FROM pines WHERE producto_id = ?", (producto_id,)).fetchall():
        try:
            existentes.add(decrypt_pin(pr['pin']))
        except Exception:
            pass

    nuevos = 0
    duplicados = 0
    for p in pins:
        if not isinstance(p, dict):
            continue
        key = str(p.get('key', '') or '').strip()
        if not key:
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
    print(f"[OK] inc={inc_id} tx={tx_id} pins={len(pins)} nuevos={nuevos} duplicados={duplicados}")

print('\n=== resumen recuperación ===')
print('procesadas_con_tx=', procesadas)
print('capturadas_ok=', capturadas_ok)
print('insertados_total=', insertados_total)

print('\n=== stock final ===')
for row in cur.execute("SELECT producto_id, COUNT(*) c FROM pines WHERE estado='disponible' AND producto_id IN (121,124) GROUP BY producto_id ORDER BY producto_id"):
    print((row['producto_id'], row['c']))

con.close()
