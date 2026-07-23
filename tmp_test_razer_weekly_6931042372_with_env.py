import json
import os
import re
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

player_id = '6931042372'
paquete = 8

resp = recargar_paquete(player_id, paquete)
print(json.dumps(resp, ensure_ascii=False, indent=2))
