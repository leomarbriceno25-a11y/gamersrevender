import os
import time

import requests


BASE_URL = os.environ.get('BLOODSTRIKE_API_BASE_URL', 'http://2.24.197.52').rstrip('/')
API_KEY = os.environ.get('BLOODSTRIKE_API_KEY', 'bs-secret-key-2026')
TIMEOUT_SECONDS = int(os.environ.get('BLOODSTRIKE_API_TIMEOUT_SECONDS', '600'))


PACKAGES = [
    {'id': 'gold50', 'name': '50 Golds', 'price': 'BDT 50.00'},
    {'id': 'gold100', 'name': '100 Golds', 'price': 'BDT 100.00'},
    {'id': 'gold300', 'name': '300 Golds', 'price': 'BDT 300.00'},
    {'id': 'gold500', 'name': '500 Golds', 'price': 'BDT 500.00'},
    {'id': 'gold1000', 'name': '1000 Golds', 'price': 'BDT 1,000.00'},
    {'id': 'gold2000', 'name': '2000 Golds', 'price': 'BDT 2,000.00'},
    {'id': 'gold5000', 'name': '5000 Golds', 'price': 'BDT 5,000.00'},
    {'id': 'strike_pass_elite', 'name': 'Strike Pass Elite', 'price': 'BDT 400.00'},
    {'id': 'strike_pass_premium', 'name': 'Strike Pass Premium', 'price': 'BDT 900.00'},
    {'id': 'enzo', 'name': 'Cofre Upgrade Enzo', 'price': 'BDT 200.00'},
    {'id': 'levelup_pass', 'name': 'Level-Up Pass', 'price': 'BDT 200.00'},
    {'id': 'maestro_voucher', 'name': 'Maestro Voucher x10', 'price': 'BDT 200.00'},
    {'id': 'deal_049', 'name': 'Cofre Ultra Skin', 'price': 'BDT 50.00'},
]

FREEFIRE_PACKAGES = [
    {'id': 'diamonds100', 'name': '100 Diamonds', 'price': ''},
    {'id': 'diamonds310', 'name': '310 Diamonds', 'price': ''},
    {'id': 'diamonds520', 'name': '520 Diamonds', 'price': ''},
    {'id': 'diamonds1060', 'name': '1060 Diamonds', 'price': ''},
    {'id': 'diamonds2180', 'name': '2180 Diamonds', 'price': ''},
    {'id': 'diamonds5600', 'name': '5600 Diamonds', 'price': ''},
]


def listar_paquetes():
    try:
        r = requests.get(f'{BASE_URL}/api/games', headers={'x-api-key': API_KEY}, timeout=15)
        data = r.json()
        if r.status_code == 200 and isinstance(data, list):
            for game in data:
                if str(game.get('id', '')).strip().lower() == 'bloodstrike':
                    packages = game.get('packages')
                    if isinstance(packages, list) and packages:
                        return {'ok': True, 'packages': packages}
        return {'ok': True, 'packages': PACKAGES}
    except Exception:
        return {'ok': True, 'packages': PACKAGES}


def recargar(player_id, package_id, visible=False, game_id='bloodstrike'):
    player_id = str(player_id or '').strip()
    package_id = str(package_id or '').strip()
    game_id = str(game_id or 'bloodstrike').strip().lower()
    if not player_id:
        return {'ok': False, 'error': 'playerId requerido'}
    if not package_id:
        return {'ok': False, 'error': 'packageId requerido'}
    if game_id not in ('bloodstrike', 'freefire'):
        return {'ok': False, 'error': 'gameId inválido'}

    payload = {
        'gameId': game_id,
        'playerId': player_id,
        'packageId': package_id,
        'visible': bool(visible),
    }
    started = time.time()
    try:
        r = requests.post(
            f'{BASE_URL}/api/recharge',
            json=payload,
            headers={'x-api-key': API_KEY, 'Content-Type': 'application/json'},
            timeout=TIMEOUT_SECONDS,
        )
        elapsed = round(time.time() - started, 3)
        try:
            data = r.json()
        except Exception:
            data = {'raw': r.text}
        if r.status_code == 200 and isinstance(data, dict) and data.get('success'):
            return {
                'ok': True,
                'status_code': r.status_code,
                'elapsed_seconds': elapsed,
                'message': data.get('message') or 'Recarga completada',
                'data': data,
            }
        error = ''
        if isinstance(data, dict):
            error = data.get('error') or data.get('message') or data.get('raw') or ''
        return {
            'ok': False,
            'status_code': r.status_code,
            'elapsed_seconds': elapsed,
            'error': error or f'HTTP {r.status_code}',
            'data': data,
        }
    except Exception as e:
        return {
            'ok': False,
            'elapsed_seconds': round(time.time() - started, 3),
            'error': str(e),
            'data': payload,
        }
