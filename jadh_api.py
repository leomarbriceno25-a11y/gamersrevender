import re
import os

import requests

import config

JADH_BASE_URL = (getattr(config, 'JADH_BASE_URL', None) or 'https://jadh.shop').rstrip('/')
JADH_EMAIL = getattr(config, 'JADH_EMAIL', '') or os.environ.get('JADH_EMAIL', '')
JADH_PASSWORD = getattr(config, 'JADH_PASSWORD', '') or os.environ.get('JADH_PASSWORD', '')
JADH_DEFAULT_ITEM_ID = getattr(config, 'JADH_ITEM_ID', '') or os.environ.get('JADH_ITEM_ID', '32')
JADH_PACKAGE_MAP = getattr(config, 'JADH_PACKAGE_MAP', None) or {
    '100': '150',
    '300': '151',
    '500': '152',
    '1000': '153',
    '2000': '154',
    '5000': '155',
}

UUID_RE = re.compile(
    r'([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})'
)


def _package_id_for(diamonds, explicit_package_id=None):
    if explicit_package_id:
        return str(explicit_package_id).strip()
    key = str(diamonds or '').strip()
    if key in JADH_PACKAGE_MAP:
        return str(JADH_PACKAGE_MAP[key]).strip()
    raise ValueError(f"Denominación de diamantes no soportada para Jadh Shop: {diamonds}")


def comprar_pines_jadh(diamonds, quantity, item_id=None, package_id=None, on_log=None):
    """Compra pines en Jadh Shop y devuelve la lista de códigos PIN encontrados."""

    def log(msg):
        print(msg)
        if on_log:
            try:
                on_log(msg)
            except Exception:
                pass

    if not JADH_EMAIL or not JADH_PASSWORD:
        raise ValueError('JADH_EMAIL y JADH_PASSWORD deben estar configurados.')

    qty = int(quantity or 0)
    if qty <= 0:
        raise ValueError('La cantidad debe ser mayor a 0.')

    pkg_id = _package_id_for(diamonds, package_id)
    itm_id = str(item_id or JADH_DEFAULT_ITEM_ID or '32').strip()

    log(f'[Jadh Auto-Buy] Iniciando compra de {qty} pines de {diamonds} diamantes (paquete {pkg_id})...')

    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    })

    # 1. Iniciar sesión
    log(f'[Jadh Auto-Buy] Iniciando sesión con {JADH_EMAIL}...')
    login_url = f'{JADH_BASE_URL}/login'
    login_data = {'email': JADH_EMAIL, 'password': JADH_PASSWORD}
    login_res = session.post(login_url, data=login_data, allow_redirects=False, timeout=20)

    if login_res.status_code not in (200, 302):
        raise RuntimeError(f'Fallo en login de Jadh Store (Status {login_res.status_code})')

    if 'session' not in session.cookies and 'PHPSESSID' not in [c.name for c in session.cookies]:
        # Algunos frameworks usan otros nombres de cookie; si hay cookies, continuamos
        if not session.cookies:
            raise RuntimeError('No se obtuvieron cookies de sesión de Jadh Store.')

    # 2. Realizar la compra
    log(f'[Jadh Auto-Buy] Enviando solicitud de compra (item {itm_id}, paquete {pkg_id}, qty {qty})...')
    purchase_url = f'{JADH_BASE_URL}/purchase'
    purchase_data = {
        'item_id': itm_id,
        'package_id': pkg_id,
        'quantity': str(qty),
    }
    purchase_res = session.post(
        purchase_url,
        data=purchase_data,
        headers={'Referer': f'{JADH_BASE_URL}/producto/freefire-chile'},
        allow_redirects=False,
        timeout=20,
    )

    if purchase_res.status_code not in (200, 302):
        raise RuntimeError(f'La solicitud de compra no redireccionó correctamente (Status {purchase_res.status_code})')

    redirect_path = purchase_res.headers.get('location', '')
    if not redirect_path:
        # Si no redirige, intentamos parsear directamente la respuesta actual
        result_url = purchase_url
    else:
        result_url = redirect_path if redirect_path.startswith('http') else f'{JADH_BASE_URL}{redirect_path}'

    # 3. Consultar página de confirmación
    log(f'[Jadh Auto-Buy] Consultando página de confirmación: {result_url}...')
    result_res = session.get(result_url, timeout=20)

    if result_res.status_code != 200:
        raise RuntimeError(f'Error al consultar confirmación de compra (Status {result_res.status_code})')

    found = []
    for match in UUID_RE.finditer(result_res.text):
        pin = match.group(1).upper()
        if pin not in found:
            found.append(pin)

    log(f'[Jadh Auto-Buy] Encontrados {len(found)} pines en la página de confirmación.')

    if not found:
        raise RuntimeError('No se localizaron códigos PIN en la respuesta final. Verifique saldo.')

    return found
