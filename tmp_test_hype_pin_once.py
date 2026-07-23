import json
import sys

sys.path.insert(0, '/var/www/tienda')

from hype_api import canjear_pin_completo

PIN = '2F7DA518-3A52-44CE-9883-3751FBB2C7AF'
GAME_ID = '3648494384'

resp = canjear_pin_completo(PIN, GAME_ID, 1)
print(json.dumps(resp, ensure_ascii=False, indent=2))
