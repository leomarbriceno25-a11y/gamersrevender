import json
import sys

sys.path.insert(0, '/var/www/tienda')

from razer_api import recargar_paquete

player_id = '6931042372'
paquete = 8  # Tarjeta Semanal

resp = recargar_paquete(player_id, paquete)
print(json.dumps(resp, ensure_ascii=False, indent=2))
