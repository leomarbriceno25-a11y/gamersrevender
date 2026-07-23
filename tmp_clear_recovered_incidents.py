import os
import sqlite3
import sys

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

DB = '/var/www/tienda/instance/tienda.db'


def norm_status(s):
    return str(s or '').strip().lower().replace(' ', '')


con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
cur = con.cursor()

rows = cur.execute(
    "SELECT id, contexto, producto_id, product_code, order_id, transaction_id "
    "FROM pincentral_incidentes "
    "WHERE contexto IN ('restock_capture','restock_capture_retry','restock','restock_auth') "
    "AND transaction_id IS NOT NULL AND trim(transaction_id) != '' "
    "ORDER BY id"
).fetchall()

resolved_tx = set()
for r in rows:
    tx_id = str(r['transaction_id'] or '').strip()
    if not tx_id or tx_id in resolved_tx:
        continue
    resp = capturar_pins(tx_id)
    data = resp.get('data', {}) if isinstance(resp.get('data', {}), dict) else {}
    status = norm_status(data.get('status', ''))
    pins = data.get('pins', []) if isinstance(data.get('pins', []), list) else []
    if resp.get('ok') and status in {'captured', 'capturado'} and pins:
        resolved_tx.add(tx_id)

if resolved_tx:
    placeholders = ','.join('?' for _ in resolved_tx)
    to_delete = cur.execute(
        f"SELECT id FROM pincentral_incidentes WHERE transaction_id IN ({placeholders})",
        tuple(resolved_tx),
    ).fetchall()
    delete_ids = [int(x['id']) for x in to_delete]
    if delete_ids:
        id_ph = ','.join('?' for _ in delete_ids)
        cur.execute(f"DELETE FROM pincentral_incidentes WHERE id IN ({id_ph})", tuple(delete_ids))
        con.commit()
    else:
        delete_ids = []
else:
    delete_ids = []

remaining = cur.execute("SELECT COUNT(1) c FROM pincentral_incidentes").fetchone()['c']
remaining_capture = cur.execute(
    "SELECT COUNT(1) c FROM pincentral_incidentes WHERE contexto IN ('restock_capture','restock_capture_retry')"
).fetchone()['c']

print('tx_resueltas=', len(resolved_tx))
print('incidentes_eliminados=', len(delete_ids))
print('restantes_totales=', remaining)
print('restantes_restock_capture=', remaining_capture)

con.close()
