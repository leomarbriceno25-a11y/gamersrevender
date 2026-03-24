"""
Módulo de integración con GamePoint Club API
Permite recargas automáticas de Free Fire, Mobile Legends, Genshin Impact, etc.

Flujo:
1. merchant/token  → Obtener token de acceso (expira diario)
2. product/list    → Listar juegos disponibles
3. product/detail  → Ver paquetes y campos requeridos por juego
4. order/validate  → Validar jugador antes de comprar
5. order/create    → Ejecutar la recarga
6. order/inquiry   → Consultar estado de la orden

Requiere: PyJWT, requests
"""
import jwt
import time
import os
import requests
import config

API_URL = config.GAMEPOINT_API_URL
PARTNER_ID = config.GAMEPOINT_PARTNER_ID
SECRET_KEY = config.GAMEPOINT_SECRET_KEY
PROXY_URL = os.environ.get('GAMEPOINT_PROXY', '')

# Cache del token (expira diario a 00:00 UTC+8)
_token_cache = {"token": None, "timestamp": 0}


def _jwt_encode(payload_data):
    """Codifica payload con HMAC SHA-256 usando la secret key del merchant."""
    return jwt.encode(payload_data, SECRET_KEY, algorithm="HS256")


def _post(endpoint, payload_data):
    """Envía POST con JWT al endpoint de GamePoint."""
    token_jwt = _jwt_encode(payload_data)
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/plain",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "partnerid": PARTNER_ID,
    }
    body = {"payload": token_jwt}
    proxies = None
    if PROXY_URL:
        proxies = {"http": PROXY_URL, "https": PROXY_URL}
    try:
        url = f"{API_URL}/{endpoint}"
        resp = requests.post(url, json=body, headers=headers, timeout=30, proxies=proxies)
        return resp.json()
    except Exception as e:
        return {"code": -1, "message": str(e)}


def invalidar_token():
    """Invalida el token cacheado para forzar renovación."""
    global _token_cache
    _token_cache = {"token": None, "timestamp": 0}


def obtener_token(forzar=False):
    """
    Obtiene token de acceso del merchant.
    Cachea el token y lo renueva si tiene más de 12 horas o si se fuerza.
    """
    global _token_cache
    ahora = int(time.time())

    # Usar cache si tiene menos de 12 horas y no se fuerza
    if not forzar and _token_cache["token"] and (ahora - _token_cache["timestamp"]) < 43200:
        return _token_cache["token"]

    resultado = _post("merchant/token", {"timestamp": ahora})

    if resultado.get("code") == 200:
        token = resultado["token"]
        _token_cache["token"] = token
        _token_cache["timestamp"] = ahora
        return token
    else:
        print(f"[GAMEPOINT] Error obteniendo token: {resultado}")
        return None


def _es_token_expirado(resultado):
    """Verifica si el error es por token expirado/incorrecto."""
    msg = str(resultado.get("message", "")).lower()
    return "incorrect token" in msg or "refresh token" in msg or "token expired" in msg


def obtener_saldo():
    """Consulta el saldo disponible del merchant."""
    token = obtener_token()
    if not token:
        return {"ok": False, "error": "No se pudo obtener token"}

    resultado = _post("merchant/balance", {
        "timestamp": int(time.time()),
        "token": token,
    })

    if _es_token_expirado(resultado):
        token = obtener_token(forzar=True)
        if not token:
            return {"ok": False, "error": "No se pudo renovar token"}
        resultado = _post("merchant/balance", {"timestamp": int(time.time()), "token": token})

    if resultado.get("code") == 200:
        return {"ok": True, "balance": resultado.get("balance", 0)}
    else:
        return {"ok": False, "error": resultado.get("message", "Error desconocido")}


def listar_productos():
    """Lista todos los juegos/productos disponibles."""
    token = obtener_token()
    if not token:
        return {"ok": False, "error": "No se pudo obtener token"}

    resultado = _post("product/list", {
        "timestamp": int(time.time()),
        "token": token,
    })

    if _es_token_expirado(resultado):
        token = obtener_token(forzar=True)
        if not token:
            return {"ok": False, "error": "No se pudo renovar token"}
        resultado = _post("product/list", {"timestamp": int(time.time()), "token": token})

    if resultado.get("code") == 200:
        return {"ok": True, "productos": resultado.get("detail", [])}
    else:
        return {"ok": False, "error": resultado.get("message", "Error desconocido")}


def detalle_producto(product_id):
    """
    Obtiene paquetes, campos requeridos y precios de un producto/juego.
    Retorna fields (inputs necesarios), packages (paquetes disponibles) y servers si aplica.
    """
    token = obtener_token()
    if not token:
        return {"ok": False, "error": "No se pudo obtener token"}

    resultado = _post("product/detail", {
        "timestamp": int(time.time()),
        "token": token,
        "productid": int(product_id),
    })

    if _es_token_expirado(resultado):
        token = obtener_token(forzar=True)
        if not token:
            return {"ok": False, "error": "No se pudo renovar token"}
        resultado = _post("product/detail", {"timestamp": int(time.time()), "token": token, "productid": int(product_id)})

    if resultado.get("code") == 200:
        return {
            "ok": True,
            "detail": resultado.get("detail", []),
            "fields": resultado.get("fields", []),
            "packages": resultado.get("package", []),
            "servers": resultado.get("server", []),
        }
    else:
        return {"ok": False, "error": resultado.get("message", "Error desconocido")}


def validar_orden(product_id, fields):
    """
    Valida los datos del jugador antes de crear la orden.
    
    Args:
        product_id: ID del producto/juego
        fields: dict con los campos requeridos, ej: {"input1": "ID_JUGADOR"}
    
    Returns:
        dict con ok y validation_token si es exitoso
    """
    token = obtener_token()
    if not token:
        return {"ok": False, "error": "No se pudo obtener token"}

    resultado = _post("order/validate", {
        "timestamp": int(time.time()),
        "token": token,
        "productid": int(product_id),
        "fields": fields,
    })

    if _es_token_expirado(resultado):
        token = obtener_token(forzar=True)
        if not token:
            return {"ok": False, "error": "No se pudo renovar token"}
        resultado = _post("order/validate", {"timestamp": int(time.time()), "token": token, "productid": int(product_id), "fields": fields})

    if resultado.get("code") == 200:
        return {
            "ok": True,
            "validation_token": resultado.get("validation_token"),
        }
    else:
        return {"ok": False, "error": resultado.get("message", "Error desconocido")}


def crear_orden(package_id, validation_token, merchant_code=""):
    """
    Crea una orden de compra/recarga.
    
    Args:
        package_id: ID del paquete a comprar
        validation_token: Token obtenido de validar_orden
        merchant_code: Código de referencia del merchant (opcional)
    
    Returns:
        dict con ok, code, referenceno, etc.
    """
    token = obtener_token()
    if not token:
        return {"ok": False, "error": "No se pudo obtener token"}

    payload = {
        "timestamp": int(time.time()),
        "token": token,
        "packageid": int(package_id),
        "validate_token": validation_token,
    }
    if merchant_code:
        payload["merchantcode"] = merchant_code

    resultado = _post("order/create", payload)

    if _es_token_expirado(resultado):
        token = obtener_token(forzar=True)
        if not token:
            return {"ok": False, "error": "No se pudo renovar token"}
        payload["token"] = token
        payload["timestamp"] = int(time.time())
        resultado = _post("order/create", payload)

    code = resultado.get("code")

    if code == 100:
        return {
            "ok": True,
            "status": "success",
            "referenceno": resultado.get("referenceno", ""),
            "message": resultado.get("message", ""),
        }
    elif code == 101:
        return {
            "ok": True,
            "status": "pending",
            "referenceno": resultado.get("referenceno", ""),
            "message": resultado.get("message", ""),
        }
    elif code == 102:
        return {
            "ok": False,
            "status": "failed",
            "referenceno": resultado.get("referenceno", ""),
            "reason": resultado.get("reason", ""),
            "message": resultado.get("message", ""),
        }
    else:
        return {
            "ok": False,
            "status": "error",
            "code": code,
            "message": resultado.get("message", "Error desconocido"),
        }


def consultar_orden(referenceno):
    """
    Consulta el estado de una orden por su número de referencia.
    
    Returns:
        dict con status (success/pending/failed), amount, item, ingamename, etc.
    """
    token = obtener_token()
    if not token:
        return {"ok": False, "error": "No se pudo obtener token"}

    resultado = _post("order/inquiry", {
        "timestamp": int(time.time()),
        "token": token,
        "referenceno": referenceno,
    })

    if _es_token_expirado(resultado):
        token = obtener_token(forzar=True)
        if not token:
            return {"ok": False, "error": "No se pudo renovar token"}
        resultado = _post("order/inquiry", {"timestamp": int(time.time()), "token": token, "referenceno": referenceno})

    code = resultado.get("code")

    if code == 100:
        return {
            "ok": True,
            "status": "success",
            "amount": resultado.get("amount"),
            "item": resultado.get("item", ""),
            "merchantcode": resultado.get("merchantcode", ""),
            "ingamename": resultado.get("ingamename", ""),
        }
    elif code == 101:
        return {
            "ok": True,
            "status": "pending",
            "amount": resultado.get("amount"),
            "item": resultado.get("item", ""),
        }
    elif code == 102:
        return {
            "ok": False,
            "status": "failed",
            "reason": resultado.get("reason", ""),
            "amount": resultado.get("amount"),
            "item": resultado.get("item", ""),
        }
    else:
        return {
            "ok": False,
            "status": "error",
            "code": code,
            "message": resultado.get("message", "Error desconocido"),
        }


def recarga_completa(product_id, fields, package_id, merchant_code=""):
    """
    Ejecuta el flujo completo de recarga:
    1. Validar jugador
    2. Crear orden
    3. Consultar resultado
    
    Args:
        product_id: ID del producto/juego
        fields: dict con campos del jugador, ej: {"input1": "ID_JUGADOR"}
        package_id: ID del paquete a comprar
        merchant_code: Código de referencia (opcional)
    
    Returns:
        dict con resultado completo
    """
    # Paso 1: Validar
    print(f"[GAMEPOINT] Validando jugador...")
    val = validar_orden(product_id, fields)
    if not val["ok"]:
        return {"ok": False, "paso": "validacion", "error": val.get("error", "")}

    validation_token = val["validation_token"]
    print(f"[GAMEPOINT] Validación OK: {validation_token}")

    # Paso 2: Crear orden
    print(f"[GAMEPOINT] Creando orden (paquete {package_id})...")
    orden = crear_orden(package_id, validation_token, merchant_code)

    referenceno = orden.get("referenceno", "")
    status = orden.get("status", "")

    if not orden.get("ok") and not referenceno:
        # Falló sin referencia — no hay nada que consultar
        return {
            "ok": False,
            "paso": "orden",
            "error": orden.get("reason", orden.get("message", "")),
        }

    if not orden.get("ok") and referenceno:
        # Falló PERO tiene referencia — GamePoint pudo haberlo procesado igual
        print(f"[GAMEPOINT] Orden reportó error pero tiene ref {referenceno}, verificando...")
        status = "pending"  # Forzar polling

    print(f"[GAMEPOINT] Orden creada: {referenceno} (status: {status})")

    # Paso 3: Si está pending, esperar 4 minutos y verificar una sola vez
    if status == "pending":
        print(f"[GAMEPOINT] Orden pendiente, esperando 4 minutos para verificar...")
        time.sleep(240)
        try:
            inquiry = consultar_orden(referenceno)
            inq_status = inquiry.get("status", "pending")
            print(f"[GAMEPOINT] Resultado después de 4min: {inq_status}")
            if inq_status in ("success", "failed"):
                return {
                    "ok": inq_status == "success",
                    "referenceno": referenceno,
                    "status": inq_status,
                    "ingamename": inquiry.get("ingamename", ""),
                    "amount": inquiry.get("amount"),
                    "item": inquiry.get("item", ""),
                    "message": inquiry.get("message", orden.get("message", "")),
                }
        except Exception as e:
            print(f"[GAMEPOINT] Error consultando después de 4min: {e}")
        # Sigue pending o error — retornar ok para que quede como procesando
        print(f"[GAMEPOINT] Aún pendiente después de 4min, dejando como procesando")
        return {
            "ok": True,
            "referenceno": referenceno,
            "status": "pending",
            "ingamename": "",
            "amount": None,
            "item": "",
            "message": "Orden pendiente, se verificará automáticamente",
        }

    # Para gift cards, consultar siempre para obtener el código
    inquiry = consultar_orden(referenceno)
    return {
        "ok": True,
        "referenceno": referenceno,
        "status": inquiry.get("status", status),
        "ingamename": inquiry.get("ingamename", ""),
        "amount": inquiry.get("amount"),
        "item": inquiry.get("item", ""),
        "message": orden.get("message", ""),
    }


# === Test directo ===
if __name__ == "__main__":
    print("=" * 50)
    print("GamePoint Club API - Test de conexión")
    print("=" * 50)

    # 1. Token
    print("\n[1] Obteniendo token...")
    token = obtener_token()
    if token:
        print(f"    Token: {token}")
    else:
        print("    ERROR: No se pudo obtener token")
        exit(1)

    # 2. Saldo
    print("\n[2] Consultando saldo...")
    saldo = obtener_saldo()
    print(f"    {saldo}")

    # 3. Productos
    print("\n[3] Listando productos...")
    prods = listar_productos()
    if prods["ok"]:
        for p in prods["productos"]:
            print(f"    ID {p['id']}: {p['name']}")
    else:
        print(f"    ERROR: {prods.get('error')}")
