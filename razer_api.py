import json
import requests
import config

RAZER_API_URL = config.RAZER_API_URL
RAZER_TOKEN = config.RAZER_API_TOKEN
RAZER_VERIFY_SSL = config.RAZER_VERIFY_SSL


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
    alerta = str(data.get('alerta', '')).strip().lower()
    mensaje_original = str(data.get('mensaje', '') or '').strip()
    mensaje_lower = mensaje_original.lower()
    nickname = str(data.get('nickname', '') or '').strip()
    nickname_valido = bool(nickname) and nickname.lower() not in ('null', 'none', '-')

    ok = alerta == 'green'

    # Ajuste por comportamiento del proveedor:
    # en algunos casos la recarga se aplica pero responden "error desconocido" con nickname válido.
    if not ok and nickname_valido and 'error desconocido' in mensaje_lower:
        ok = True

    mensaje_final = 'Recarga exitosa' if ok else (mensaje_original or 'Recarga rechazada por proveedor')

    return {
        'ok': ok,
        'status_code': resp.status_code,
        'mensaje': mensaje_final,
        'mensaje_original': mensaje_original,
        'nickname': nickname,
        'dias_disponibles': data.get('dias_disponibles', 0),
        'fecha_corte': data.get('fecha_corte', ''),
        'raw': data,
        'error': '' if ok else mensaje_final,
    }
