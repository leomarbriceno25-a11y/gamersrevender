import json
import html
import requests

HYPE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Accept": "*/*",
    "Referer": "https://redeem.hype.games/widget",
    "Origin": "https://redeem.hype.games"
}

CAPTCHA_TOKEN = "03AFcWeA7iMhAV8eEIBUFdhuuetcYorsqTgmxJvdpVsnpQRoYGhopvxbYwyS"

# Mapeo monto_api → ProductId de Hype Games
# monto 1 = 110 diamantes, 2 = 341, 3 = 572, 4 = 1166, 5 = 2376, 6 = 6138
MONTO_PRODUCT_ID = {
    1: "2630",
    2: "2631",
    3: "2632",
    4: "2633",
    5: "2634",
    6: "2635",
}


def redeem_validate(pin):
    """Paso 1: Validar el PIN"""
    url = "https://redeem.hype.games/validate"
    data = {
        "Key": pin,
        "CaptchaToken": CAPTCHA_TOKEN,
        "origin": "widget"
    }
    try:
        r = requests.post(url, data=data, headers=HYPE_HEADERS, timeout=30)
        return {"ok": True, "status": r.status_code, "body": r.text}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _body_to_dict(body):
    if isinstance(body, dict):
        return body
    if not isinstance(body, str):
        return {}
    txt = body.strip()
    if not txt:
        return {}
    try:
        parsed = json.loads(txt)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _is_failed_provider_body(body):
    data = _body_to_dict(body)
    if not data:
        return False, ''
    msg = str(data.get('Message', '') or data.get('message', '')).strip()
    if 'Success' in data and data.get('Success') is False:
        return True, msg or 'Proveedor devolvió Success=false'
    status_code = data.get('StatusCode')
    try:
        st = int(status_code)
        if st != 200:
            return True, msg or f'StatusCode={st}'
    except Exception:
        pass
    return False, ''


def _is_confirm_delivery_ok(body):
    if isinstance(body, dict):
        txt = json.dumps(body, ensure_ascii=False)
    else:
        txt = str(body or '')
    txt_norm = html.unescape(txt).lower()
    frases_ok = (
        'entrega de créditos',
        'entrega de creditos',
        'creditos em processo',
        'créditos em processo',
    )
    return any(f in txt_norm for f in frases_ok)


def _is_retryable_transport_error(error_text):
    txt = str(error_text or '').strip().lower()
    if not txt:
        return False
    retryable_signals = (
        'read timed out',
        'connect timeout',
        'connection aborted',
        'connection reset',
        'temporarily unavailable',
        'remote end closed connection',
        '502',
        '503',
        '504',
    )
    return any(s in txt for s in retryable_signals)


def redeem_account(game_account_id, pin, monto_api=1, nombre="Juan Perez", fecha="15/03/1995", pais="VE"):
    """Paso 2: Asociar PIN a cuenta"""
    url = "https://redeem.hype.games/validate/account"
    product_id = MONTO_PRODUCT_ID.get(monto_api, "2630")
    data = {
        "RedeemCountryId": "5",
        "ProductId": product_id,
        "CountryId": "5",
        "CompanyName": "HypeMexico",
        "Customer.CompanyName": "HypeMexico",
        "Key": pin,
        "CookieCardHypeInfo": "",
        "GameAccountId": game_account_id,
        "privacy": "on",
        "CaptchaToken": CAPTCHA_TOKEN,
        "Customer.NationalityAlphaCode": pais,
        "Customer.Name": nombre,
        "Customer.BornAt": fecha,
        "Customer.CountryId": "5",
        "RedeemSourceTypeId": "3",
        "QueryString": "",
        "origin": "widget"
    }
    try:
        r = requests.post(url, data=data, headers=HYPE_HEADERS, timeout=30)
        return {"ok": True, "status": r.status_code, "body": r.json() if r.headers.get('content-type', '').startswith('application/json') else r.text}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def redeem_confirm(game_account_id, pin, monto_api=1, nombre="Juan Perez", fecha="15/03/1995", pais="VE"):
    """Paso 3: Confirmar redención"""
    url = "https://redeem.hype.games/confirm"
    product_id = MONTO_PRODUCT_ID.get(monto_api, "2630")
    data = {
        "RedeemCountryId": "5",
        "ProductId": product_id,
        "CountryId": "5",
        "CompanyName": "HypeMexico",
        "Customer.CompanyName": "HypeMexico",
        "Key": pin,
        "CookieCardHypeInfo": "",
        "GameAccountId": game_account_id,
        "privacy": "on",
        "CaptchaToken": CAPTCHA_TOKEN,
        "Customer.NationalityAlphaCode": pais,
        "Customer.Name": nombre,
        "Customer.BornAt": fecha,
        "Customer.CountryId": "5",
        "RedeemSourceTypeId": "3",
        "QueryString": "",
        "origin": "widget"
    }
    try:
        r = requests.post(url, data=data, headers=HYPE_HEADERS, timeout=30)
        return {"ok": True, "status": r.status_code, "body": r.text}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def canjear_pin_completo(pin, game_account_id, monto_api=1, nombre="Juan Perez", fecha="15/03/1995", pais="VE"):
    """Ejecuta los 3 pasos completos de canje"""
    # Paso 1
    paso1 = redeem_validate(pin)
    if not paso1.get("ok"):
        err = paso1.get("error", "Error validando PIN")
        return {
            "ok": False,
            "paso": 1,
            "error": err,
            "pin_error": True,
            "retry_same_pin": _is_retryable_transport_error(err),
            "paso1": paso1,
        }

    fail_1, msg_1 = _is_failed_provider_body(paso1.get("body", ""))
    if fail_1:
        return {"ok": False, "paso": 1, "error": msg_1 or "Error validando PIN", "pin_error": True, "paso1": paso1}

    # Paso 2 - Aquí devuelve el Username del jugador
    paso2 = redeem_account(game_account_id, pin, monto_api, nombre, fecha, pais)
    if not paso2.get("ok"):
        err = paso2.get("error", "Error asociando cuenta")
        return {
            "ok": False,
            "paso": 2,
            "error": err,
            "pin_error": False,
            "retry_same_pin": _is_retryable_transport_error(err),
            "paso1": paso1,
            "paso2": paso2,
        }

    # Extraer nombre del jugador
    body2 = paso2.get("body", {})
    username = body2.get("Username", "") if isinstance(body2, dict) else ""

    # Verificar que paso 2 fue exitoso
    if isinstance(body2, dict) and not body2.get("Success", False):
        return {
            "ok": False,
            "paso": 2,
            "error": "API rechazó la cuenta",
            "username": username,
            "pin_error": False,
            "paso1": paso1,
            "paso2": paso2,
        }

    # Paso 3
    paso3 = redeem_confirm(game_account_id, pin, monto_api, nombre, fecha, pais)
    if not paso3.get("ok"):
        err = paso3.get("error", "Error confirmando canje")
        return {
            "ok": False,
            "paso": 3,
            "error": err,
            "username": username,
            "pin_error": True,
            "retry_same_pin": _is_retryable_transport_error(err),
            "paso1": paso1,
            "paso2": paso2,
            "paso3": paso3,
        }

    if not _is_confirm_delivery_ok(paso3.get("body", "")):
        return {
            "ok": False,
            "paso": 3,
            "error": "Confirmación no válida del proveedor (no indica entrega de créditos)",
            "username": username,
            "pin_error": True,
            "paso1": paso1,
            "paso2": paso2,
            "paso3": paso3,
        }

    fail_3, msg_3 = _is_failed_provider_body(paso3.get("body", ""))
    if fail_3:
        return {
            "ok": False,
            "paso": 3,
            "error": msg_3 or "Error confirmando canje",
            "username": username,
            "pin_error": True,
            "paso1": paso1,
            "paso2": paso2,
            "paso3": paso3,
        }

    return {
        "ok": True,
        "mensaje": "PIN canjeado exitosamente",
        "username": username,
        "pin_error": False,
        "monto_api": monto_api,
        "product_id": MONTO_PRODUCT_ID.get(monto_api, "2630"),
        "paso1": paso1,
        "paso2": paso2,
        "paso3": paso3
    }
