import hashlib
import hmac
import json
from datetime import datetime, timezone

import requests

import config

PINCENTRAL_API_URL = (config.PINCENTRAL_API_URL or '').rstrip('/')
PINCENTRAL_API_KEY = config.PINCENTRAL_API_KEY
PINCENTRAL_API_SECRET = config.PINCENTRAL_API_SECRET
PINCENTRAL_VERIFY_SSL = config.PINCENTRAL_VERIFY_SSL


def _iso_utc_now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _compact_json(payload):
    if payload is None:
        return ''
    return json.dumps(payload, separators=(',', ':'), ensure_ascii=False)


def _signature(method, path, x_date, body_str):
    sign_path = str(path or '').lstrip('/')
    content = f"{method.upper()}{sign_path}{x_date}{body_str}"
    return hmac.new(
        PINCENTRAL_API_SECRET.encode('utf-8'),
        content.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()


def _build_headers(method, path, payload):
    x_date = _iso_utc_now()
    body_str = _compact_json(payload)
    sign = _signature(method, path, x_date, body_str)
    return {
        'Authorization': f'{PINCENTRAL_API_KEY}:{sign}',
        'X-Date': x_date,
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }, body_str


def _request(method, path, payload=None, timeout=45):
    if not PINCENTRAL_API_URL:
        return {'ok': False, 'error': 'PINCENTRAL_API_URL no configurado'}
    if not PINCENTRAL_API_KEY or not PINCENTRAL_API_SECRET:
        return {'ok': False, 'error': 'Credenciales PinCentral incompletas'}

    headers, body_str = _build_headers(method, path, payload)
    url = f"{PINCENTRAL_API_URL}{path}"

    try:
        resp = requests.request(
            method=method.upper(),
            url=url,
            headers=headers,
            data=body_str if payload is not None else None,
            timeout=timeout,
            verify=PINCENTRAL_VERIFY_SSL,
        )
    except Exception as e:
        return {'ok': False, 'error': str(e)}

    raw_text = (resp.text or '').strip()
    data = {}
    if raw_text:
        try:
            data = resp.json()
        except Exception:
            data = {'raw_text': raw_text}

    ok_http = 200 <= resp.status_code < 300
    return {
        'ok': ok_http,
        'status_code': resp.status_code,
        'data': data,
        'raw_text': raw_text,
        'error': '' if ok_http else (data.get('message') if isinstance(data, dict) else raw_text),
    }


def consultar_stock(product_code):
    return _request('POST', '/api/pins/stock', {'product': str(product_code).strip()})


def listar_productos():
    return _request('GET', '/api/products')


def autorizar_pins(product_code, quantity, order_id, client_name='', client_email=''):
    payload = {
        'product': str(product_code).strip(),
        'quantity': int(quantity),
        'order_id': str(order_id).strip(),
    }
    if client_name:
        payload['client_name'] = client_name
    if client_email:
        payload['client_email'] = client_email
    return _request('POST', '/api/pins/authorize', payload)


def capturar_pins(transaction_id):
    return _request('POST', '/api/pins/capture', {'id': str(transaction_id).strip()})


def consultar_pedido_pin(transaction_id):
    tx_id = str(transaction_id or '').strip()
    return _request('GET', f"/api/pins/{tx_id}")


def validar_recarga(product_code, service_user_id, additional_data='', additional_data_2='', client_first_name='', client_last_name='', client_country='VE'):
    payload = {
        'product_code': str(product_code).strip(),
        'service_user_id': str(service_user_id).strip(),
    }
    if additional_data:
        payload['additional_data'] = str(additional_data).strip()
    if additional_data_2:
        payload['additional_data_2'] = str(additional_data_2).strip()
    if client_first_name:
        payload['client_first_name'] = str(client_first_name).strip()
    if client_last_name:
        payload['client_last_name'] = str(client_last_name).strip()
    if client_country:
        payload['client_country'] = str(client_country).strip()[:2].upper()
    return _request('POST', '/api/recharges/validate', payload)


def crear_recarga(product_code, service_user_id, order_id, additional_data='', additional_data_2='', client_email='', client_first_name='', client_last_name='', client_country='VE'):
    payload = {
        'order_id': str(order_id).strip(),
        'service_user_id': str(service_user_id).strip(),
        'product_code': str(product_code).strip(),
    }
    if additional_data:
        payload['additional_data'] = str(additional_data).strip()
    if additional_data_2:
        payload['additional_data_2'] = str(additional_data_2).strip()
    if client_email:
        payload['client_email'] = str(client_email).strip()
    if client_first_name:
        payload['client_first_name'] = str(client_first_name).strip()
    if client_last_name:
        payload['client_last_name'] = str(client_last_name).strip()
    if client_country:
        payload['client_country'] = str(client_country).strip()[:2].upper()
    return _request('POST', '/api/recharges', payload)
