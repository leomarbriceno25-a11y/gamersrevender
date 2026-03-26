import json
import requests
import config

RAZER_API_URL = getattr(config, 'RAZER_API_URL', 'https://152.53.210.85/api/razer.jsp')
RAZER_TOKEN = getattr(config, 'RAZER_API_TOKEN', '')
RAZER_VERIFY_SSL = bool(getattr(config, 'RAZER_VERIFY_SSL', False))

PAQUETES_RAZER = {
    1: '110 diamantes',
    2: '341 diamantes',
    3: '520 diamantes',
    4: '1060 diamantes',
    5: '2180 diamantes',
    6: '5600 diamantes',
    7: 'Tarjeta Básica',
    8: 'Membresía Semanal',
    9: 'Membresía Mensual',
    10: 'Pase Booyah',
}


def _safe_json(text):
    text = (text or '').strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except Exception:
                return {}
        return {}


def recargar_paquete(id_jugador, paquete):
    if not RAZER_TOKEN:
        return {'ok': False, 'error': 'RAZER_API_TOKEN no configurado'}

    paquete_num = int(paquete or 0)
    if paquete_num <= 0:
        return {'ok': False, 'error': 'Paquete inválido'}

    try:
        resp = requests.get(
            RAZER_API_URL,
            params={
                'token': RAZER_TOKEN,
                'id_jugador': str(id_jugador).strip(),
                'paquete': paquete_num,
            },
            timeout=45,
            verify=RAZER_VERIFY_SSL,
        )
    except Exception as e:
        return {'ok': False, 'error': str(e)}

    data = _safe_json(resp.text)
    alerta = str(data.get('alerta', '')).lower()
    ok = alerta == 'green'

    return {
        'ok': ok,
        'status_code': resp.status_code,
        'mensaje': data.get('mensaje', ''),
        'nickname': data.get('nickname', ''),
        'dias_disponibles': data.get('dias_disponibles', 0),
        'fecha_corte': data.get('fecha_corte', ''),
        'raw': data,
        'error': '' if ok else (data.get('mensaje') or 'Recarga rechazada por proveedor'),
    }
