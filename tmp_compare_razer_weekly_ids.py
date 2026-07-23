import json
import os
import sys

UNIT_FILE = '/etc/systemd/system/tienda.service'


def load_env_from_systemd_unit(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            txt = f.read()
    except Exception:
        return

    for line in txt.splitlines():
        line = line.strip()
        if not line.startswith('Environment='):
            continue
        raw = line[len('Environment='):].strip()
        if raw.startswith('"') and raw.endswith('"'):
            raw = raw[1:-1]
        if '=' not in raw:
            continue
        k, v = raw.split('=', 1)
        if k and v:
            os.environ.setdefault(k.strip(), v.strip())


load_env_from_systemd_unit(UNIT_FILE)

sys.path.insert(0, '/var/www/tienda')
from razer_api import recargar_paquete

ids = ['6931042372', '8536270965']
for pid in ids:
    resp = recargar_paquete(pid, 8)
    print('===', pid, '===')
    print(json.dumps(resp, ensure_ascii=False, indent=2))
