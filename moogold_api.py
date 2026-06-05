import base64
import hashlib
import hmac
import json
import time

import requests

import config


MOOGOLD_API_URL = (config.MOOGOLD_API_URL or '').rstrip('/')
MOOGOLD_PARTNER_ID = config.MOOGOLD_PARTNER_ID
MOOGOLD_SECRET_KEY = config.MOOGOLD_SECRET_KEY


def _compact_json(payload):
    if payload is None:
        return ''
    return json.dumps(payload, separators=(',', ':'), ensure_ascii=False)


def _build_basic_auth():
    token = f"{MOOGOLD_PARTNER_ID}:{MOOGOLD_SECRET_KEY}".encode('utf-8')
    return f"Basic {base64.b64encode(token).decode('utf-8')}"


def _build_auth_signature(payload_str, timestamp, path):
    string_to_sign = f"{payload_str}{timestamp}{path}"
    return hmac.new(
        MOOGOLD_SECRET_KEY.encode('utf-8'),
        string_to_sign.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()


def _request(path, payload, timeout=45):
    if not MOOGOLD_API_URL:
        return {'ok': False, 'error': 'MOOGOLD_API_URL no configurado'}
    if not MOOGOLD_PARTNER_ID or not MOOGOLD_SECRET_KEY:
        return {'ok': False, 'error': 'Credenciales MooGold incompletas'}

    clean_path = str(path or '').strip().lstrip('/')
    url = f"{MOOGOLD_API_URL}/{clean_path}"
    payload = dict(payload or {})
    payload.setdefault('path', clean_path)

    ts = int(time.time())
    payload_str = _compact_json(payload)
    headers = {
        'Authorization': _build_basic_auth(),
        'timestamp': str(ts),
        'auth': _build_auth_signature(payload_str, str(ts), clean_path),
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }

    try:
        resp = requests.post(url, headers=headers, data=payload_str, timeout=timeout)
    except Exception as e:
        return {'ok': False, 'status_code': 0, 'error': str(e), 'data': {}}

    try:
        data = resp.json()
    except Exception:
        data = {'raw_text': (resp.text or '').strip()}

    ok_http = 200 <= resp.status_code < 300
    status_field = data.get('status') if isinstance(data, dict) else None
    err_code = str(data.get('err_code', '') or '') if isinstance(data, dict) else ''
    ok_business = bool(status_field is True or str(status_field).lower() == 'success') and not err_code
    ok = ok_http and ok_business

    return {
        'ok': ok,
        'status_code': resp.status_code,
        'data': data,
        'error': '' if ok else (
            (data.get('err_message') if isinstance(data, dict) else '')
            or (data.get('message') if isinstance(data, dict) else '')
            or f'HTTP {resp.status_code}'
        ),
    }


def obtener_saldo():
    return _request('user/balance', {'path': 'user/balance'})


def listar_productos(category_id):
    return _request(
        'product/list_product',
        {
            'path': 'product/list_product',
            'category_id': int(category_id),
        },
    )


def detalle_producto(product_id):
    return _request(
        'product/product_detail',
        {
            'path': 'product/product_detail',
            'product_id': int(product_id),
        },
    )


def validar_producto(product_id, account_fields):
    data = {'product-id': str(product_id)}
    for k, v in (account_fields or {}).items():
        if k and v is not None:
            data[str(k)] = str(v)
    return _request(
        'product/validate',
        {
            'path': 'product/validate',
            'data': data,
        },
    )


def crear_orden(category, variation_id, quantity, account_fields=None, partner_order_id=''):
    data = {
        'category': int(category),
        'product-id': int(variation_id),
        'quantity': int(quantity),
    }
    for k, v in (account_fields or {}).items():
        if k and v is not None:
            data[str(k)] = str(v)

    payload = {
        'path': 'order/create_order',
        'data': data,
    }
    if partner_order_id:
        payload['partnerOrderId'] = str(partner_order_id)

    return _request('order/create_order', payload)


def consultar_orden(order_id):
    return _request(
        'order/order_detail',
        {
            'path': 'order/order_detail',
            'order_id': int(order_id),
        },
    )


def consultar_orden_partner(partner_order_id):
    return _request(
        'order/order_detail_partner_id',
        {
            'path': 'order/order_detail_partner_id',
            'partner_order_id': str(partner_order_id),
        },
    )
