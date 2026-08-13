from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from functools import wraps
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from models import (
    init_db, get_db, get_user_by_id, get_user_by_email, get_user_by_api_key,
    create_user, get_saldo, recargar_saldo, descontar_saldo, rotate_api_key,
    encrypt_pin, decrypt_pin, mask_pin, pin_hash
)
import config
import os
from telegram_bot import notificar_recarga, notificar_stock_bajo, enviar_telegram, enviar_telegram_con_keys
import uuid
import sqlite3
import threading
import json
import time
import re
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta

import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import secrets
import random

PINCENTRAL_RESTOCK_LOCK = threading.Lock()
JADH_RESTOCK_LOCK = threading.Lock()
PINCENTRAL_SCAN_THREAD_GUARD = threading.Lock()
PINCENTRAL_SCAN_THREAD_STARTED = False
PINCENTRAL_SCAN_INTERVAL_SECONDS = 30
PINCENTRAL_SCAN_LOCK_TTL_SECONDS = 180
PINCENTRAL_RESTOCK_CAPTURE_MAX_ATTEMPTS = 3
PINCENTRAL_RESTOCK_CAPTURE_RETRY_DELAY_SECONDS = 2
PINCENTRAL_RESTOCK_BETWEEN_PINS_SECONDS = max(0, int(os.environ.get('PINCENTRAL_RESTOCK_BETWEEN_PINS_SECONDS', '2')))
PINCENTRAL_RESTOCK_AUTH_MAX_ATTEMPTS = max(1, int(os.environ.get('PINCENTRAL_RESTOCK_AUTH_MAX_ATTEMPTS', '3')))
PINCENTRAL_RESTOCK_AUTH_RETRY_BASE_SECONDS = max(1, int(os.environ.get('PINCENTRAL_RESTOCK_AUTH_RETRY_BASE_SECONDS', '2')))
PINCENTRAL_RESTOCK_AUTH_COOLDOWN_SECONDS = max(10, int(os.environ.get('PINCENTRAL_RESTOCK_AUTH_COOLDOWN_SECONDS', '120')))
PINCENTRAL_RESTOCK_CAPTURE_DEFER_RETRY_SECONDS = max(5, int(os.environ.get('PINCENTRAL_RESTOCK_CAPTURE_DEFER_RETRY_SECONDS', '20')))
PINCENTRAL_RESTOCK_INCIDENT_COOLDOWN_SECONDS = max(60, int(os.environ.get('PINCENTRAL_RESTOCK_INCIDENT_COOLDOWN_SECONDS', '120')))
PINCENTRAL_CAPTURE_QUEUE_INTERVAL_SECONDS = max(15, int(os.environ.get('PINCENTRAL_CAPTURE_QUEUE_INTERVAL_SECONDS', '60')))
PINCENTRAL_CAPTURE_QUEUE_MAX_WINDOW_SECONDS = max(120, int(os.environ.get('PINCENTRAL_CAPTURE_QUEUE_MAX_WINDOW_SECONDS', '900')))
PINCENTRAL_CAPTURE_QUEUE_MAX_ATTEMPTS = max(3, int(os.environ.get('PINCENTRAL_CAPTURE_QUEUE_MAX_ATTEMPTS', '15')))
PINCENTRAL_CAPTURE_QUEUE_BATCH_SIZE = max(1, int(os.environ.get('PINCENTRAL_CAPTURE_QUEUE_BATCH_SIZE', '25')))
PINCENTRAL_RESTOCK_PRODUCT_COOLDOWN_UNTIL = {}
PINCENTRAL_RESTOCK_PRODUCT_COOLDOWN_LOCK = threading.Lock()
RECARGA_STATUS_CACHE_TTL_SECONDS = max(1, int(os.environ.get('RECARGA_STATUS_CACHE_TTL_SECONDS', '2')))
RECARGA_STATUS_CACHE_MAX_ITEMS = max(100, int(os.environ.get('RECARGA_STATUS_CACHE_MAX_ITEMS', '5000')))
RECARGA_STATUS_CACHE = {}
RECARGA_STATUS_CACHE_LOCK = threading.Lock()

FREEFIRE_BP_BASE_URL = os.environ.get('FREEFIRE_BP_BASE_URL', 'http://2.24.197.52').rstrip('/')
FREEFIRE_BP_TOKEN = os.environ.get('FREEFIRE_BP_TOKEN', '')

# Configuración de correo electrónico
SMTP_HOST = os.environ.get('SMTP_HOST', 'smtp.gmail.com')
SMTP_PORT = int(os.environ.get('SMTP_PORT', '587'))
SMTP_USER = os.environ.get('SMTP_USER', '')
SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD', '')
SMTP_FROM = os.environ.get('SMTP_FROM', '')

app = Flask(__name__)
app.secret_key = config.SECRET_KEY

# Seguridad de cookies de sesión
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('FLASK_SECURE_COOKIES', '0') == '1'
app.config['PERMANENT_SESSION_LIFETIME'] = 3600 * 8  # 8 horas

# Rate limiter (protección contra fuerza bruta)
limiter = Limiter(get_remote_address, app=app, default_limits=['200 per minute'], storage_uri='memory://')


@app.after_request
def set_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response

UPLOAD_FOLDER = os.path.join(app.static_folder, 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def save_upload(file):
    if file and file.filename and allowed_file(file.filename):
        ext = file.filename.rsplit('.', 1)[1].lower()
        filename = f"{uuid.uuid4().hex}.{ext}"
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        file.save(filepath)
        return f"/static/uploads/{filename}"
    return None


def _config_get(db, key, default=''):
    row = db.execute("SELECT valor FROM configuracion WHERE clave = ?", (key,)).fetchone()
    if not row:
        return default
    return str(row['valor'] or default)


def _config_set(db, key, value):
    value = str(value)
    existing = db.execute("SELECT id FROM configuracion WHERE clave = ?", (key,)).fetchone()
    if existing:
        db.execute("UPDATE configuracion SET valor = ? WHERE clave = ?", (value, key))
    else:
        db.execute("INSERT INTO configuracion (clave, valor) VALUES (?,?)", (key, value))


def _enviar_email(destinatario, asunto, cuerpo_html, cuerpo_texto=None):
    """Envía un correo electrónico usando SMTP. Retorna (ok, error)."""
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = asunto
        msg['From'] = SMTP_FROM
        msg['To'] = destinatario

        texto = cuerpo_texto or re.sub(r'<[^>]+>', '', cuerpo_html)
        msg.attach(MIMEText(texto, 'plain'))
        msg.attach(MIMEText(cuerpo_html, 'html'))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM, [destinatario], msg.as_string())
        return True, None
    except Exception as e:
        return False, str(e)


def _generar_codigo_verificacion(longitud=6):
    return ''.join([str(random.randint(0, 9)) for _ in range(longitud)])


def _generar_token_seguro(longitud=32):
    return secrets.token_urlsafe(longitud)


def _crear_token_usuario(usuario_id, tipo, expiracion_minutos=30):
    db = get_db()
    if tipo == 'verificacion_email':
        token = _generar_codigo_verificacion()
    else:
        token = _generar_token_seguro()
    expiracion = (datetime.now() + timedelta(minutes=expiracion_minutos)).strftime('%Y-%m-%d %H:%M:%S')
    db.execute(
        "UPDATE usuario_tokens SET usado = 1 WHERE usuario_id = ? AND tipo = ? AND usado = 0",
        (usuario_id, tipo)
    )
    db.execute(
        "INSERT INTO usuario_tokens (usuario_id, token, tipo, expiracion) VALUES (?,?,?,?)",
        (usuario_id, token, tipo, expiracion)
    )
    db.commit()
    db.close()
    return token


def _validar_token_usuario(token, tipo):
    db = get_db()
    row = db.execute(
        "SELECT * FROM usuario_tokens WHERE token = ? AND tipo = ? AND usado = 0 ORDER BY id DESC LIMIT 1",
        (token, tipo)
    ).fetchone()
    if not row:
        db.close()
        return None
    exp = _parse_local_datetime(row['expiracion'])
    if not exp or exp < datetime.now():
        db.close()
        return None
    db.close()
    return row


def _marcar_token_usado(token_id):
    db = get_db()
    db.execute("UPDATE usuario_tokens SET usado = 1 WHERE id = ?", (token_id,))
    db.commit()
    db.close()


def _enviar_verificacion_email(usuario_id, email, nombre):
    codigo = _crear_token_usuario(usuario_id, 'verificacion_email', expiracion_minutos=30)
    asunto = f"Verifica tu correo en {os.environ.get('TIENDA_NOMBRE', 'Tienda Gift Ven')}"
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;padding:20px;border:1px solid #eee;border-radius:10px;">
        <h2 style="color:#7c3aed;">Hola {nombre},</h2>
        <p>Gracias por registrarte. Para activar tu cuenta ingresa el siguiente código de verificación:</p>
        <div style="font-size:2rem;font-weight:800;letter-spacing:4px;text-align:center;padding:15px;background:#f3f4f6;border-radius:8px;margin:20px 0;">{codigo}</div>
        <p>Este código expira en 30 minutos.</p>
        <p style="font-size:0.85rem;color:#666;">Si no solicitaste este registro, ignora este mensaje.</p>
    </div>
    """
    return _enviar_email(email, asunto, html)


def _enviar_recuperacion_email(usuario_id, email, nombre):
    token = _crear_token_usuario(usuario_id, 'reset_password', expiracion_minutos=30)
    enlace = url_for('restablecer', token=token, _external=True)
    asunto = f"Recuperación de contraseña en {os.environ.get('TIENDA_NOMBRE', 'Tienda Gift Ven')}"
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;padding:20px;border:1px solid #eee;border-radius:10px;">
        <h2 style="color:#7c3aed;">Hola {nombre},</h2>
        <p>Recibimos una solicitud para restablecer tu contraseña. Haz clic en el siguiente enlace o copia y pégalo en tu navegador:</p>
        <a href="{enlace}" style="display:inline-block;padding:12px 24px;background:#7c3aed;color:#fff;text-decoration:none;border-radius:6px;margin:15px 0;">Restablecer contraseña</a>
        <p style="word-break:break-all;">{enlace}</p>
        <p>Este enlace expira en 30 minutos.</p>
        <p style="font-size:0.85rem;color:#666;">Si no solicitaste esto, ignora este mensaje.</p>
    </div>
    """
    return _enviar_email(email, asunto, html)


def _to_decimal(value, default=None):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default


def _parse_local_datetime(value):
    txt = str(value or '').strip()
    if not txt:
        return None
    txt = txt.replace('T', ' ')
    if '.' in txt:
        txt = txt.split('.', 1)[0]
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            return datetime.strptime(txt, fmt)
        except ValueError:
            continue
    return None


def _suscripcion_activa_desde_row(user_row, now=None):
    if not user_row:
        return False
    ref = now or datetime.now()
    vence = _parse_local_datetime(user_row['suscripcion_hasta'] if 'suscripcion_hasta' in user_row.keys() else '')
    return bool(vence and vence > ref)


def _precio_producto_para_usuario(prod_row, user_row=None):
    precio_base = float((prod_row['precio'] if 'precio' in prod_row.keys() else 0) or 0)
    precio_sub = float((prod_row['precio_suscriptor'] if 'precio_suscriptor' in prod_row.keys() else 0) or 0)
    if user_row and _suscripcion_activa_desde_row(user_row) and precio_sub > 0:
        return precio_sub
    return precio_base


def _bool_autorenovar_desde_row(user_row):
    if not user_row:
        return False
    return int((user_row['autorenovar_suscripcion'] if 'autorenovar_suscripcion' in user_row.keys() else 0) or 0) == 1


def _procesar_autorenovacion_suscripcion(usuario_id):
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    try:
        user = db.execute(
            "SELECT suscripcion_hasta, autorenovar_suscripcion FROM usuarios WHERE id = ?",
            (usuario_id,)
        ).fetchone()
        if not user:
            db.rollback()
            return {'accion': 'sin_usuario'}

        ahora = datetime.now()
        if _suscripcion_activa_desde_row(user, now=ahora):
            db.rollback()
            return {'accion': 'activa'}

        if not _bool_autorenovar_desde_row(user):
            db.rollback()
            return {'accion': 'desactivada'}

        precio_cfg = _config_get(db, 'suscripcion_mensual_precio', '0').replace(',', '.').strip()
        try:
            precio_mensual = float(precio_cfg)
        except (TypeError, ValueError):
            precio_mensual = 0.0
        if precio_mensual <= 0:
            db.rollback()
            return {'accion': 'plan_no_disponible'}

        cartera = db.execute("SELECT saldo FROM carteras WHERE usuario_id = ?", (usuario_id,)).fetchone()
        saldo_actual = float((cartera['saldo'] if cartera else 0) or 0)
        if saldo_actual < precio_mensual:
            db.rollback()
            return {
                'accion': 'saldo_insuficiente',
                'saldo': saldo_actual,
                'precio': precio_mensual,
            }

        saldo_nuevo = saldo_actual - precio_mensual
        db.execute(
            "UPDATE carteras SET saldo = ?, ultima_actualizacion = datetime('now','localtime') WHERE usuario_id = ?",
            (saldo_nuevo, usuario_id)
        )
        db.execute(
            "INSERT INTO transacciones (usuario_id, tipo, monto, saldo_anterior, saldo_nuevo, descripcion) VALUES (?,?,?,?,?,?)",
            (
                usuario_id,
                'compra',
                precio_mensual,
                saldo_actual,
                saldo_nuevo,
                'Auto-renovación suscripción mensual (30 días)',
            )
        )

        nuevo_hasta = ahora + timedelta(days=30)
        db.execute(
            "UPDATE usuarios SET suscripcion_hasta = ? WHERE id = ?",
            (nuevo_hasta.strftime('%Y-%m-%d %H:%M:%S'), usuario_id)
        )
        db.commit()

        return {
            'accion': 'renovada',
            'precio': precio_mensual,
            'nuevo_hasta': nuevo_hasta.strftime('%Y-%m-%d %H:%M:%S'),
            'saldo_nuevo': saldo_nuevo,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _procesar_autorenovaciones_global(max_usuarios=1000):
    db = get_db()
    try:
        rows = db.execute(
            "SELECT id FROM usuarios WHERE autorenovar_suscripcion = 1 ORDER BY id ASC LIMIT ?",
            (int(max_usuarios or 1000),)
        ).fetchall()
    finally:
        db.close()

    resumen = {
        'evaluados': len(rows),
        'renovadas': 0,
        'saldo_insuficiente': 0,
        'sin_cambios': 0,
        'errores': 0,
    }

    for row in rows:
        try:
            result = _procesar_autorenovacion_suscripcion(int(row['id']))
            accion = str(result.get('accion', '') or '').strip().lower()
            if accion == 'renovada':
                resumen['renovadas'] += 1
            elif accion == 'saldo_insuficiente':
                resumen['saldo_insuficiente'] += 1
            elif accion in ('activa', 'desactivada', 'plan_no_disponible', 'sin_usuario'):
                resumen['sin_cambios'] += 1
            else:
                resumen['sin_cambios'] += 1
        except Exception:
            resumen['errores'] += 1

    return resumen


def _actualizar_precios_gamepoint_desde_tasa(db, tasa_myr_usd, margen_porcentaje, target_column='precio', detalle_cache=None):
    from gamepoint_api import detalle_producto

    productos = db.execute(
        "SELECT id, nombre, gamepoint_product_id, gamepoint_package_id "
        "FROM productos WHERE gamepoint_product_id > 0 AND gamepoint_package_id > 0"
    ).fetchall()
    if not productos:
        return {'total': 0, 'actualizados': 0, 'omitidos': 0, 'errores': []}

    target_column = str(target_column or 'precio').strip().lower()
    if target_column not in ('precio', 'precio_suscriptor'):
        target_column = 'precio'

    factor_margen = Decimal('1') + (margen_porcentaje / Decimal('100'))
    detalle_cache = detalle_cache if isinstance(detalle_cache, dict) else {}
    errores = []
    actualizados = 0
    omitidos = 0

    for prod in productos:
        gp_product_id = int(prod['gamepoint_product_id'] or 0)
        gp_package_id = str(int(prod['gamepoint_package_id'] or 0))

        if gp_product_id not in detalle_cache:
            detalle_cache[gp_product_id] = detalle_producto(gp_product_id)
        detalle = detalle_cache[gp_product_id]

        if not detalle.get('ok'):
            omitidos += 1
            errores.append(f"{prod['nombre']}: no se pudo consultar Product ID {gp_product_id}")
            continue

        paquete = None
        for pkg in (detalle.get('packages') or []):
            if str(pkg.get('id', '')).strip() == gp_package_id:
                paquete = pkg
                break

        if not paquete:
            omitidos += 1
            errores.append(f"{prod['nombre']}: no existe Package ID {gp_package_id} en GamePoint")
            continue

        precio_myr = _to_decimal(paquete.get('price'))
        if precio_myr is None or precio_myr <= 0:
            omitidos += 1
            errores.append(f"{prod['nombre']}: precio MYR inválido para Package ID {gp_package_id}")
            continue

        precio_usd = (precio_myr * tasa_myr_usd * factor_margen).quantize(Decimal('0.00000001'))
        if target_column == 'precio_suscriptor':
            db.execute("UPDATE productos SET precio_suscriptor = ? WHERE id = ?", (float(precio_usd), prod['id']))
        else:
            db.execute("UPDATE productos SET precio = ? WHERE id = ?", (float(precio_usd), prod['id']))
        actualizados += 1

    return {
        'total': len(productos),
        'actualizados': actualizados,
        'omitidos': omitidos,
        'errores': errores,
    }


def _snapshot_precios_proveedores(db):
    rows = db.execute(
        "SELECT p.id, p.nombre, p.precio, p.precio_suscriptor, p.gamepoint_product_id, p.moogold_product_id, "
        "c.nombre as categoria_nombre "
        "FROM productos p "
        "LEFT JOIN categorias c ON c.id = p.categoria_id "
        "WHERE (p.gamepoint_product_id > 0 OR p.moogold_product_id > 0)"
    ).fetchall()
    snap = {}
    for r in rows:
        if int(r['gamepoint_product_id'] or 0) > 0 and int(r['moogold_product_id'] or 0) > 0:
            proveedor = 'GamePoint/MooGold'
        elif int(r['gamepoint_product_id'] or 0) > 0:
            proveedor = 'GamePoint'
        elif int(r['moogold_product_id'] or 0) > 0:
            proveedor = 'MooGold'
        else:
            proveedor = '-'
        snap[int(r['id'])] = {
            'producto_id': int(r['id']),
            'producto_nombre': r['nombre'] or '',
            'categoria_nombre': r['categoria_nombre'] or 'Sin categoría',
            'proveedor': proveedor,
            'precio': float(r['precio'] or 0),
            'precio_suscriptor': float(r['precio_suscriptor'] or 0),
        }
    return snap


def _ejecutar_refresh_precios_proveedores(db, origen='manual'):
    tasa_txt = str(_config_get(db, 'gamepoint_myr_usd_rate', '0.252205') or '').strip().replace(',', '.')
    gp_margin_txt = str(_config_get(db, 'gamepoint_margin_percent', '6') or '').strip().replace(',', '.')
    gp_sub_margin_txt = str(_config_get(db, 'gamepoint_margin_percent_subscriber', gp_margin_txt or '6') or '').strip().replace(',', '.')
    mg_margin_txt = str(_config_get(db, 'moogold_margin_percent', '6') or '').strip().replace(',', '.')
    mg_sub_margin_txt = str(_config_get(db, 'moogold_margin_percent_subscriber', mg_margin_txt or '6') or '').strip().replace(',', '.')

    tasa_myr_usd = _to_decimal(tasa_txt)
    gp_margin = _to_decimal(gp_margin_txt, Decimal('0'))
    gp_sub_margin = _to_decimal(gp_sub_margin_txt, Decimal('0'))
    mg_margin = _to_decimal(mg_margin_txt, Decimal('0'))
    mg_sub_margin = _to_decimal(mg_sub_margin_txt, Decimal('0'))

    if tasa_myr_usd is None or tasa_myr_usd <= 0:
        return {'ok': False, 'error': 'Tasa GamePoint inválida'}
    if gp_margin is None or gp_margin < 0 or gp_sub_margin is None or gp_sub_margin < 0:
        return {'ok': False, 'error': 'Margen GamePoint inválido'}
    if mg_margin is None or mg_margin < 0 or mg_sub_margin is None or mg_sub_margin < 0:
        return {'ok': False, 'error': 'Margen MooGold inválido'}

    before = _snapshot_precios_proveedores(db)

    gp_cache = {}
    gp_normal = _actualizar_precios_gamepoint_desde_tasa(
        db,
        tasa_myr_usd,
        gp_margin,
        target_column='precio',
        detalle_cache=gp_cache,
    )
    gp_sub = _actualizar_precios_gamepoint_desde_tasa(
        db,
        tasa_myr_usd,
        gp_sub_margin,
        target_column='precio_suscriptor',
        detalle_cache=gp_cache,
    )

    mg_normal = _actualizar_precios_moogold_desde_margen(db, mg_margin, target_column='precio')
    mg_sub = _actualizar_precios_moogold_desde_margen(db, mg_sub_margin, target_column='precio_suscriptor')

    after = _snapshot_precios_proveedores(db)
    cambios = []
    for pid, cur in after.items():
        prev = before.get(pid)
        if not prev:
            continue
        for campo in ('precio', 'precio_suscriptor'):
            anterior = float(prev.get(campo, 0) or 0)
            nuevo = float(cur.get(campo, 0) or 0)
            if abs(anterior - nuevo) > 0.00000001:
                cambios.append({
                    'proveedor': cur.get('proveedor', '-'),
                    'producto_id': pid,
                    'producto_nombre': cur.get('producto_nombre', ''),
                    'categoria_nombre': cur.get('categoria_nombre', ''),
                    'campo': campo,
                    'precio_anterior': anterior,
                    'precio_nuevo': nuevo,
                })

    resumen = {
        'gamepoint_normal': gp_normal,
        'gamepoint_suscriptor': gp_sub,
        'moogold_normal': mg_normal,
        'moogold_suscriptor': mg_sub,
    }

    cur = db.execute(
        "INSERT INTO precios_refresh_runs (origen, total_cambios, detalles_json) VALUES (?,?,?)",
        (str(origen or 'manual'), len(cambios), json.dumps(resumen, ensure_ascii=False))
    )
    run_id = int(cur.lastrowid or 0)

    for c in cambios:
        db.execute(
            "INSERT INTO precios_refresh_cambios "
            "(run_id, proveedor, producto_id, producto_nombre, categoria_nombre, campo, precio_anterior, precio_nuevo) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                run_id,
                c['proveedor'],
                c['producto_id'],
                c['producto_nombre'],
                c['categoria_nombre'],
                c['campo'],
                c['precio_anterior'],
                c['precio_nuevo'],
            )
        )

    return {
        'ok': True,
        'run_id': run_id,
        'total_cambios': len(cambios),
        'resumen': resumen,
    }


def _build_refresh_prices_telegram_message(db, run_id, total_cambios):
    run = db.execute(
        "SELECT id, fecha FROM precios_refresh_runs WHERE id = ? LIMIT 1",
        (int(run_id or 0),),
    ).fetchone()
    fecha_txt = str((run['fecha'] if run else '') or '').strip() or datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    cambios = db.execute(
        "SELECT categoria_nombre FROM precios_refresh_cambios WHERE run_id = ? ORDER BY id ASC",
        (int(run_id or 0),),
    ).fetchall()

    juegos = []
    vistos = set()
    for row in cambios:
        nombre = str((row['categoria_nombre'] if row else '') or '').strip()
        if not nombre:
            continue
        key = nombre.lower()
        if key in vistos:
            continue
        vistos.add(key)
        juegos.append(nombre)

    juegos_txt = '\n'.join(f"- {j}" for j in juegos) if juegos else '- Sin categoría definida'
    return (
        "<b>Cambio de precios</b>\n\n"
        f"Fecha: <b>{fecha_txt}</b>\n"
        f"Cambios detectados: <b>{int(total_cambios or 0)}</b>\n\n"
        "Juegos que tuvieron cambio de precios:\n"
        f"{juegos_txt}"
    )


def _registrar_refresh_run_desde_snapshots(db, before, after, origen='manual', proveedor_filtro=''):
    filtro = str(proveedor_filtro or '').strip().lower()
    cambios = []

    for pid, cur in (after or {}).items():
        prev = (before or {}).get(pid)
        if not prev:
            continue

        proveedor = str(cur.get('proveedor', '') or '').strip().lower()
        if filtro and proveedor != filtro:
            continue

        for campo in ('precio', 'precio_suscriptor'):
            anterior = float(prev.get(campo, 0) or 0)
            nuevo = float(cur.get(campo, 0) or 0)
            if abs(anterior - nuevo) <= 0.00000001:
                continue
            cambios.append({
                'proveedor': cur.get('proveedor', '-'),
                'producto_id': int(pid or 0),
                'producto_nombre': cur.get('producto_nombre', ''),
                'categoria_nombre': cur.get('categoria_nombre', ''),
                'campo': campo,
                'precio_anterior': anterior,
                'precio_nuevo': nuevo,
            })

    resumen = {
        'origen': str(origen or 'manual'),
        'proveedor_filtro': filtro or '-',
    }
    cur = db.execute(
        "INSERT INTO precios_refresh_runs (origen, total_cambios, detalles_json) VALUES (?,?,?)",
        (str(origen or 'manual'), len(cambios), json.dumps(resumen, ensure_ascii=False))
    )
    run_id = int(cur.lastrowid or 0)

    for c in cambios:
        db.execute(
            "INSERT INTO precios_refresh_cambios "
            "(run_id, proveedor, producto_id, producto_nombre, categoria_nombre, campo, precio_anterior, precio_nuevo) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                run_id,
                c['proveedor'],
                c['producto_id'],
                c['producto_nombre'],
                c['categoria_nombre'],
                c['campo'],
                c['precio_anterior'],
                c['precio_nuevo'],
            )
        )

    return {'run_id': run_id, 'total_cambios': len(cambios)}


def _moogold_stock_variacion(mg_product_id, mg_variation_id, cache=None):
    from moogold_api import detalle_producto

    mg_product_id = int(mg_product_id or 0)
    mg_variation_id = str(int(mg_variation_id or 0)) if str(mg_variation_id or '0').strip().isdigit() else ''
    if mg_product_id <= 0 or not mg_variation_id:
        return {'ok': False, 'status': '', 'disponible': True, 'error': 'Product/Variation MooGold inválidos'}

    cache = cache if isinstance(cache, dict) else {}
    if mg_product_id in cache:
        detalle = cache[mg_product_id]
    else:
        detalle = detalle_producto(mg_product_id)
        cache[mg_product_id] = detalle

    if not detalle.get('ok'):
        return {'ok': False, 'status': '', 'disponible': True, 'error': detalle.get('error', 'No se pudo consultar MooGold')}

    data = detalle.get('data') if isinstance(detalle.get('data'), dict) else {}
    variaciones = data.get('Variation') if isinstance(data.get('Variation'), list) else []
    for var in variaciones:
        if str((var or {}).get('variation_id', '')).strip() == mg_variation_id:
            status = str((var or {}).get('stock_status') or '').strip().lower()
            if status == 'outofstock':
                return {'ok': True, 'status': status, 'disponible': False, 'variation': var}
            if status == 'instock':
                return {'ok': True, 'status': status, 'disponible': True, 'variation': var}
            return {'ok': True, 'status': status, 'disponible': True, 'variation': var}
    return {'ok': False, 'status': '', 'disponible': True, 'error': 'Variation ID no encontrado en MooGold'}


def _actualizar_precio_moogold_producto_desde_margen(db, margen_porcentaje, mg_product_id, mg_variation_id, prod_id=0, target_column='precio'):
    from moogold_api import detalle_producto

    mg_product_id = int(mg_product_id or 0)
    mg_variation_id = str(int(mg_variation_id or 0)) if str(mg_variation_id or '0').strip().isdigit() else ''
    if mg_product_id <= 0 or not mg_variation_id:
        return {'ok': False, 'error': 'Product/Variation MooGold inválidos'}

    factor_margen = Decimal('1') + (margen_porcentaje / Decimal('100'))
    detalle = detalle_producto(mg_product_id)
    if not detalle.get('ok'):
        return {'ok': False, 'error': f"No se pudo consultar Product ID {mg_product_id}"}

    data = detalle.get('data') if isinstance(detalle.get('data'), dict) else {}
    variaciones = data.get('Variation') if isinstance(data.get('Variation'), list) else []

    variacion = None
    for var in variaciones:
        if str((var or {}).get('variation_id', '')).strip() == mg_variation_id:
            variacion = var or {}
            break

    if not variacion:
        return {'ok': False, 'error': f"No existe Variation ID {mg_variation_id} en MooGold"}

    costo_usd = _to_decimal(variacion.get('variation_price'))
    if costo_usd is None or costo_usd <= 0:
        return {'ok': False, 'error': f"Costo USD inválido en Variation ID {mg_variation_id}"}

    target_column = str(target_column or 'precio').strip().lower()
    if target_column not in ('precio', 'precio_suscriptor'):
        target_column = 'precio'

    precio_venta = (costo_usd * factor_margen).quantize(Decimal('0.00000001'))
    if int(prod_id or 0) > 0:
        if target_column == 'precio_suscriptor':
            db.execute("UPDATE productos SET precio_suscriptor = ? WHERE id = ?", (float(precio_venta), int(prod_id)))
        else:
            db.execute("UPDATE productos SET precio = ? WHERE id = ?", (float(precio_venta), int(prod_id)))

    return {
        'ok': True,
        'precio': float(precio_venta),
        'costo_usd': float(costo_usd),
        'variation_id': mg_variation_id,
        'product_id': mg_product_id,
    }


def _actualizar_precios_moogold_desde_margen(db, margen_porcentaje, target_column='precio'):
    productos = db.execute(
        "SELECT id, nombre, moogold_product_id, moogold_variation_id "
        "FROM productos WHERE moogold_product_id > 0 AND moogold_variation_id > 0"
    ).fetchall()
    if not productos:
        return {'total': 0, 'actualizados': 0, 'omitidos': 0, 'errores': []}

    errores = []
    actualizados = 0
    omitidos = 0

    for prod in productos:
        mg_product_id = int(prod['moogold_product_id'] or 0)
        mg_variation_id = int(prod['moogold_variation_id'] or 0)

        result = _actualizar_precio_moogold_producto_desde_margen(
            db,
            margen_porcentaje,
            mg_product_id,
            mg_variation_id,
            prod_id=prod['id'],
            target_column=target_column,
        )
        if not result.get('ok'):
            omitidos += 1
            errores.append(f"{prod['nombre']}: {result.get('error', 'sin detalle')}")
            continue

        actualizados += 1

    return {
        'total': len(productos),
        'actualizados': actualizados,
        'omitidos': omitidos,
        'errores': errores,
    }


def _estado_interno_desde_moogold(estado_moogold):
    estado = str(estado_moogold or '').strip().lower()
    if estado in ('completed', 'success', 'delivered'):
        return 'completado'
    if estado in ('refunded', 'incorrect-details', 'failed', 'cancelled', 'canceled'):
        return 'cancelado'
    return 'procesando'


def _moogold_category_catalogo():
    return [
        {'id': 1, 'name': 'DTLU (Direct Top Up)'},
        {'id': 2, 'name': 'Gift Cards'},
        {'id': 50, 'name': 'Direct Top Up (legacy)'},
        {'id': 51, 'name': 'Other Gift Cards (legacy)'},
        {'id': 451, 'name': 'Razer Gold'},
        {'id': 538, 'name': 'Google Play'},
        {'id': 765, 'name': 'PSN'},
        {'id': 766, 'name': 'Garena Shells'},
        {'id': 874, 'name': 'Netflix'},
        {'id': 992, 'name': 'Spotify'},
        {'id': 993, 'name': 'Steam'},
        {'id': 1223, 'name': 'League of Legends'},
        {'id': 1261, 'name': 'Riot Access Code'},
        {'id': 1391, 'name': 'Amazon Gift Cards'},
        {'id': 1444, 'name': 'Apple Music'},
        {'id': 2377, 'name': 'Apex Legends'},
        {'id': 2433, 'name': 'iTunes Gift Card'},
        {'id': 3075, 'name': 'Bilibili'},
        {'id': 3154, 'name': 'XBox Gift Card'},
        {'id': 3351, 'name': 'Astro Pay'},
        {'id': 3381, 'name': 'NetEase Pay'},
        {'id': 3382, 'name': 'iQIYI'},
        {'id': 3563, 'name': 'Roblox'},
        {'id': 3737, 'name': 'Nintendo Gift Card'},
    ]


def _moogold_category_efectiva(categoria_tipo, category_configured):
    tipo = str(categoria_tipo or '').strip().lower()
    if tipo == 'giftcards':
        return 2

    try:
        cat = int(category_configured or 0)
    except Exception:
        cat = 0

    if cat == 51:
        return 2
    if cat == 50:
        return 1
    if cat in (1, 2):
        return cat
    return 1


def _moogold_parse_fields(fields_raw):
    return [f.get('name', '') for f in _moogold_parse_field_defs(fields_raw)]


def _moogold_parse_field_defs(fields_raw):
    txt = str(fields_raw or '').strip()
    if not txt:
        return []
    try:
        payload = json.loads(txt)
    except Exception:
        return []
    if not isinstance(payload, list):
        return []

    out = []
    for idx, f in enumerate(payload):
        if isinstance(f, dict):
            name = str(f.get('name', '') or f.get('field', '') or f.get('key', '') or '').strip()
            if not name:
                continue
            desc = str(f.get('desc', '') or f.get('label', '') or f.get('placeholder', '') or name).strip()
            ftype = str(f.get('type', '') or f.get('input_type', '') or 'text').strip().lower()

            options_raw = f.get('options', [])
            if not isinstance(options_raw, list):
                for alt_key in ('values', 'choices', 'items', 'list'):
                    alt = f.get(alt_key, [])
                    if isinstance(alt, list):
                        options_raw = alt
                        break
                else:
                    options_raw = []

            options = []
            for opt in options_raw:
                if isinstance(opt, dict):
                    opt_value = str(
                        opt.get('value', '') or opt.get('id', '') or opt.get('key', '') or opt.get('code', '')
                        or opt.get('name', '') or opt.get('label', '')
                    ).strip()
                    opt_label = str(
                        opt.get('label', '') or opt.get('name', '') or opt.get('title', '') or opt.get('value', '')
                        or opt.get('id', '')
                    ).strip()
                    if opt_value:
                        options.append({'value': opt_value, 'label': opt_label or opt_value})
                    continue

                opt_txt = str(opt or '').strip()
                if opt_txt:
                    options.append(opt_txt)

            out.append({
                'name': name,
                'desc': desc or name,
                'type': ftype or 'text',
                'options': options,
                'index': idx,
            })
            continue

        name = str(f or '').strip()
        if name:
            out.append({
                'name': name,
                'desc': name,
                'type': 'text',
                'options': [],
                'index': idx,
            })
    return out


def _extract_named_inputs(source, field_names, prefix='mg_field_'):
    def _read(key):
        if not key:
            return ''
        try:
            val = source.get(key, '')
        except Exception:
            return ''
        return str(val or '').strip()

    out = {}
    for idx, name in enumerate(field_names or []):
        key_idx = f'{prefix}{idx}'
        value = _read(key_idx)
        if not value and name:
            value = _read(name)
        if value:
            out[key_idx] = value
            if name:
                out[name] = value
    return out


def _moogold_build_account_fields(fields_raw, id_juego, input2='', extra_inputs=None):
    names = _moogold_parse_fields(fields_raw)
    v1 = str(id_juego or '').strip()
    v2 = str(input2 or '').strip()
    extra = extra_inputs if isinstance(extra_inputs, dict) else {}

    if v1 and not v2:
        for sep in ('|', ',', ':'):
            if sep in v1:
                left, right = v1.split(sep, 1)
                v1 = left.strip()
                v2 = right.strip()
                break

    if not names:
        base = {}
        if v1:
            base['User ID'] = v1
        if v2:
            base['Zone ID'] = v2
        return base

    out = {}
    for idx, name in enumerate(names):
        value = ''
        if name:
            value = str(extra.get(name, '') or '').strip()
        if not value:
            value = str(extra.get(f'mg_field_{idx}', '') or '').strip()
        if not value:
            if idx == 0:
                value = v1
            elif idx == 1:
                value = v2
            elif idx >= 2:
                value = str(extra.get(f'input{idx + 1}', '') or '').strip()
        if value:
            out[name] = value
    return out


def _moogold_extract_ref(data):
    if not isinstance(data, dict):
        return ''
    for key in ('order_id', 'orderid', 'id', 'transaction_id', 'trx_id'):
        val = str(data.get(key, '') or '').strip()
        if val:
            return val
    return ''


def _moogold_clean_code(value):
    if isinstance(value, (list, tuple, dict)):
        return ''
    return str(value or '').replace('\r', '').replace('\n', '').strip()


def _moogold_format_code_text(value):
    txt = _moogold_clean_code(value)
    if not txt:
        return ''
    m = re.search(r'(?i)serial\s*:\s*(.+?)\s+pin\s*:\s*(.+)$', txt)
    if m:
        return f"Serial: {m.group(1).strip()}\nPIN: {m.group(2).strip()}"
    return txt


def _moogold_extract_code(data):
    if not isinstance(data, dict):
        return ''

    def format_serial_pin(row):
        if not isinstance(row, dict):
            return ''
        serial = ''
        pin = ''
        for key in ('serial', 'serial_number', 'serial_no', 'card_serial', 'sn'):
            serial = _moogold_clean_code(row.get(key))
            if serial:
                break
        for key in ('pin', 'pin_code', 'voucher_code', 'voucher', 'code', 'gift_code', 'giftcode'):
            raw_pin = row.get(key)
            if isinstance(raw_pin, list):
                pin = '\n'.join([_moogold_format_code_text(x) for x in raw_pin if _moogold_format_code_text(x)])
            else:
                pin = _moogold_format_code_text(raw_pin)
            if pin:
                break
        if serial and pin:
            return f"Serial: {serial}\nPIN: {pin}"
        return pin or serial

    direct_pair = format_serial_pin(data)
    if direct_pair:
        return direct_pair

    for key in ('voucher_code', 'voucher', 'code', 'pin', 'gift_code', 'giftcode'):
        val = data.get(key)
        if isinstance(val, list):
            codigos = []
            for x in val:
                txt = format_serial_pin(x) if isinstance(x, dict) else _moogold_format_code_text(x)
                if txt:
                    codigos.append(txt)
            if codigos:
                return '\n'.join(codigos)
        else:
            txt = _moogold_format_code_text(val)
            if txt:
                return txt

    for list_key in ('item', 'items', 'codes', 'vouchers', 'cards'):
        items = data.get(list_key)
        if isinstance(items, list):
            codigos = []
            for row in items:
                txt = format_serial_pin(row) if isinstance(row, dict) else _moogold_format_code_text(row)
                if txt:
                    codigos.append(txt)
            if codigos:
                return '\n'.join(codigos)
    return ''


def _moogold_extract_nombre(data, fallback=''):
    if isinstance(data, dict):
        account_details = data.get('account_details') if isinstance(data.get('account_details'), dict) else {}
        for key in ('Username', 'username', 'Ingame Name', 'Player ID', 'User ID'):
            txt = str(account_details.get(key, '') or '').strip()
            if txt:
                return txt

        for key in ('username', 'ingame_name', 'player_id', 'user_id'):
            txt = str(data.get(key, '') or '').strip()
            if txt:
                return txt
    return str(fallback or '').strip()


def _moogold_order_detail_safe(order_ref):
    ref = str(order_ref or '').strip()
    if not ref.isdigit():
        return {}
    try:
        from moogold_api import consultar_orden
        detalle = consultar_orden(int(ref))
    except Exception:
        return {}
    if not isinstance(detalle, dict) or not detalle.get('ok'):
        return {}
    data = detalle.get('data')
    return data if isinstance(data, dict) else {}


def _procesar_callback_moogold(db, pedido, payload):
    pedido_id = int(pedido['id'])
    usuario_id = int(pedido['usuario_id'])
    producto_id = int(pedido['producto_id'])
    estado_actual = str(pedido['estado'] or '').strip().lower()
    total = float(pedido['total'] or 0)

    estado_moogold = str(payload.get('status', '') or '').strip().lower()
    mensaje = str(payload.get('message', '') or '').strip()
    estado_nuevo = _estado_interno_desde_moogold(estado_moogold)
    ref_externa = str(payload.get('order_id', '') or '').strip()

    account_details = payload.get('account_details') if isinstance(payload.get('account_details'), dict) else {}
    nombre_jugador_prev = str((pedido['nombre_jugador'] if 'nombre_jugador' in pedido.keys() else '') or '').strip()
    referencia_prev = str((pedido['referencia_externa'] if 'referencia_externa' in pedido.keys() else '') or '').strip()
    nombre_jugador = (
        str(account_details.get('Username', '') or '').strip()
        or str(account_details.get('User ID', '') or '').strip()
        or nombre_jugador_prev
    )
    codigo = _moogold_extract_code(payload)

    _registrar_auditoria_recarga(
        pedido_id=pedido_id,
        usuario_id=usuario_id,
        producto_id=producto_id,
        proveedor='moogold',
        etapa='callback',
        estado=estado_moogold,
        detalle=mensaje,
        referencia=ref_externa or referencia_prev,
        payload=payload,
    )

    if estado_nuevo == 'completado' and estado_actual != 'completado':
        if codigo:
            db.execute(
                "UPDATE pedidos SET estado = 'completado', nombre_jugador = ?, codigo_entregado = ?, referencia_externa = COALESCE(NULLIF(referencia_externa, ''), ?) WHERE id = ?",
                (nombre_jugador, codigo, ref_externa, pedido_id),
            )
        else:
            db.execute(
                "UPDATE pedidos SET estado = 'completado', nombre_jugador = ?, referencia_externa = COALESCE(NULLIF(referencia_externa, ''), ?) WHERE id = ?",
                (nombre_jugador, ref_externa, pedido_id),
            )
        enviar_webhook(usuario_id, {
            'evento': 'pedido_actualizado',
            'pedido_id': pedido_id,
            'estado': 'completado',
            'referencia': ref_externa,
            'nombre_jugador': nombre_jugador,
            'codigo': codigo,
            'mensaje': mensaje or 'Pedido completado por proveedor',
        })
        return {'accion': 'completado'}

    if estado_nuevo == 'cancelado' and estado_actual != 'cancelado':
        db.execute(
            "UPDATE pedidos SET estado = 'cancelado', referencia_externa = COALESCE(NULLIF(referencia_externa, ''), ?) WHERE id = ?",
            (ref_externa, pedido_id),
        )
        db.commit()
        try:
            recargar_saldo(usuario_id, total, f"Reembolso MooGold callback pedido #{pedido_id} ({estado_moogold or 'cancelado'})")
        except Exception as e:
            _registrar_auditoria_recarga(
                pedido_id=pedido_id,
                usuario_id=usuario_id,
                producto_id=producto_id,
                proveedor='moogold',
                etapa='callback_refund_error',
                estado=estado_moogold,
                detalle=f"Error en recargar_saldo: {e}",
                referencia=ref_externa or referencia_prev,
                payload=payload,
            )
        enviar_webhook(usuario_id, {
            'evento': 'pedido_actualizado',
            'pedido_id': pedido_id,
            'estado': 'cancelado',
            'referencia': ref_externa,
            'razon': mensaje or estado_moogold or 'Pedido cancelado por proveedor',
            'reembolso': float(total),
        })
        return {'accion': 'cancelado_reembolsado'}

    if estado_nuevo == 'procesando' and estado_actual in ('pendiente', 'procesando'):
        db.execute(
            "UPDATE pedidos SET estado = 'procesando', referencia_externa = COALESCE(NULLIF(referencia_externa, ''), referencia_externa) WHERE id = ?",
            (pedido_id,),
        )
        return {'accion': 'procesando'}

    return {'accion': 'sin_cambios'}


def _sincronizar_pedido_moogold_si_pendiente(db, pedido):
    if not pedido:
        return {'accion': 'sin_pedido'}

    estado_actual = str(pedido['estado'] or '').strip().lower()
    if estado_actual not in ('pendiente', 'procesando'):
        return {'accion': 'estado_final'}

    prod = db.execute(
        "SELECT moogold_category_id, moogold_variation_id FROM productos WHERE id = ? LIMIT 1",
        (int(pedido['producto_id']),),
    ).fetchone()
    if not prod:
        return {'accion': 'sin_producto'}

    mg_cat = int((prod['moogold_category_id'] if 'moogold_category_id' in prod.keys() else 0) or 0)
    mg_var = int((prod['moogold_variation_id'] if 'moogold_variation_id' in prod.keys() else 0) or 0)
    if mg_cat <= 0 or mg_var <= 0:
        return {'accion': 'no_moogold'}

    ref_externa = str(pedido['referencia_externa'] or '').strip()
    ref_cliente = str(pedido['referencia_cliente'] or '').strip()

    detail_data = {}
    if ref_externa:
        detail_data = _moogold_order_detail_safe(ref_externa)

    if not detail_data and ref_cliente:
        try:
            from moogold_api import consultar_orden_partner
            by_partner = consultar_orden_partner(ref_cliente)
            if isinstance(by_partner, dict) and by_partner.get('ok'):
                data = by_partner.get('data')
                if isinstance(data, dict):
                    detail_data = data
        except Exception:
            detail_data = {}

    if not detail_data:
        aud = db.execute(
            "SELECT estado, detalle, referencia FROM recargas_auditoria "
            "WHERE pedido_id = ? AND proveedor = 'moogold' AND etapa = 'callback' "
            "ORDER BY id DESC LIMIT 1",
            (int(pedido['id']),),
        ).fetchone()
        if aud:
            estado_aud = str((aud['estado'] if 'estado' in aud.keys() else '') or '').strip().lower()
            if estado_aud:
                payload = {
                    'status': estado_aud,
                    'message': str((aud['detalle'] if 'detalle' in aud.keys() else '') or '').strip(),
                    'order_id': str((aud['referencia'] if 'referencia' in aud.keys() else '') or ref_externa or '').strip(),
                }
                return _procesar_callback_moogold(db, pedido, payload)
        return {'accion': 'sin_detalle'}

    estado_mg = str(detail_data.get('order_status', '') or detail_data.get('status', '') or '').strip().lower()
    if not estado_mg:
        return {'accion': 'sin_estado'}

    payload = dict(detail_data)
    payload['status'] = estado_mg
    if ref_externa and not payload.get('order_id'):
        payload['order_id'] = ref_externa

    return _procesar_callback_moogold(db, pedido, payload)


def _recarga_status_cache_get(cache_key):
    now = time.time()
    with RECARGA_STATUS_CACHE_LOCK:
        item = RECARGA_STATUS_CACHE.get(cache_key)
        if not item:
            return None
        expires_at, status_code, payload = item
        if expires_at <= now:
            RECARGA_STATUS_CACHE.pop(cache_key, None)
            return None
        return status_code, payload


def _recarga_status_cache_set(cache_key, status_code, payload):
    now = time.time()
    expires_at = now + RECARGA_STATUS_CACHE_TTL_SECONDS
    with RECARGA_STATUS_CACHE_LOCK:
        if len(RECARGA_STATUS_CACHE) >= RECARGA_STATUS_CACHE_MAX_ITEMS:
            expiradas = [k for k, (exp, _, _) in RECARGA_STATUS_CACHE.items() if exp <= now]
            for k in expiradas:
                RECARGA_STATUS_CACHE.pop(k, None)
            if len(RECARGA_STATUS_CACHE) >= RECARGA_STATUS_CACHE_MAX_ITEMS:
                oldest_key = min(RECARGA_STATUS_CACHE.items(), key=lambda kv: kv[1][0])[0]
                RECARGA_STATUS_CACHE.pop(oldest_key, None)
        RECARGA_STATUS_CACHE[cache_key] = (expires_at, int(status_code), dict(payload))


def _obtener_popup_publicitario_para_usuario(db, user_id):
    activo = _config_get(db, 'popup_publicitario_activo', '0') == '1'
    imagen = _config_get(db, 'popup_publicitario_imagen', '').strip()
    try:
        max_vistas = int(_config_get(db, 'popup_publicitario_max_vistas', '0') or 0)
    except (TypeError, ValueError):
        max_vistas = 0
    try:
        version = int(_config_get(db, 'popup_publicitario_version', '1') or 1)
    except (TypeError, ValueError):
        version = 1

    if not activo or not imagen or max_vistas <= 0:
        return None

    row = db.execute(
        "SELECT vistas FROM popup_publicidad_vistas WHERE usuario_id = ? AND version = ?",
        (user_id, version),
    ).fetchone()
    vistas_actuales = int(row['vistas']) if row else 0
    if vistas_actuales >= max_vistas:
        return None

    return {
        'imagen': imagen,
        'version': version,
        'max_vistas': max_vistas,
        'vistas_actuales': vistas_actuales,
    }


init_db()


def _extraer_digitos(texto):
    return ''.join(ch for ch in str(texto or '') if ch.isdigit())


def _normalizar_metodo(metodo_pago):
    raw = str(metodo_pago or '').strip().lower()
    return raw.replace(' ', '').replace('_', '').replace('-', '')


def _es_binance(metodo_pago):
    m = _normalizar_metodo(metodo_pago)
    return 'binance' in m


def _verificar_pago_binance_solicitud(db, sol):
    """Verifica pago por Binance usando la API de Recargas Homero.
    La API inactiva (reclama) el pago automáticamente si es válido.
    Solo se aceptan pagos en USDT."""
    referencia_ingresada = str(sol['referencia'] or '').strip()
    ref_digits = _extraer_digitos(referencia_ingresada)
    if len(ref_digits) < 8:
        return {
            'ok': False,
            'error': 'La referencia debe contener al menos 8 dígitos para verificar pago Binance.',
        }

    try:
        monto_objetivo = Decimal(str(sol['monto']))
    except (InvalidOperation, TypeError):
        return {'ok': False, 'error': 'Monto de solicitud inválido para verificación Binance.'}

    api_url = (config.BINANCE_MOV_API_URL or '').strip()
    api_key = (config.BINANCE_MOV_API_TOKEN or '').strip()
    if not api_url or not api_key:
        return {'ok': False, 'error': 'API de verificación Binance no configurada'}

    try:
        resp = requests.post(
            api_url,
            json={
                'api_key': api_key,
                'metodo': 'binance',
                'referencia': ref_digits,
            },
            headers={'Content-Type': 'application/json'},
            timeout=30,
            verify=config.BINANCE_MOV_VERIFY_SSL,
        )
        data = resp.json() if resp.text else {}
    except Exception as e:
        return {'ok': False, 'error': f'Error consultando API Binance: {e}'}

    if not isinstance(data, dict):
        return {'ok': False, 'error': 'Respuesta inválida de API Binance'}

    estado = str(data.get('status', '') or '').strip().lower()
    if estado == 'unauthorized':
        return {'ok': False, 'error': 'API key inválida o inactiva.'}
    if estado == 'not_found':
        return {'ok': False, 'error': f'No se encontró pago Binance con referencia {ref_digits}.'}
    if estado == 'claimed':
        return {'ok': False, 'error': 'Esta transacción ya fue reclamada anteriormente.'}
    if estado != 'valid':
        return {'ok': False, 'error': data.get('message', 'Respuesta inesperada de la API de pagos.')}

    data_obj = data.get('data') or {}
    referencia_full = str(data_obj.get('referencia', '') or ref_digits).strip()
    moneda = str(data_obj.get('moneda', '') or '').strip().upper()
    if moneda != 'USDT':
        return {'ok': False, 'error': f'La transacción está en {moneda or "moneda desconocida"}, solo se acepta USDT.'}

    try:
        monto_real = Decimal(str(data_obj.get('monto_real_num', '0')))
    except (InvalidOperation, TypeError):
        return {'ok': False, 'error': 'Monto real inválido devuelto por la API de pagos.'}

    if monto_real != monto_objetivo:
        return {
            'ok': False,
            'error': f'El monto del pago ({monto_real} USDT) no coincide con el monto solicitado ({monto_objetivo} USDT).',
        }

    usada = db.execute(
        "SELECT id, usuario_id FROM referencias_pago_usadas WHERE referencia_full = ? LIMIT 1",
        (referencia_full,),
    ).fetchone()
    if usada:
        if int(usada['usuario_id']) != int(sol['usuario_id']):
            return {'ok': False, 'error': 'Esta referencia ya fue utilizada por otro cliente.'}
        return {'ok': False, 'error': 'Esta referencia ya fue utilizada anteriormente.'}

    match = {
        'referencia_full': referencia_full,
        'referencia_suffix8': ref_digits[-8:],
        'monto': float(monto_real),
        'fecha': str(data_obj.get('fecha', '') or ''),
    }

    db.execute(
        "INSERT INTO referencias_pago_usadas (solicitud_id, usuario_id, referencia_ingresada, referencia_suffix8, referencia_full, monto, moneda, fecha_movimiento) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            int(sol['id']),
            int(sol['usuario_id']),
            referencia_ingresada,
            match['referencia_suffix8'],
            match['referencia_full'],
            match['monto'],
            'USDT',
            match['fecha'],
        ),
    )
    return {'ok': True, 'match': match}


def _verificar_freefire_levelpass(player_id, levelpass_key, validar_id_tipo=None):
    """Consulta la API de Free Fire BO para saber si un pase de nivel está disponible.
    Si validar_id_tipo se especifica, primero verifica que el ID del jugador exista."""
    player_id = str(player_id or '').strip()
    levelpass_key = str(levelpass_key or '').strip()
    if not player_id or not levelpass_key:
        return {'ok': False, 'error': 'Datos incompletos'}
    nombre_jugador = None
    if validar_id_tipo:
        nv = verificar_nombre_jugador(validar_id_tipo, player_id)
        if not nv.get('ok'):
            return {'ok': False, 'error': 'ID de jugador no válido'}
        nombre_jugador = nv.get('nombre')
    try:
        r = requests.get(
            f"{FREEFIRE_BP_BASE_URL}/api/freefire-bo/levelpass-check",
            params={'playerId': player_id, 'token': FREEFIRE_BP_TOKEN},
            timeout=60,
        )
        data = r.json() if r.status_code == 200 else {}
        if not isinstance(data, dict) or not data.get('success'):
            return {'ok': False, 'error': 'Error al validar'}
        lp = (data.get('levelPasses') or {}).get(levelpass_key)
        if not isinstance(lp, dict):
            return {'ok': False, 'error': 'Error al validar'}
        result = {'ok': True, 'available': bool(lp.get('available'))}
        if nombre_jugador:
            result['nombre'] = nombre_jugador
        return result
    except Exception:
        return {'ok': False, 'error': 'Error al validar'}


# Cache en sesion para evitar revalidar el mismo pase varias veces seguidas
LEVELPASS_CACHE_TTL_SECONDS = 300


def _cache_key_levelpass(player_id, producto_id):
    return f"{player_id}:{producto_id}"


def _get_cached_levelpass(player_id, producto_id):
    cache = session.get('levelpass_cache') or {}
    item = cache.get(_cache_key_levelpass(player_id, producto_id))
    if not item:
        return None
    try:
        ts = datetime.fromisoformat(item.get('ts'))
        if datetime.utcnow() - ts > timedelta(seconds=LEVELPASS_CACHE_TTL_SECONDS):
            return None
    except Exception:
        return None
    return item


def _set_cached_levelpass(player_id, producto_id, available, nombre=''):
    cache = session.get('levelpass_cache') or {}
    cache[_cache_key_levelpass(player_id, producto_id)] = {
        'available': bool(available),
        'nombre': str(nombre or ''),
        'ts': datetime.utcnow().isoformat(),
    }
    session['levelpass_cache'] = cache


def _clear_cached_levelpass(player_id, producto_id=None):
    cache = session.get('levelpass_cache') or {}
    if producto_id is None:
        session.pop('levelpass_cache', None)
        return
    cache.pop(_cache_key_levelpass(player_id, producto_id), None)
    session['levelpass_cache'] = cache


def verificar_nombre_jugador(tipo, player_id, zone_id=''):
    """Consulta APIs externas para obtener el nombre del jugador según el tipo de juego."""
    import requests as ext_requests
    try:
        if tipo == 'freefire':
            api_url = (config.FREEFIRE_VALIDATE_API_URL or 'https://tiendagiftvenhost.com/api/game/free-fire-us').rstrip('/')
            api_key = config.FREEFIRE_VALIDATE_API_KEY or ''
            if not api_key:
                return {'ok': False, 'error': 'API key de validación Free Fire no configurada'}
            r = ext_requests.get(
                f"{api_url}?key={api_key}&uid={player_id}&zoneId=",
                timeout=60
            )
            data = r.json()
            if data.get('status') and data.get('code') == 200:
                ff_data = data.get('data', {})
                region = str(ff_data.get('region', '') or '').upper()
                username = ff_data.get('username', '')
                if region != 'US':
                    return {'ok': False, 'error': f'ID no válido: region {region or "desconocida"}'}
                if username:
                    return {'ok': True, 'nombre': username}
                return {'ok': False, 'error': 'ID no encontrado'}
            return {'ok': False, 'error': 'ID no encontrado'}

        elif tipo == 'freefire_id':
            r = ext_requests.get(
                f"https://freefire-api-six.vercel.app/get_player_personal_show?server=id&uid={player_id}",
                timeout=60
            )
            if r.status_code == 200:
                data = r.json()
                basic = data.get('basicinfo', {})
                nickname = basic.get('nickname', '')
                if nickname:
                    return {'ok': True, 'nombre': nickname}
            return {'ok': False, 'error': 'ID no encontrado en servidor Indonesia'}

        elif tipo == 'bloodstrike':
            r = ext_requests.get(
                f"https://pay.neteasegames.com/gameclub/bloodstrike/-1/login-role?roleid={player_id}&client_type=gameclub",
                timeout=10
            )
            data = r.json()
            if data.get('code') == '0000' and data.get('data', {}).get('rolename'):
                return {'ok': True, 'nombre': data['data']['rolename']}
            return {'ok': False, 'error': 'ID no encontrado'}

        elif tipo == 'mobilelegends':
            if not zone_id:
                return {'ok': False, 'error': 'Se requiere el Zone ID (Server ID)'}
            r = ext_requests.get(
                f"https://api.isan.eu.org/nickname/ml?id={player_id}&zone={zone_id}",
                timeout=10
            )
            if r.status_code != 200:
                return {'ok': False, 'error': 'Verificación de Mobile Legends no disponible temporalmente. Intenta más tarde.'}
            try:
                data = r.json()
            except Exception:
                return {'ok': False, 'error': 'Verificación de Mobile Legends no disponible temporalmente. Intenta más tarde.'}
            if data.get('success') and data.get('name'):
                return {'ok': True, 'nombre': data['name']}
            return {'ok': False, 'error': 'ID o Zone ID no encontrado'}

        else:
            return {'ok': False, 'error': f'Tipo de verificación no soportado: {tipo}'}
    except Exception as e:
        return {'ok': False, 'error': f'Error de conexión: {str(e)}'}


def restock_pines(producto_id=None):
    """Transfiere pines del producto origen (Gift Card) al producto Hype cuando el stock baja del mínimo.
    Si producto_id se especifica, solo reabastece ese producto. Si no, revisa todos."""
    db = get_db()
    if producto_id:
        productos = db.execute(
            "SELECT id, nombre, pin_origen_producto_id, stock_minimo, stock_objetivo "
            "FROM productos WHERE id = ? AND pin_origen_producto_id > 0 AND stock_minimo > 0",
            (producto_id,)
        ).fetchall()
    else:
        productos = db.execute(
            "SELECT id, nombre, pin_origen_producto_id, stock_minimo, stock_objetivo "
            "FROM productos WHERE pin_origen_producto_id > 0 AND stock_minimo > 0 AND activo = 1"
        ).fetchall()

    transferidos_total = 0
    for prod in productos:
        stock_actual = db.execute(
            "SELECT COUNT(*) as c FROM pines WHERE producto_id = ? AND estado = 'disponible'",
            (prod['id'],)
        ).fetchone()['c']

        if stock_actual < prod['stock_minimo']:
            necesarios = prod['stock_objetivo'] - stock_actual
            if necesarios <= 0:
                continue
            # Tomar pines del producto origen (Gift Card)
            pines_origen = db.execute(
                "SELECT id FROM pines WHERE producto_id = ? AND estado = 'disponible' ORDER BY fecha_agregado ASC LIMIT ?",
                (prod['pin_origen_producto_id'], necesarios)
            ).fetchall()

            for pin in pines_origen:
                db.execute("UPDATE pines SET producto_id = ? WHERE id = ?", (prod['id'], pin['id']))
                transferidos_total += 1

            if pines_origen:
                db.commit()
                print(f"[RESTOCK] {len(pines_origen)} pines transferidos a '{prod['nombre']}' (stock: {stock_actual} -> {stock_actual + len(pines_origen)})")

    db.close()
    return transferidos_total


def _pincentral_auth_retryable(status, error_msg=''):
    st = _pincentral_status_normalizado(status)
    msg = str(error_msg or '').strip().lower()
    return (
        st in {'error', 'failed', 'pending', 'procesando', 'denied'}
        or 'too many attempts' in msg
        or 'too many request' in msg
        or 'rate limit' in msg
        or 'temporarily unavailable' in msg
    )


def _pincentral_restock_cooldown_remaining(producto_id):
    now = int(time.time())
    with PINCENTRAL_RESTOCK_PRODUCT_COOLDOWN_LOCK:
        until_ts = int(PINCENTRAL_RESTOCK_PRODUCT_COOLDOWN_UNTIL.get(int(producto_id or 0), 0) or 0)
    return max(0, until_ts - now)


def _pincentral_restock_set_cooldown(producto_id, segundos):
    now = int(time.time())
    until_ts = now + max(1, int(segundos or 1))
    with PINCENTRAL_RESTOCK_PRODUCT_COOLDOWN_LOCK:
        PINCENTRAL_RESTOCK_PRODUCT_COOLDOWN_UNTIL[int(producto_id or 0)] = until_ts


def _insertar_pin_disponible(db, producto_id, pin_code, lote_id=''):
    raw = str(pin_code or '').strip()
    if not raw:
        return False, 'vacio'
    h = pin_hash(raw)
    if not h:
        return False, 'hash_invalido'
    existe = db.execute("SELECT id FROM pines WHERE pin_hash = ? LIMIT 1", (h,)).fetchone()
    if existe:
        return False, 'duplicado'
    db.execute(
        "INSERT INTO pines (producto_id, pin, pin_hash, estado, lote_id) VALUES (?, ?, ?, 'disponible', ?)",
        (int(producto_id or 0), encrypt_pin(raw), h, str(lote_id or '')),
    )
    return True, 'ok'


def _pincentral_capture_queue_upsert(producto_id, product_code, order_id, tx_id, status='', error='', payload=None, attempts=1, next_retry_ts=0):
    tx = str(tx_id or '').strip()
    if not tx:
        return
    now = int(time.time())
    retry_at = int(next_retry_ts or 0) or (now + PINCENTRAL_CAPTURE_QUEUE_INTERVAL_SECONDS)
    payload_txt = ''
    if payload is not None:
        try:
            payload_txt = json.dumps(payload, ensure_ascii=False)[:4000]
        except Exception:
            payload_txt = str(payload)[:4000]

    db = get_db()
    try:
        db.execute(
            "INSERT INTO pincentral_capture_queue (tx_id, producto_id, product_code, order_id, attempts, created_ts, updated_ts, next_retry_ts, last_status, last_error, payload) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(tx_id) DO UPDATE SET "
            "producto_id=excluded.producto_id, product_code=excluded.product_code, order_id=excluded.order_id, "
            "attempts=excluded.attempts, updated_ts=excluded.updated_ts, next_retry_ts=excluded.next_retry_ts, "
            "last_status=excluded.last_status, last_error=excluded.last_error, payload=excluded.payload",
            (
                tx,
                int(producto_id or 0) or None,
                str(product_code or '').strip(),
                str(order_id or '').strip(),
                max(1, int(attempts or 1)),
                now,
                now,
                retry_at,
                str(status or '').strip(),
                str(error or '').strip(),
                payload_txt,
            ),
        )
        db.commit()
    finally:
        db.close()


def _pincentral_capture_queue_delete(tx_id):
    tx = str(tx_id or '').strip()
    if not tx:
        return
    db = get_db()
    try:
        db.execute("DELETE FROM pincentral_capture_queue WHERE tx_id = ?", (tx,))
        db.commit()
    finally:
        db.close()


def _pincentral_limpiar_incidentes_tx(tx_id):
    tx = str(tx_id or '').strip()
    if not tx:
        return
    db = get_db()
    try:
        db.execute(
            "DELETE FROM pincentral_incidentes WHERE transaction_id = ? "
            "AND contexto IN ('restock_capture', 'restock_capture_retry')",
            (tx,),
        )
        db.commit()
    finally:
        db.close()


def _pincentral_procesar_cola_capturas(max_items=None):
    now = int(time.time())
    limit = max(1, int(max_items or PINCENTRAL_CAPTURE_QUEUE_BATCH_SIZE))
    db = get_db()
    try:
        rows = db.execute(
            "SELECT tx_id, producto_id, product_code, order_id, attempts, created_ts "
            "FROM pincentral_capture_queue WHERE next_retry_ts <= ? "
            "ORDER BY next_retry_ts ASC LIMIT ?",
            (now, limit),
        ).fetchall()
    finally:
        db.close()

    for row in rows:
        tx_id = str(row['tx_id'] or '').strip()
        if not tx_id:
            continue
        producto_id = int(row['producto_id'] or 0)
        product_code = str(row['product_code'] or '').strip()
        order_id = str(row['order_id'] or '').strip()
        attempts = int(row['attempts'] or 0)
        created_ts = int(row['created_ts'] or now)

        cap, cap_data, cap_status, pins, cap_error = _pincentral_capture_con_fallback(tx_id)

        capture_ok = bool(cap.get('ok')) and _pincentral_capturado(cap_status) and bool(pins)
        if capture_ok:
            db_ins = get_db()
            nuevos = 0
            duplicados = 0
            try:
                for p in pins:
                    if not isinstance(p, dict):
                        continue
                    key = str(p.get('key', '') or '').strip()
                    ok_insert, reason = _insertar_pin_disponible(db_ins, producto_id, key)
                    if ok_insert:
                        nuevos += 1
                    elif reason == 'duplicado':
                        duplicados += 1
                db_ins.commit()
            finally:
                db_ins.close()

            _pincentral_capture_queue_delete(tx_id)
            _pincentral_limpiar_incidentes_tx(tx_id)
            print(f"[PINCENTRAL-QUEUE] tx={tx_id} capturado. nuevos={nuevos}, duplicados={duplicados}")
            continue

        elapsed = max(0, now - created_ts)
        next_attempts = attempts + 1
        if _pincentral_capture_retryable(cap_status, cap_error) and elapsed < PINCENTRAL_CAPTURE_QUEUE_MAX_WINDOW_SECONDS and next_attempts <= PINCENTRAL_CAPTURE_QUEUE_MAX_ATTEMPTS:
            _pincentral_capture_queue_upsert(
                producto_id=producto_id,
                product_code=product_code,
                order_id=order_id,
                tx_id=tx_id,
                status=cap_data.get('status', ''),
                error=cap_error,
                payload=cap_data or cap,
                attempts=next_attempts,
                next_retry_ts=now + PINCENTRAL_CAPTURE_QUEUE_INTERVAL_SECONDS,
            )
            continue

        _pincentral_capture_queue_delete(tx_id)
        _registrar_incidente_pincentral(
            contexto='restock_capture',
            producto_id=producto_id,
            product_code=product_code,
            order_id=order_id,
            transaction_id=tx_id,
            detalle=f"Captura restock no válida (final). status={cap_data.get('status', '')}, ok={cap.get('ok')}, error={cap_error}, intentos={next_attempts}, elapsed={elapsed}s",
            payload=cap_data or cap,
        )
        if producto_id > 0:
            _pincentral_restock_set_cooldown(producto_id, PINCENTRAL_RESTOCK_INCIDENT_COOLDOWN_SECONDS)


def _pincentral_restock_deferred_capture_retry(producto_id, codigo, order_id, tx_id):
    time.sleep(max(1, int(PINCENTRAL_RESTOCK_CAPTURE_DEFER_RETRY_SECONDS or 20)))

    cap = None
    cap_data = {}
    cap_status = ''
    pins = []
    cap_error = ''
    capture_ok = False

    max_attempts = max(1, int(PINCENTRAL_RESTOCK_CAPTURE_MAX_ATTEMPTS or 1))
    for intento in range(1, max_attempts + 1):
        cap, cap_data, cap_status, pins, cap_error = _pincentral_capture_con_fallback(tx_id)

        capture_ok = bool(cap.get('ok')) and _pincentral_capturado(cap_status) and bool(pins)
        if capture_ok:
            break
        if intento < max_attempts and _pincentral_capture_retryable(cap_status, cap_error):
            time.sleep(max(1, int(PINCENTRAL_RESTOCK_CAPTURE_RETRY_DELAY_SECONDS or 2)))
            continue
        break

    if not capture_ok:
        _registrar_incidente_pincentral(
            contexto='restock_capture_retry',
            producto_id=producto_id,
            product_code=codigo,
            order_id=order_id,
            transaction_id=tx_id,
            detalle=f"Captura diferida restock no válida. status={cap_data.get('status', '')}, ok={cap.get('ok') if isinstance(cap, dict) else False}, error={cap_error}",
            payload=cap_data or cap,
        )
        _pincentral_restock_set_cooldown(producto_id, PINCENTRAL_RESTOCK_INCIDENT_COOLDOWN_SECONDS)
        return 0

    db = get_db()
    nuevos = 0
    try:
        for p in pins:
            if not isinstance(p, dict):
                continue
            key = str(p.get('key', '') or '').strip()
            if not key:
                continue
            ok_insert, _ = _insertar_pin_disponible(db, producto_id, key)
            if ok_insert:
                nuevos += 1
        db.commit()
    finally:
        db.close()

    if nuevos > 0:
        print(f"[PINCENTRAL-RESTOCK] Recuperación diferida tx={tx_id}: {nuevos} PIN(s) agregados")
    return nuevos


def restock_pincentral_almacen(producto_id):
    """Reabastece pines desde PinCentral cuando el stock local baja del mínimo."""
    from pincentral_api import autorizar_pins

    with PINCENTRAL_RESTOCK_LOCK:
        db = get_db()
        prod = db.execute(
            "SELECT id, nombre, usa_pincentral, pincentral_product_code, stock_minimo, stock_objetivo "
            "FROM productos WHERE id = ? AND activo = 1",
            (producto_id,),
        ).fetchone()

        if not prod:
            db.close()
            return 0

        usa_pincentral = int(prod['usa_pincentral'] or 0)
        codigo = str(prod['pincentral_product_code'] or '').strip()
        stock_minimo = int(prod['stock_minimo'] or 0)
        stock_objetivo = int(prod['stock_objetivo'] or 0)

        if not usa_pincentral or not codigo or stock_minimo <= 0:
            db.close()
            return 0

        cooldown_remaining = _pincentral_restock_cooldown_remaining(producto_id)
        if cooldown_remaining > 0:
            db.close()
            print(f"[PINCENTRAL-RESTOCK] Cooldown activo producto #{producto_id}: {cooldown_remaining}s")
            return 0

        stock_actual = db.execute(
            "SELECT COUNT(*) as c FROM pines WHERE producto_id = ? AND estado = 'disponible'",
            (producto_id,),
        ).fetchone()['c']

        if stock_actual >= stock_minimo:
            db.close()
            return 0

        objetivo = stock_objetivo if stock_objetivo > stock_minimo else stock_minimo
        necesarios = objetivo - stock_actual
        if necesarios <= 0:
            db.close()
            return 0

        agregados = 0
        lote_n = 0

        while necesarios > 0:
            lote_n += 1
            lote = 1
            order_id = f"RSTK_{producto_id}_{uuid.uuid4().hex[:10]}_{lote_n}"

            auth = None
            auth_data = {}
            auth_status = ''
            tx_id = ''
            auth_error = ''
            auth_ok = False

            for intento_auth in range(1, PINCENTRAL_RESTOCK_AUTH_MAX_ATTEMPTS + 1):
                auth = autorizar_pins(codigo, lote, order_id)
                auth_data = auth.get('data', {}) if isinstance(auth.get('data', {}), dict) else {}
                auth_status = _pincentral_status_normalizado(auth_data.get('status', ''))
                tx_id = str(auth_data.get('id', '') or '').strip()
                auth_error = auth.get('error') or auth_data.get('message') or ''
                auth_ok = bool(auth.get('ok')) and _pincentral_autorizado(auth_status) and bool(tx_id)
                if auth_ok:
                    break

                if intento_auth < PINCENTRAL_RESTOCK_AUTH_MAX_ATTEMPTS and _pincentral_auth_retryable(auth_status, auth_error):
                    time.sleep(PINCENTRAL_RESTOCK_AUTH_RETRY_BASE_SECONDS * intento_auth)
                    continue
                break

            if not auth_ok:
                _registrar_incidente_pincentral(
                    contexto='restock_auth',
                    producto_id=producto_id,
                    product_code=codigo,
                    order_id=order_id,
                    transaction_id=tx_id,
                    detalle=f"Autorización restock no válida. status={auth_data.get('status', '')}, ok={auth.get('ok') if isinstance(auth, dict) else False}, error={auth_error}, intentos={PINCENTRAL_RESTOCK_AUTH_MAX_ATTEMPTS}",
                    payload=auth_data or auth,
                )
                _pincentral_restock_set_cooldown(producto_id, PINCENTRAL_RESTOCK_INCIDENT_COOLDOWN_SECONDS)
                print(f"[PINCENTRAL-RESTOCK] Autorización fallida producto #{producto_id}: {auth_error or auth_data}")
                break

            cap = None
            cap_data = {}
            cap_status = ''
            pins = []
            cap_error = ''
            capture_ok = False

            max_attempts = max(1, int(PINCENTRAL_RESTOCK_CAPTURE_MAX_ATTEMPTS or 1))
            for intento in range(1, max_attempts + 1):
                cap, cap_data, cap_status, pins, cap_error = _pincentral_capture_con_fallback(tx_id)

                capture_ok = bool(cap.get('ok')) and _pincentral_capturado(cap_status) and bool(pins)
                if capture_ok:
                    break

                if intento < max_attempts and _pincentral_capture_retryable(cap_status, cap_error):
                    time.sleep(max(1, int(PINCENTRAL_RESTOCK_CAPTURE_RETRY_DELAY_SECONDS or 2)))
                    continue
                break

            if not capture_ok:
                if tx_id and _pincentral_capture_retryable(cap_status, cap_error):
                    _pincentral_capture_queue_upsert(
                        producto_id=producto_id,
                        product_code=codigo,
                        order_id=order_id,
                        tx_id=tx_id,
                        status=cap_data.get('status', ''),
                        error=cap_error,
                        payload=cap_data or cap,
                        attempts=1,
                        next_retry_ts=int(time.time()) + PINCENTRAL_CAPTURE_QUEUE_INTERVAL_SECONDS,
                    )
                    print(f"[PINCENTRAL-RESTOCK] Captura pendiente tx={tx_id}. Encolada para reintento persistente")
                else:
                    _registrar_incidente_pincentral(
                        contexto='restock_capture',
                        producto_id=producto_id,
                        product_code=codigo,
                        order_id=order_id,
                        transaction_id=tx_id,
                        detalle=f"Captura restock no válida. status={cap_data.get('status', '')}, ok={cap.get('ok')}, error={cap_error}",
                        payload=cap_data or cap,
                    )
                    _pincentral_restock_set_cooldown(producto_id, PINCENTRAL_RESTOCK_INCIDENT_COOLDOWN_SECONDS)
                print(f"[PINCENTRAL-RESTOCK] Captura fallida producto #{producto_id}: {cap.get('error') or cap_data}")
                break

            nuevos = 0
            for p in pins:
                if not isinstance(p, dict):
                    _registrar_incidente_pincentral(
                        contexto='restock',
                        producto_id=producto_id,
                        product_code=codigo,
                        order_id=order_id,
                        transaction_id=tx_id,
                        detalle='Respuesta de PinCentral con item de PIN inválido (no es objeto).',
                        payload=p,
                    )
                    continue
                key = str(p.get('key', '') or '').strip()
                serial = str(p.get('serial', '') or '').strip()
                if not key:
                    _registrar_incidente_pincentral(
                        contexto='restock',
                        producto_id=producto_id,
                        product_code=codigo,
                        order_id=order_id,
                        transaction_id=tx_id,
                        detalle='PinCentral devolvió key vacío durante restock.',
                        payload={'pin': p, 'serial': serial},
                    )
                    continue
                ok_insert, _ = _insertar_pin_disponible(db, producto_id, key)
                if ok_insert:
                    nuevos += 1

            db.commit()
            agregados += nuevos
            necesarios -= nuevos
            if nuevos == 0:
                break
            if necesarios > 0 and PINCENTRAL_RESTOCK_BETWEEN_PINS_SECONDS > 0:
                time.sleep(PINCENTRAL_RESTOCK_BETWEEN_PINS_SECONDS)

        db.close()
        if agregados > 0:
            print(f"[PINCENTRAL-RESTOCK] {agregados} PINs agregados a '{prod['nombre']}'")
        return agregados


def restock_pincentral_almacen_async(producto_id):
    """Ejecuta restock PinCentral en segundo plano para no bloquear la compra."""
    try:
        pid = int(producto_id or 0)
    except Exception:
        return
    if pid <= 0:
        return
    threading.Thread(target=restock_pincentral_almacen, args=(pid,), daemon=True).start()


def restock_jadh_almacen(producto_id):
    """Reabastece pines desde Jadh Shop cuando el stock local baja del mínimo."""
    from jadh_api import comprar_pines_jadh

    with JADH_RESTOCK_LOCK:
        db = get_db()
        prod = db.execute(
            "SELECT id, nombre, usa_jadh, jadh_item_id, jadh_diamonds, jadh_package_id, stock_minimo, stock_objetivo "
            "FROM productos WHERE id = ? AND activo = 1",
            (producto_id,),
        ).fetchone()

        if not prod:
            db.close()
            return 0

        usa_jadh = int(prod['usa_jadh'] or 0)
        item_id = str(prod['jadh_item_id'] or '').strip() or '32'
        diamonds = int(prod['jadh_diamonds'] or 0)
        package_id = str(prod['jadh_package_id'] or '').strip()
        stock_minimo = int(prod['stock_minimo'] or 0)
        stock_objetivo = int(prod['stock_objetivo'] or 0)

        if not usa_jadh or diamonds <= 0 or stock_minimo <= 0:
            db.close()
            return 0

        stock_actual = db.execute(
            "SELECT COUNT(*) as c FROM pines WHERE producto_id = ? AND estado = 'disponible'",
            (producto_id,),
        ).fetchone()['c']

        if stock_actual >= stock_minimo:
            db.close()
            return 0

        objetivo = stock_objetivo if stock_objetivo > stock_minimo else stock_minimo
        necesarios = objetivo - stock_actual
        if necesarios <= 0:
            db.close()
            return 0

        try:
            lote_id = f"JADH_{producto_id}_{uuid.uuid4().hex[:10]}"
            print(f"[JADH-RESTOCK] Comprando {necesarios} pines de {diamonds} diamantes (producto #{producto_id})...")
            pins = comprar_pines_jadh(
                diamonds=diamonds,
                quantity=necesarios,
                item_id=item_id,
                package_id=package_id or None,
            )
            agregados = 0
            for pin in pins:
                ok_insert, _ = _insertar_pin_disponible(db, producto_id, pin, lote_id=lote_id)
                if ok_insert:
                    agregados += 1
            db.commit()
            db.close()
            if agregados > 0:
                print(f"[JADH-RESTOCK] {agregados} PIN(s) agregados a '{prod['nombre']}'")
            return agregados
        except Exception as e:
            db.rollback()
            db.close()
            print(f"[JADH-RESTOCK] Error reabasteciendo producto #{producto_id}: {e}")
            return 0


def restock_jadh_almacen_async(producto_id):
    """Ejecuta restock Jadh Shop en segundo plano para no bloquear la compra."""
    try:
        pid = int(producto_id or 0)
    except Exception:
        return
    if pid <= 0:
        return
    threading.Thread(target=restock_jadh_almacen, args=(pid,), daemon=True).start()


def _pincentral_adquirir_lock_global(lock_name='pincentral_restock_scan', ttl_seg=55):
    now_ts = int(time.time())
    expira_ts = now_ts + max(10, int(ttl_seg or 55))
    owner = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"

    db = get_db()
    try:
        db.execute(
            "CREATE TABLE IF NOT EXISTS app_locks ("
            "nombre TEXT PRIMARY KEY, owner TEXT DEFAULT '', expira_ts INTEGER DEFAULT 0, actualizada TEXT DEFAULT (datetime('now','localtime'))"
            ")"
        )
        db.execute("DELETE FROM app_locks WHERE nombre = ? AND expira_ts <= ?", (lock_name, now_ts))
        db.execute(
            "INSERT INTO app_locks (nombre, owner, expira_ts) VALUES (?,?,?)",
            (lock_name, owner, expira_ts),
        )
        db.commit()
        return owner
    except sqlite3.IntegrityError:
        db.rollback()
        return False
    except Exception as e:
        db.rollback()
        print(f"[PINCENTRAL-SCAN] Error lock global: {e}")
        return False
    finally:
        db.close()


def _pincentral_liberar_lock_global(lock_name='pincentral_restock_scan', owner=None):
    if not owner:
        return
    db = get_db()
    try:
        db.execute("DELETE FROM app_locks WHERE nombre = ? AND owner = ?", (lock_name, owner))
        db.commit()
    except Exception as e:
        print(f"[PINCENTRAL-SCAN] Error liberando lock global: {e}")
    finally:
        db.close()


def restock_pincentral_productos_bajo_minimo():
    db = get_db()
    try:
        productos = db.execute(
            "SELECT id, nombre, stock_minimo FROM productos "
            "WHERE activo = 1 AND COALESCE(usa_pincentral, 0) = 1 AND COALESCE(stock_minimo, 0) > 0"
        ).fetchall()
    finally:
        db.close()

    for prod in productos:
        try:
            db2 = get_db()
            stock_actual = db2.execute(
                "SELECT COUNT(*) as c FROM pines WHERE producto_id = ? AND estado = 'disponible'",
                (prod['id'],),
            ).fetchone()['c']
            db2.close()
            if stock_actual < int(prod['stock_minimo'] or 0):
                restock_pincentral_almacen(prod['id'])
        except Exception as e:
            print(f"[PINCENTRAL-SCAN] Error revisando producto #{prod['id']}: {e}")

    # Revisar productos con auto-compra Jadh Shop
    db = get_db()
    try:
        productos_jadh = db.execute(
            "SELECT id, nombre, stock_minimo FROM productos "
            "WHERE activo = 1 AND COALESCE(usa_jadh, 0) = 1 AND COALESCE(stock_minimo, 0) > 0"
        ).fetchall()
    finally:
        db.close()

    for prod in productos_jadh:
        try:
            db2 = get_db()
            stock_actual = db2.execute(
                "SELECT COUNT(*) as c FROM pines WHERE producto_id = ? AND estado = 'disponible'",
                (prod['id'],),
            ).fetchone()['c']
            db2.close()
            if stock_actual < int(prod['stock_minimo'] or 0):
                restock_jadh_almacen(prod['id'])
        except Exception as e:
            print(f"[JADH-SCAN] Error revisando producto #{prod['id']}: {e}")


def _worker_restock_pincentral_global():
    while True:
        lock_owner = None
        try:
            lock_owner = _pincentral_adquirir_lock_global(
                lock_name='pincentral_restock_scan',
                ttl_seg=PINCENTRAL_SCAN_LOCK_TTL_SECONDS,
            )
            if lock_owner:
                _pincentral_procesar_cola_capturas()
                restock_pincentral_productos_bajo_minimo()
                _pincentral_procesar_cola_capturas()
        except Exception as e:
            print(f"[PINCENTRAL-SCAN] Worker error: {e}")
        finally:
            if lock_owner:
                _pincentral_liberar_lock_global(
                    lock_name='pincentral_restock_scan',
                    owner=lock_owner,
                )
        time.sleep(max(15, int(PINCENTRAL_SCAN_INTERVAL_SECONDS or 60)))


def iniciar_worker_restock_pincentral_global():
    global PINCENTRAL_SCAN_THREAD_STARTED
    with PINCENTRAL_SCAN_THREAD_GUARD:
        if PINCENTRAL_SCAN_THREAD_STARTED:
            return
        threading.Thread(target=_worker_restock_pincentral_global, daemon=True).start()
        PINCENTRAL_SCAN_THREAD_STARTED = True


# ===== HELPERS =====
def verificar_stock_bajo(producto_id):
    """Verifica si el stock de pines bajó del mínimo y notifica por Telegram"""
    try:
        db = get_db()
        prod = db.execute("SELECT id, nombre, stock_minimo FROM productos WHERE id = ?", (producto_id,)).fetchone()
        if prod and prod['stock_minimo'] > 0:
            stock_actual = db.execute("SELECT COUNT(*) as c FROM pines WHERE producto_id = ? AND estado = 'disponible'", (producto_id,)).fetchone()['c']
            if stock_actual <= prod['stock_minimo']:
                notificar_stock_bajo(prod['nombre'], prod['id'], stock_actual, prod['stock_minimo'])
        db.close()
    except Exception as e:
        print(f"[STOCK] Error verificando stock: {e}")


def procesar_pedido_razer_background(pedido_id, user_id, total, id_juego, paquete, cantidad):
    """Ejecuta recarga Razer en segundo plano y actualiza el pedido al estado final."""
    from razer_api import recargar_paquete

    exitosas = 0
    nickname = ''
    error_msg = ''

    try:
        db_init = get_db()
        db_init.execute("UPDATE pedidos SET estado = 'procesando' WHERE id = ?", (pedido_id,))
        db_init.commit()
        db_init.close()

        for _ in range(max(1, int(cantidad))):
            resultado_api = recargar_paquete(id_juego, paquete)
            if resultado_api.get('ok'):
                exitosas += 1
                nickname = resultado_api.get('nickname', '') or nickname
            else:
                error_msg = resultado_api.get('error', 'Proveedor Razer rechazó la recarga')
                break

        db = get_db()
        if exitosas == cantidad:
            db.execute("UPDATE pedidos SET estado = 'completado', nombre_jugador = ? WHERE id = ?", (nickname or id_juego, pedido_id))
            db.commit()
            db.close()
            enviar_webhook(user_id, {
                'evento': 'pedido_actualizado',
                'pedido_id': pedido_id,
                'estado': 'completado',
                'nombre_jugador': nickname or id_juego,
                'mensaje': f'Recarga aprobada ({exitosas}/{cantidad})'
            })
            return

        if exitosas > 0:
            monto_parcial = (total / cantidad) * (cantidad - exitosas)
            db.execute("UPDATE pedidos SET estado = 'completado', nombre_jugador = ? WHERE id = ?", (f"{nickname or id_juego} (parcial {exitosas}/{cantidad})", pedido_id))
            db.commit()
            db.close()
            recargar_saldo(user_id, monto_parcial, f"Reembolso parcial Razer: {exitosas}/{cantidad} recargas OK pedido #{pedido_id}")
            enviar_webhook(user_id, {
                'evento': 'pedido_actualizado',
                'pedido_id': pedido_id,
                'estado': 'completado',
                'nombre_jugador': nickname or id_juego,
                'reembolso_parcial': monto_parcial,
                'mensaje': f'Recarga aprobada parcial ({exitosas}/{cantidad})'
            })
            return

        db.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
        db.commit()
        db.close()
        recargar_saldo(user_id, total, f"Reembolso: Error API Razer pedido #{pedido_id}")
        enviar_webhook(user_id, {
            'evento': 'pedido_actualizado',
            'pedido_id': pedido_id,
            'estado': 'cancelado',
            'razon': 'La recarga fue rechazada',
            'reembolsado': True,
            'mensaje': 'Recarga rechazada'
        })
    except Exception as e:
        db_err = get_db()
        db_err.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
        db_err.commit()
        db_err.close()
        recargar_saldo(user_id, total, f"Reembolso: Excepción API Razer pedido #{pedido_id}")
        enviar_webhook(user_id, {
            'evento': 'pedido_actualizado',
            'pedido_id': pedido_id,
            'estado': 'cancelado',
            'razon': str(e),
            'reembolsado': True,
            'mensaje': 'Recarga rechazada por excepción'
        })
        print(f"[RAZER-BG] Pedido #{pedido_id} cancelado por excepción: {error_msg or str(e)}")


def procesar_pedido_deltaforce_background(pedido_id, user_id, total, id_juego, paquete, cantidad):
    """Ejecuta recarga Delta Force en segundo plano y actualiza el pedido al estado final."""
    from deltaforce_api import recargar_paquete

    exitosas = 0
    nickname = ''
    error_msg = ''

    try:
        db_init = get_db()
        db_init.execute("UPDATE pedidos SET estado = 'procesando' WHERE id = ?", (pedido_id,))
        db_init.commit()
        db_init.close()

        for _ in range(max(1, int(cantidad))):
            resultado_api = recargar_paquete(id_juego, paquete)
            if resultado_api.get('ok'):
                exitosas += 1
                nickname = resultado_api.get('nickname', '') or nickname
            else:
                error_msg = resultado_api.get('error', 'Proveedor Delta Force rechazó la recarga')
                break

        db = get_db()
        if exitosas == cantidad:
            db.execute("UPDATE pedidos SET estado = 'completado', nombre_jugador = ? WHERE id = ?", (nickname or id_juego, pedido_id))
            db.commit()
            db.close()
            enviar_webhook(user_id, {
                'evento': 'pedido_actualizado',
                'pedido_id': pedido_id,
                'estado': 'completado',
                'nombre_jugador': nickname or id_juego,
                'mensaje': f'Recarga aprobada ({exitosas}/{cantidad})'
            })
            return

        if exitosas > 0:
            monto_parcial = (total / cantidad) * (cantidad - exitosas)
            db.execute("UPDATE pedidos SET estado = 'completado', nombre_jugador = ? WHERE id = ?", (f"{nickname or id_juego} (parcial {exitosas}/{cantidad})", pedido_id))
            db.commit()
            db.close()
            recargar_saldo(user_id, monto_parcial, f"Reembolso parcial Delta Force: {exitosas}/{cantidad} recargas OK pedido #{pedido_id}")
            enviar_webhook(user_id, {
                'evento': 'pedido_actualizado',
                'pedido_id': pedido_id,
                'estado': 'completado',
                'nombre_jugador': nickname or id_juego,
                'reembolso_parcial': monto_parcial,
                'mensaje': f'Recarga aprobada parcial ({exitosas}/{cantidad})'
            })
            return

        db.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
        db.commit()
        db.close()
        recargar_saldo(user_id, total, f"Reembolso: Error API Delta Force pedido #{pedido_id}")
        enviar_webhook(user_id, {
            'evento': 'pedido_actualizado',
            'pedido_id': pedido_id,
            'estado': 'cancelado',
            'razon': 'La recarga fue rechazada',
            'reembolsado': True,
            'mensaje': 'Recarga rechazada'
        })
    except Exception as e:
        db_err = get_db()
        db_err.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
        db_err.commit()
        db_err.close()
        recargar_saldo(user_id, total, f"Reembolso: Excepción API Delta Force pedido #{pedido_id}")
        enviar_webhook(user_id, {
            'evento': 'pedido_actualizado',
            'pedido_id': pedido_id,
            'estado': 'cancelado',
            'razon': 'Error inesperado al procesar la recarga',
            'reembolsado': True,
            'mensaje': 'Recarga rechazada'
        })
        print(f"[DELTAFORCE-BG] Pedido #{pedido_id} cancelado por excepción: {error_msg or str(e)}")


def _formatear_pins_pincentral(pins):
    lineas = []
    for idx, pin in enumerate(pins or [], start=1):
        if not isinstance(pin, dict):
            continue
        serial = str(pin.get('serial', '') or '').strip()
        key = str(pin.get('key', '') or '').strip()
        if key and serial:
            lineas.append(f"{idx}) Serial: {serial}")
            lineas.append(f"PIN: {key}")
        elif key:
            lineas.append(f"{idx}) PIN: {key}")
    return '\n'.join(lineas)


def _pincentral_status_normalizado(status):
    return str(status or '').strip().lower().replace(' ', '')


def _pincentral_autorizado(status):
    return _pincentral_status_normalizado(status) in {'authorized', 'autorizado'}


def _pincentral_capturado(status):
    return _pincentral_status_normalizado(status) in {'captured', 'capturado'}


def _pincentral_capture_retryable(status, error_msg=''):
    st = _pincentral_status_normalizado(status)
    msg = str(error_msg or '').strip().lower()
    return (
        st in {'error', 'failed', 'pending', 'procesando', 'authorized', 'autorizado', 'created', 'retry'}
        or 'too many attempts' in msg
        or 'too many request' in msg
        or 'rate limit' in msg
    )


def _pincentral_capture_con_fallback(tx_id):
    from pincentral_api import capturar_pins, consultar_pedido_pin

    tx = str(tx_id or '').strip()
    cap = capturar_pins(tx)
    cap_data = cap.get('data', {}) if isinstance(cap.get('data', {}), dict) else {}
    cap_status = _pincentral_status_normalizado(cap_data.get('status', ''))
    pins = cap_data.get('pins', []) if isinstance(cap_data.get('pins', []), list) else []
    cap_error = cap.get('error') or cap_data.get('message') or ''

    capture_ok = bool(cap.get('ok')) and _pincentral_capturado(cap_status) and bool(pins)
    if capture_ok:
        return cap, cap_data, cap_status, pins, cap_error

    pedido = consultar_pedido_pin(tx)
    pedido_data = pedido.get('data', {}) if isinstance(pedido.get('data', {}), dict) else {}
    if pedido_data or pedido.get('ok'):
        cap = pedido
        cap_data = pedido_data
        cap_status = _pincentral_status_normalizado(cap_data.get('status', ''))
        pins = cap_data.get('pins', []) if isinstance(cap_data.get('pins', []), list) else []
        pedido_error = pedido.get('error') or cap_data.get('message') or ''
        if pedido_error:
            cap_error = pedido_error

    return cap, cap_data, cap_status, pins, cap_error


def _registrar_incidente_pincentral(
    contexto,
    detalle,
    payload=None,
    pedido_id=None,
    producto_id=None,
    product_code='',
    order_id='',
    transaction_id='',
):
    detalle = str(detalle or '').strip() or 'Incidente PinCentral'
    payload_txt = ''
    if payload is not None:
        try:
            payload_txt = json.dumps(payload, ensure_ascii=False)[:4000]
        except Exception:
            payload_txt = str(payload)[:4000]

    db = get_db()
    try:
        db.execute(
            "INSERT INTO pincentral_incidentes (contexto, pedido_id, producto_id, product_code, order_id, transaction_id, detalle, payload) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                str(contexto or '').strip() or 'general',
                pedido_id,
                producto_id,
                str(product_code or '').strip(),
                str(order_id or '').strip(),
                str(transaction_id or '').strip(),
                detalle,
                payload_txt,
            ),
        )
        db.commit()
    except Exception as e:
        print(f"[PINCENTRAL-INCIDENTE] Error guardando incidente: {e}")
    finally:
        db.close()

    msg = (
        "⚠️ <b>Incidente PinCentral</b>\n"
        f"Contexto: <b>{contexto}</b>\n"
        f"Detalle: {detalle}\n"
        f"Pedido: {pedido_id or '-'} | Producto: {producto_id or '-'}\n"
        f"Código: <code>{product_code or '-'}</code>\n"
        f"Order ID: <code>{order_id or '-'}</code>\n"
        f"Tx ID: <code>{transaction_id or '-'}</code>"
    )
    enviar_telegram(msg)


def _pincentral_detectar_key_vacia(
    pins,
    contexto,
    pedido_id=None,
    producto_id=None,
    product_code='',
    order_id='',
    transaction_id='',
):
    errores = []
    for idx, pin in enumerate(pins or [], start=1):
        if not isinstance(pin, dict):
            detalle = f"Item PIN #{idx} inválido (no es objeto)."
            _registrar_incidente_pincentral(
                contexto=contexto,
                pedido_id=pedido_id,
                producto_id=producto_id,
                product_code=product_code,
                order_id=order_id,
                transaction_id=transaction_id,
                detalle=detalle,
                payload=pin,
            )
            errores.append(detalle)
            continue
        key = str(pin.get('key', '') or '').strip()
        if not key:
            detalle = f"Item PIN #{idx} con key vacío."
            _registrar_incidente_pincentral(
                contexto=contexto,
                pedido_id=pedido_id,
                producto_id=producto_id,
                product_code=product_code,
                order_id=order_id,
                transaction_id=transaction_id,
                detalle=detalle,
                payload=pin,
            )
            errores.append(detalle)
    return errores


def _registrar_auditoria_recarga(
    pedido_id,
    usuario_id,
    producto_id,
    proveedor,
    etapa='',
    estado='',
    detalle='',
    referencia='',
    payload=None,
):
    payload_txt = ''
    if payload is not None:
        try:
            payload_txt = json.dumps(payload, ensure_ascii=False)[:4000]
        except Exception:
            payload_txt = str(payload)[:4000]

    db = get_db()
    try:
        db.execute(
            "INSERT INTO recargas_auditoria (pedido_id, usuario_id, producto_id, proveedor, etapa, estado, detalle, referencia, payload) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                int(pedido_id or 0) or None,
                int(usuario_id or 0) or None,
                int(producto_id or 0) or None,
                str(proveedor or '').strip() or 'desconocido',
                str(etapa or '').strip(),
                str(estado or '').strip(),
                str(detalle or '').strip(),
                str(referencia or '').strip(),
                payload_txt,
            ),
        )
        db.commit()
    except Exception as e:
        print(f"[RECARGA-AUDITORIA] Error guardando auditoría: {e}")
    finally:
        db.close()


def _marcar_pin_error_hype(pin_id, pedido_id, id_juego, pin_code, motivo=''):
    db = get_db()
    try:
        db.execute("UPDATE pines SET estado = 'error' WHERE id = ?", (pin_id,))
        db.commit()
    finally:
        db.close()

    enviar_telegram(
        "⚠️ <b>PIN con error (Hype)</b>\n"
        f"Pedido: <b>#{pedido_id}</b>\n"
        f"ID Juego: <code>{id_juego or '-'}</code>\n"
        f"PIN: <code>{pin_code or '-'}</code>\n"
        f"Motivo: {motivo or 'Error del proveedor'}"
    )


def _reservar_pin_reemplazo_hype(producto_id, pedido_id, user_id):
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT id, pin FROM pines WHERE producto_id = ? AND estado = 'disponible' ORDER BY fecha_agregado ASC LIMIT 1",
            (producto_id,),
        ).fetchone()
        if not row:
            db.commit()
            return None
        db.execute(
            "UPDATE pines SET estado = 'usado', usado_por = ?, pedido_id = ?, fecha_usado = datetime('now','localtime') WHERE id = ?",
            (user_id, pedido_id, row['id']),
        )
        db.commit()
        return {'id': row['id'], 'pin_code': decrypt_pin(row['pin'])}
    finally:
        db.close()


def _pincentral_estado_recarga(data):
    return str((data or {}).get('status', '') or '').strip().lower().replace(' ', '')


def _parse_campos_cliente(raw, ensure_id_juego=False):
    fields = []
    allowed = {'id_juego', 'input2', 'additional_data_2', 'zone_id'}
    for line in str(raw or '').replace('\r', '\n').split('\n'):
        line = line.strip()
        if not line:
            continue
        if ':' in line:
            name, label = line.split(':', 1)
        else:
            name, label = line, line
        name = name.strip()
        label = label.strip() or name
        if name in allowed:
            fields.append({'name': name, 'label': label})
    if ensure_id_juego and not any(f['name'] == 'id_juego' for f in fields):
        fields.insert(0, {'name': 'id_juego', 'label': 'ID del jugador'})
    return fields


def _pincentral_parse_fields(raw):
    fields = [f for f in _parse_campos_cliente(raw, ensure_id_juego=True) if f['name'] in ('id_juego', 'input2', 'additional_data_2')]
    if not fields:
        fields = [{'name': 'id_juego', 'label': 'ID del jugador'}]
    if not any(f['name'] == 'id_juego' for f in fields):
        fields.insert(0, {'name': 'id_juego', 'label': 'ID del jugador'})
    return fields


def procesar_pedido_pincentral_background(pedido_id, user_id, total, product_code, cantidad):
    """Ejecuta autorización + captura de PINs PinCentral en segundo plano."""
    from pincentral_api import autorizar_pins, capturar_pins

    db_init = get_db()
    db_init.execute("UPDATE pedidos SET estado = 'procesando' WHERE id = ?", (pedido_id,))
    user = db_init.execute("SELECT nombre, email FROM usuarios WHERE id = ?", (user_id,)).fetchone()
    db_init.commit()
    db_init.close()

    client_name = (user['nombre'] if user else '') or ''
    client_email = (user['email'] if user else '') or ''
    order_id = f"PC{pedido_id}"

    db_prod = get_db()
    prod_row = db_prod.execute("SELECT producto_id FROM pedidos WHERE id = ?", (pedido_id,)).fetchone()
    db_prod.close()
    producto_id = int(prod_row['producto_id']) if prod_row else None

    try:
        auth = autorizar_pins(product_code, int(cantidad), order_id, client_name=client_name, client_email=client_email)
        auth_data = auth.get('data', {}) if isinstance(auth.get('data', {}), dict) else {}
        auth_status = _pincentral_status_normalizado(auth_data.get('status', ''))
        tx_id = str(auth_data.get('id', '') or '').strip()

        if (not auth.get('ok')) or (not _pincentral_autorizado(auth_status)) or not tx_id:
            error_msg = auth.get('error') or auth_data.get('message') or f"Estado autorización: {auth_data.get('status', 'desconocido')}"
            db_err = get_db()
            db_err.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
            db_err.commit()
            db_err.close()
            recargar_saldo(user_id, total, f"Reembolso: Error API PinCentral pedido #{pedido_id}")
            enviar_webhook(user_id, {
                'evento': 'pedido_actualizado',
                'pedido_id': pedido_id,
                'estado': 'cancelado',
                'razon': 'La solicitud fue rechazada',
                'reembolsado': True,
                'mensaje': 'Pedido rechazado'
            })
            return

        cap = capturar_pins(tx_id)
        cap_data = cap.get('data', {}) if isinstance(cap.get('data', {}), dict) else {}
        cap_status = _pincentral_status_normalizado(cap_data.get('status', ''))
        pins = cap_data.get('pins', []) if isinstance(cap_data.get('pins', []), list) else []
        errores_key = _pincentral_detectar_key_vacia(
            pins,
            contexto='pedido',
            pedido_id=pedido_id,
            producto_id=producto_id,
            product_code=product_code,
            order_id=order_id,
            transaction_id=tx_id,
        )
        codigos = _formatear_pins_pincentral(pins)

        if (not cap.get('ok')) or (not _pincentral_capturado(cap_status)) or not codigos or errores_key:
            error_msg = cap.get('error') or cap_data.get('message') or f"Estado captura: {cap_data.get('status', 'desconocido')}"
            if errores_key:
                error_msg = f"PinCentral devolvió key vacío: {'; '.join(errores_key)}"
            db_err = get_db()
            db_err.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
            db_err.commit()
            db_err.close()
            recargar_saldo(user_id, total, f"Reembolso: Error captura PinCentral pedido #{pedido_id}")
            enviar_webhook(user_id, {
                'evento': 'pedido_actualizado',
                'pedido_id': pedido_id,
                'estado': 'cancelado',
                'razon': 'No fue posible completar la entrega',
                'reembolsado': True,
                'mensaje': 'Pedido rechazado'
            })
            return

        db_ok = get_db()
        db_ok.execute(
            "UPDATE pedidos SET estado = 'completado', codigo_entregado = ?, referencia_externa = ? WHERE id = ?",
            (codigos, tx_id, pedido_id),
        )
        db_ok.commit()
        db_ok.close()
        cantidad_codigos = len([
            p for p in pins
            if isinstance(p, dict) and str(p.get('key', '') or '').strip()
        ])
        enviar_webhook(user_id, {
            'evento': 'pedido_actualizado',
            'pedido_id': pedido_id,
            'estado': 'completado',
            'referencia': tx_id,
            'cantidad_codigos': cantidad_codigos,
            'mensaje': 'Códigos entregados'
        })
    except Exception as e:
        db_err = get_db()
        db_err.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
        db_err.commit()
        db_err.close()
        recargar_saldo(user_id, total, f"Reembolso: Excepción API PinCentral pedido #{pedido_id}")
        enviar_webhook(user_id, {
            'evento': 'pedido_actualizado',
            'pedido_id': pedido_id,
            'estado': 'cancelado',
            'razon': 'Error inesperado al procesar el pedido',
            'reembolsado': True,
            'mensaje': 'Pedido rechazado'
        })


def procesar_pedido_bloodstrike_background(pedido_id, user_id, total, id_juego, package_id):
    from bloodstrike_api import FREEFIRE_PACKAGES, FREEFIRE_PROMO_PACKAGES, consultar_estado as consultar_estado_bloodstrike, recargar as recargar_bloodstrike

    package_key = str(package_id or '').strip()
    if package_key in {p['id'] for p in FREEFIRE_PROMO_PACKAGES}:
        game_id = 'freefire_pinxtore'
    elif package_key in {p['id'] for p in FREEFIRE_PACKAGES}:
        game_id = 'freefire'
    else:
        game_id = 'bloodstrike'
    ref = f"BS{pedido_id}" if game_id == 'bloodstrike' else f"FF{pedido_id}"
    db_init = get_db()
    db_init.execute("UPDATE pedidos SET estado = 'procesando', referencia_externa = ? WHERE id = ?", (ref, pedido_id))
    db_init.commit()
    db_init.close()

    try:
        resultado_api = recargar_bloodstrike(id_juego, package_id, game_id=game_id)
        db2 = get_db()
        if resultado_api.get('ok'):
            db2.execute("UPDATE pedidos SET estado = 'completado', nombre_jugador = ?, referencia_externa = ? WHERE id = ?", (id_juego, ref, pedido_id))
            db2.commit()
            db2.close()
            enviar_webhook(user_id, {
                'evento': 'pedido_actualizado',
                'pedido_id': pedido_id,
                'estado': 'completado',
                'referencia': ref,
                'mensaje': resultado_api.get('message', 'Recarga completada'),
                'tiempo_respuesta': resultado_api.get('elapsed_seconds'),
            })
            return
        if resultado_api.get('pending'):
            provider_id = resultado_api.get('provider_id')
            if provider_id:
                ref = provider_id
                db2.execute("UPDATE pedidos SET estado = 'procesando', referencia_externa = ? WHERE id = ?", (ref, pedido_id))
                db2.commit()
                db2.close()
                while True:
                    time.sleep(15)
                    estado_api = consultar_estado_bloodstrike(provider_id)
                    if estado_api.get('ok') and estado_api.get('final'):
                        db_ok = get_db()
                        db_ok.execute("UPDATE pedidos SET estado = 'completado', nombre_jugador = ?, referencia_externa = ? WHERE id = ?", (id_juego, ref, pedido_id))
                        db_ok.commit()
                        db_ok.close()
                        enviar_webhook(user_id, {
                            'evento': 'pedido_actualizado',
                            'pedido_id': pedido_id,
                            'estado': 'completado',
                            'referencia': ref,
                            'mensaje': 'Recarga completada',
                            'tiempo_respuesta': estado_api.get('elapsed_seconds'),
                        })
                        return
                    if estado_api.get('final'):
                        db_fail = get_db()
                        db_fail.execute("UPDATE pedidos SET estado = 'cancelado', referencia_externa = ? WHERE id = ?", (ref, pedido_id))
                        db_fail.commit()
                        db_fail.close()
                        recargar_saldo(user_id, total, f"Reembolso: Recarga rechazada pedido #{pedido_id}")
                        enviar_webhook(user_id, {
                            'evento': 'pedido_actualizado',
                            'pedido_id': pedido_id,
                            'estado': 'cancelado',
                            'referencia': ref,
                            'razon': 'La recarga fue rechazada',
                            'reembolsado': True,
                            'mensaje': 'Recarga rechazada',
                            'tiempo_respuesta': estado_api.get('elapsed_seconds'),
                        })
                        return
            db2.execute("UPDATE pedidos SET estado = 'procesando', referencia_externa = ? WHERE id = ?", (ref, pedido_id))
            db2.commit()
            db2.close()
            enviar_webhook(user_id, {
                'evento': 'pedido_actualizado',
                'pedido_id': pedido_id,
                'estado': 'procesando',
                'referencia': ref,
                'mensaje': resultado_api.get('message', 'Recarga en proceso'),
                'tiempo_respuesta': resultado_api.get('elapsed_seconds'),
            })
            return
        db2.execute("UPDATE pedidos SET estado = 'cancelado', referencia_externa = ? WHERE id = ?", (ref, pedido_id))
        db2.commit()
        db2.close()
        recargar_saldo(user_id, total, f"Reembolso: Error API recarga pedido #{pedido_id}")
        enviar_webhook(user_id, {
            'evento': 'pedido_actualizado',
            'pedido_id': pedido_id,
            'estado': 'cancelado',
            'referencia': ref,
            'razon': 'La recarga fue rechazada',
            'reembolsado': True,
            'mensaje': 'Recarga rechazada',
            'tiempo_respuesta': resultado_api.get('elapsed_seconds'),
        })
    except Exception as e:
        db2 = get_db()
        db2.execute("UPDATE pedidos SET estado = 'cancelado', referencia_externa = ? WHERE id = ?", (ref, pedido_id))
        db2.commit()
        db2.close()
        recargar_saldo(user_id, total, f"Reembolso: Excepción API recarga pedido #{pedido_id}")
        enviar_webhook(user_id, {
            'evento': 'pedido_actualizado',
            'pedido_id': pedido_id,
            'estado': 'cancelado',
            'referencia': ref,
            'razon': 'Error inesperado al procesar la recarga',
            'reembolsado': True,
            'mensaje': 'Recarga rechazada',
        })


# ===== DECORADORES =====
# Endpoints permitidos para usuarios logueados pero con email no verificado
_EMAIL_VERIFICACION_EXCEPT_ENDPOINTS = {
    'actualizar_datos', 'verificar_email', 'reenviar_verificacion', 'logout',
    'static', 'login', 'registro', 'recuperar', 'restablecer',
}

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Inicia sesión para continuar', 'error')
            return redirect(url_for('login'))
        user = get_user_by_id(session['user_id'])
        if not user:
            session.clear()
            return redirect(url_for('login'))
        if request.endpoint not in _EMAIL_VERIFICACION_EXCEPT_ENDPOINTS:
            if not int((user['email_verificado'] if 'email_verificado' in user.keys() else 1) or 1):
                flash('Debes verificar tu correo electrónico para continuar.', 'error')
                return redirect(url_for('actualizar_datos'))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        user = get_user_by_id(session['user_id'])
        if not user or user['rol'] != 'admin':
            flash('Acceso denegado', 'error')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated


def api_key_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        api_key = request.headers.get('X-API-Key', '').strip()

        # Compatibilidad: algunos clientes usan Authorization: Bearer <API_KEY>
        if not api_key:
            auth_header = request.headers.get('Authorization', '').strip()
            if auth_header.lower().startswith('bearer '):
                api_key = auth_header[7:].strip()

        # Compatibilidad temporal para integraciones legadas
        if not api_key:
            api_key = (request.args.get('api_key') or '').strip()
        if not api_key and request.method in ('POST', 'PUT', 'PATCH'):
            body = request.get_json(silent=True) or {}
            api_key = (body.get('api_key') or '').strip()

        if not api_key:
            return jsonify({'error': 'API key requerida en header X-API-Key'}), 401
        user = get_user_by_api_key(api_key)
        if not user:
            return jsonify({'error': 'API key inválida'}), 401
        request.api_user = user
        return f(*args, **kwargs)
    return decorated


@app.context_processor
def inject_globals():
    user = None
    saldo = 0
    if 'user_id' in session:
        user = get_user_by_id(session['user_id'])
        if user:
            saldo = get_saldo(user['id'])
    return dict(current_user=user, saldo=saldo, tienda_nombre=config.TIENDA_NOMBRE)


# ===== AUTH =====
@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("5 per minute", methods=["POST"])
def login():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        user = get_user_by_email(email)
        if user and check_password_hash(user['password'], password):
            email_verif = int((user['email_verificado'] if 'email_verificado' in user.keys() else 0) or 0)
            aprobado = int(user['aprobado']) if 'aprobado' in user.keys() and user['aprobado'] is not None else 1
            if not aprobado:
                if not email_verif:
                    flash('Tu cuenta aún no está verificada. Revisa tu correo e ingresa el código. Una vez verificada, comunícate con soporte al WhatsApp +573169183784 para la aprobación.', 'popup')
                else:
                    flash('Tu cuenta está en revisión. Comunícate con soporte al WhatsApp +573169183784 para la aprobación de tu usuario.', 'popup')
                return render_template('login.html')
            if not user['activo']:
                flash('Tu cuenta está desactivada.', 'error')
                return render_template('login.html')
            session['user_id'] = user['id']
            session['user_nombre'] = user['nombre']
            session['user_rol'] = user['rol']
            db = get_db()
            db.execute("UPDATE usuarios SET ultimo_login = datetime('now','localtime') WHERE id = ?", (user['id'],))
            db.commit()
            db.close()
            if not email_verif:
                flash('Por seguridad debes verificar tu correo electrónico. Actualiza tus datos y solicita el código.', 'error')
                return redirect(url_for('actualizar_datos'))
            flash(f'Bienvenido, {user["nombre"]}!', 'success')
            return redirect(url_for('dashboard'))
        flash('Email o contraseña incorrectos', 'error')
    return render_template('login.html')


@app.route('/registro', methods=['GET', 'POST'])
def registro():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        nombre = request.form.get('nombre', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        telefono = request.form.get('telefono', '').strip()
        if not nombre or not email or not password or not telefono:
            flash('Todos los campos son obligatorios, incluyendo el teléfono', 'error')
            return render_template('registro.html')
        if len(password) < 6:
            flash('La contraseña debe tener al menos 6 caracteres', 'error')
            return render_template('registro.html')
        user = create_user(nombre, email, password, telefono)
        if not user:
            flash('El email ya está registrado', 'error')
            return render_template('registro.html')
        ok, err = _enviar_verificacion_email(user['id'], user['email'], user['nombre'])
        if not ok:
            flash(f'Cuenta creada, pero no pudimos enviar el correo de verificación: {err}. Intenta reenviar el código.', 'error')
        else:
            flash('Te hemos enviado un código de verificación a tu correo. Ingrésalo para activar tu cuenta.', 'success')
        return redirect(url_for('verificar_email', email=user['email']))
    return render_template('registro.html')


@app.route('/verificar-email', methods=['GET', 'POST'])
def verificar_email():
    if 'user_id' in session:
        user = get_user_by_id(session['user_id'])
        if user and int((user['email_verificado'] if 'email_verificado' in user.keys() else 0) or 0):
            return redirect(url_for('dashboard'))
    email = request.args.get('email', request.form.get('email', '')).strip()
    if request.method == 'POST':
        codigo = request.form.get('codigo', '').strip()
        db = get_db()
        user = db.execute("SELECT * FROM usuarios WHERE email = ?", (email,)).fetchone() if email else None
        if not user:
            db.close()
            flash('Correo no encontrado.', 'error')
            return redirect(url_for('login'))
        row = db.execute(
            "SELECT * FROM usuario_tokens WHERE usuario_id = ? AND token = ? AND tipo = 'verificacion_email' AND usado = 0 ORDER BY id DESC LIMIT 1",
            (user['id'], codigo)
        ).fetchone()
        if not row:
            db.close()
            flash('Código incorrecto.', 'error')
            return redirect(url_for('verificar_email', email=email))
        exp = _parse_local_datetime(row['expiracion'])
        if not exp or exp < datetime.now():
            db.close()
            flash('El código ha expirado. Solicita uno nuevo.', 'error')
            return redirect(url_for('verificar_email', email=email))
        db.execute("UPDATE usuarios SET email_verificado = 1 WHERE id = ?", (user['id'],))
        db.execute("UPDATE usuario_tokens SET usado = 1 WHERE id = ?", (row['id'],))
        db.commit()
        aprobado = int(user['aprobado']) if 'aprobado' in user.keys() and user['aprobado'] is not None else 1
        db.close()
        if not aprobado:
            flash('Tu cuenta está en revisión. Comunícate con soporte al WhatsApp +573169183784 para la aprobación de tu usuario.', 'popup')
            return redirect(url_for('login'))
        flash('Correo verificado correctamente.', 'success')
        if 'user_id' in session:
            return redirect(url_for('dashboard'))
        return redirect(url_for('login'))
    return render_template('verificar_email.html', email=email)


@app.route('/esperando-aprobacion')
def esperando_aprobacion():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    email = request.args.get('email', '').strip()
    return render_template('esperando_aprobacion.html', email=email, whatsapp='+573169183784')


@app.route('/reenviar-verificacion', methods=['POST'])
@limiter.limit("3 per minute", methods=["POST"])
def reenviar_verificacion():
    email = request.form.get('email', '').strip()
    if not email and 'user_id' in session:
        user = get_user_by_id(session['user_id'])
        if user:
            email = user['email']
    user = get_user_by_email(email)
    if not user:
        flash('Correo no encontrado.', 'error')
        if 'user_id' in session:
            return redirect(url_for('actualizar_datos'))
        return redirect(url_for('registro'))
    if int((user['email_verificado'] if 'email_verificado' in user.keys() else 0) or 0):
        flash('Este correo ya está verificado.', 'info')
        return redirect(url_for('dashboard' if 'user_id' in session else 'login'))
    ok, err = _enviar_verificacion_email(user['id'], user['email'], user['nombre'])
    if ok:
        flash('Código reenviado. Revisa tu correo.', 'success')
    else:
        flash(f'No se pudo reenviar el código: {err}', 'error')
    return redirect(url_for('verificar_email', email=email))


@app.route('/actualizar-datos', methods=['GET', 'POST'])
@login_required
def actualizar_datos():
    user = get_user_by_id(session['user_id'])
    if not user:
        session.clear()
        return redirect(url_for('login'))
    if int((user['email_verificado'] if 'email_verificado' in user.keys() else 0) or 0):
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        nombre = request.form.get('nombre', '').strip()
        email = request.form.get('email', '').strip()
        telefono = request.form.get('telefono', '').strip()
        if not nombre or not email:
            flash('Nombre y correo son obligatorios.', 'error')
            return render_template('actualizar_datos.html', user=user)
        if '@' not in email or '.' not in email.split('@')[-1]:
            flash('Ingresa un correo válido.', 'error')
            return render_template('actualizar_datos.html', user=user)
        db = get_db()
        existing = db.execute("SELECT id FROM usuarios WHERE email = ? AND id != ?", (email, user['id'])).fetchone()
        if existing:
            db.close()
            flash('Ese correo ya está registrado por otro usuario.', 'error')
            return render_template('actualizar_datos.html', user=user)
        db.execute(
            "UPDATE usuarios SET nombre = ?, email = ?, telefono = ?, email_verificado = 0 WHERE id = ?",
            (nombre, email, telefono, user['id'])
        )
        db.commit()
        db.close()
        session['user_nombre'] = nombre
        ok, err = _enviar_verificacion_email(user['id'], email, nombre)
        if ok:
            flash('Datos actualizados. Te enviamos un código de verificación.', 'success')
        else:
            flash(f'Datos actualizados, pero no se pudo enviar el código: {err}', 'error')
        return redirect(url_for('verificar_email', email=email))
    return render_template('actualizar_datos.html', user=user)


@app.route('/recuperar', methods=['GET', 'POST'])
def recuperar():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        user = get_user_by_email(email)
        if user:
            ok, err = _enviar_recuperacion_email(user['id'], user['email'], user['nombre'])
            if not ok:
                flash(f'No pudimos enviar el correo: {err}', 'error')
                return redirect(url_for('recuperar'))
        flash('Si el correo está registrado, recibirás las instrucciones para restablecer tu contraseña.', 'success')
        return redirect(url_for('login'))
    return render_template('recuperar.html')


@app.route('/restablecer', methods=['GET', 'POST'])
def restablecer():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    token = request.args.get('token', request.form.get('token', '')).strip()
    row = _validar_token_usuario(token, 'reset_password')
    if not row:
        flash('El enlace de recuperación es inválido o ha expirado.', 'error')
        return redirect(url_for('login'))
    db = get_db()
    user = db.execute("SELECT * FROM usuarios WHERE id = ?", (row['usuario_id'],)).fetchone()
    if not user:
        db.close()
        flash('Usuario no encontrado.', 'error')
        return redirect(url_for('login'))
    if request.method == 'POST':
        password = request.form.get('password', '')
        if len(password) < 6:
            flash('La contraseña debe tener al menos 6 caracteres.', 'error')
            return redirect(url_for('restablecer', token=token))
        db.execute("UPDATE usuarios SET password = ? WHERE id = ?", (generate_password_hash(password), user['id']))
        db.execute("UPDATE usuario_tokens SET usado = 1 WHERE id = ?", (row['id'],))
        db.commit()
        db.close()
        flash('Contraseña actualizada. Inicia sesión con tu nueva contraseña.', 'success')
        return redirect(url_for('login'))
    db.close()
    return render_template('restablecer.html', token=token)


@app.route('/logout')
def logout():
    session.clear()
    flash('Sesión cerrada', 'success')
    return redirect(url_for('login'))


# ===== VERIFICAR NOMBRE JUGADOR =====
@app.route('/api/verificar-nombre', methods=['POST'])
@login_required
def api_verificar_nombre():
    data = request.get_json() or {}
    producto_id = data.get('producto_id', 0)
    player_id = str(data.get('player_id', '')).strip()
    zone_id = str(data.get('zone_id', '')).strip()
    if not producto_id or not player_id:
        return jsonify({'ok': False, 'error': 'Faltan parámetros'})
    db = get_db()
    prod = db.execute(
        "SELECT c.verificar_nombre, c.verificar_nombre_tipo FROM productos p "
        "JOIN categorias c ON p.categoria_id = c.id WHERE p.id = ?", (producto_id,)
    ).fetchone()
    db.close()
    if not prod or not prod['verificar_nombre']:
        return jsonify({'ok': False, 'error': 'Este producto no requiere verificación'})
    resultado = verificar_nombre_jugador(prod['verificar_nombre_tipo'], player_id, zone_id)
    return jsonify(resultado)


# ===== VERIFICAR PASE DE NIVEL FREE FIRE =====
@app.route('/api/verificar-levelpass', methods=['POST'])
@login_required
def api_verificar_levelpass():
    data = request.get_json(silent=True) or {}
    producto_id = int(data.get('producto_id', 0) or 0)
    player_id = str(data.get('player_id', '') or '').strip()
    zone_id = str(data.get('zone_id', '') or '').strip()
    if not producto_id or not player_id:
        return jsonify({'ok': False, 'error': 'Producto y player ID requeridos'}), 400
    db = get_db()
    prod = db.execute(
        "SELECT p.freefire_levelpass, c.verificar_nombre, c.verificar_nombre_tipo "
        "FROM productos p JOIN categorias c ON p.categoria_id = c.id "
        "WHERE p.id = ? AND p.activo = 1", (producto_id,)
    ).fetchone()
    db.close()
    if not prod:
        return jsonify({'ok': False, 'error': 'Producto no encontrado'}), 404
    levelpass_key = str(prod['freefire_levelpass'] or '').strip()
    if not levelpass_key:
        return jsonify({'ok': False, 'error': 'Producto sin pase configurado'}), 400
    tipo = str(prod['verificar_nombre_tipo'] or 'freefire').strip() or 'freefire'
    resultado = _verificar_freefire_levelpass(player_id, levelpass_key, validar_id_tipo=tipo)
    _set_cached_levelpass(player_id, producto_id, resultado.get('available'), resultado.get('nombre'))
    return jsonify(resultado)


@app.route('/api/verificar-pases', methods=['POST'])
@login_required
def api_verificar_pases():
    """Verifica el ID del jugador y retorna la disponibilidad de todos los pases de nivel configurados."""
    data = request.get_json(silent=True) or {}
    player_id = str(data.get('player_id', '') or '').strip()
    zone_id = str(data.get('zone_id', '') or '').strip()
    if not player_id:
        return jsonify({'ok': False, 'error': 'Ingresa el ID del jugador'}), 400

    # 1. Validar que el ID existe antes de consultar pases
    nv = verificar_nombre_jugador('freefire', player_id, zone_id)
    if not nv.get('ok'):
        return jsonify({'ok': False, 'error': 'ID de jugador no válido'}), 400

    # 2. Consultar disponibilidad de todos los pases en una sola llamada
    try:
        r = requests.get(
            f"{FREEFIRE_BP_BASE_URL}/api/freefire-bo/levelpass-check",
            params={'playerId': player_id, 'token': FREEFIRE_BP_TOKEN},
            timeout=60,
        )
        api_data = r.json() if r.status_code == 200 else {}
    except Exception:
        return jsonify({'ok': False, 'error': 'Error al validar'}), 400

    if not isinstance(api_data, dict) or not api_data.get('success'):
        return jsonify({'ok': False, 'error': 'Error al validar'}), 400

    level_passes = api_data.get('levelPasses') or {}

    db = get_db()
    user_row = db.execute("SELECT suscripcion_hasta FROM usuarios WHERE id = ?", (session['user_id'],)).fetchone()
    prods = db.execute(
        "SELECT p.* FROM productos p "
        "WHERE p.activo = 1 AND COALESCE(p.freefire_levelpass, '') != '' "
        "ORDER BY p.orden ASC, p.precio ASC, p.nombre ASC"
    ).fetchall()

    pases = []
    for prod in prods:
        key = str(prod['freefire_levelpass'] or '').strip()
        lp = level_passes.get(key) if isinstance(level_passes, dict) else None
        disponible = isinstance(lp, dict) and bool(lp.get('available'))
        precio = _precio_producto_para_usuario(prod, user_row)
        pases.append({
            'producto_id': prod['id'],
            'nombre': prod['nombre'],
            'descripcion': prod['descripcion'] or '',
            'icono': prod['icono'] or 'fa-medal',
            'freefire_levelpass': key,
            'precio': precio,
            'disponible': disponible,
            'nombre_pase': lp.get('name', key) if isinstance(lp, dict) else key,
        })
    db.close()

    # Cachear resultados para evitar revalidar al comprar
    for p in pases:
        _set_cached_levelpass(player_id, p['producto_id'], p['disponible'], nv.get('nombre'))

    return jsonify({
        'ok': True,
        'player_id': player_id,
        'player_name': nv.get('nombre'),
        'alguno_disponible': any(p['disponible'] for p in pases),
        'pases': pases,
    })


@app.route('/pases-de-nivel')
@login_required
def pases_de_nivel():
    db = get_db()
    user_row = db.execute("SELECT suscripcion_hasta FROM usuarios WHERE id = ?", (session['user_id'],)).fetchone()
    prods = db.execute(
        "SELECT p.* FROM productos p "
        "WHERE p.activo = 1 AND COALESCE(p.freefire_levelpass, '') != '' "
        "ORDER BY p.orden ASC, p.precio ASC, p.nombre ASC"
    ).fetchall()
    productos = []
    for prod in prods:
        d = dict(prod)
        d['precio'] = _precio_producto_para_usuario(prod, user_row)
        productos.append(d)
    db.close()
    return render_template('pases_de_nivel.html', productos=productos)


# ===== DASHBOARD =====
@app.route('/dashboard')
@login_required
def dashboard():
    user = get_user_by_id(session['user_id'])
    db = get_db()
    stats = db.execute("SELECT COUNT(*) as total_pedidos, COALESCE(SUM(total), 0) as total_gastado FROM pedidos WHERE usuario_id = ?", (user['id'],)).fetchone()
    ultimos = db.execute("SELECT p.*, pr.nombre as producto_nombre FROM pedidos p JOIN productos pr ON p.producto_id = pr.id WHERE p.usuario_id = ? ORDER BY p.fecha_pedido DESC LIMIT 5", (user['id'],)).fetchall()
    categorias = db.execute(
        "SELECT c.*, COALESCE(pc.total_productos, 0) as total_productos "
        "FROM categorias c "
        "LEFT JOIN ("
        "  SELECT categoria_id, COUNT(*) as total_productos "
        "  FROM productos WHERE activo = 1 GROUP BY categoria_id"
        ") pc ON pc.categoria_id = c.id "
        "WHERE c.activo = 1 "
        "ORDER BY c.orden"
    ).fetchall()
    popup_publicitario = _obtener_popup_publicitario_para_usuario(db, user['id'])
    saldo = get_saldo(user['id'])
    db.close()
    return render_template(
        'dashboard.html',
        user=user,
        stats=stats,
        ultimos=ultimos,
        categorias=categorias,
        saldo=saldo,
        popup_publicitario=popup_publicitario,
    )


# ===== CATALOGO =====
@app.route('/catalogo')
@login_required
def catalogo():
    db = get_db()
    categorias = db.execute(
        "SELECT c.*, COALESCE(pc.total_productos, 0) as total_productos "
        "FROM categorias c "
        "LEFT JOIN ("
        "  SELECT categoria_id, COUNT(*) as total_productos "
        "  FROM productos WHERE activo = 1 GROUP BY categoria_id"
        ") pc ON pc.categoria_id = c.id "
        "WHERE c.activo = 1 "
        "ORDER BY c.orden"
    ).fetchall()
    hay_pases_de_nivel = db.execute(
        "SELECT 1 FROM productos WHERE activo = 1 AND COALESCE(freefire_levelpass, '') != '' LIMIT 1"
    ).fetchone() is not None
    db.close()
    return render_template('catalogo.html', categorias=categorias, hay_pases_de_nivel=hay_pases_de_nivel)


@app.route('/catalogo/<slug>')
@login_required
def catalogo_juego(slug):
    db = get_db()
    cat = db.execute("SELECT * FROM categorias WHERE slug = ? AND activo = 1", (slug,)).fetchone()
    if not cat:
        flash('Juego no encontrado', 'error')
        return redirect(url_for('catalogo'))
    if cat['nombre'].strip().lower() == 'pases de nivel':
        db.close()
        return redirect(url_for('pases_de_nivel'))
    productos_rows = db.execute("SELECT * FROM productos WHERE categoria_id = ? AND activo = 1 ORDER BY orden ASC, precio ASC", (cat['id'],)).fetchall()
    user_row = db.execute("SELECT suscripcion_hasta FROM usuarios WHERE id = ?", (session['user_id'],)).fetchone()

    productos = []
    moogold_stock_cache = {}
    for prod in productos_rows:
        d = dict(prod)
        precio_normal = float(d.get('precio', 0) or 0)
        precio_final = _precio_producto_para_usuario(prod, user_row)
        d['precio_normal'] = precio_normal
        d['precio'] = precio_final
        d['precio_suscriptor_aplicado'] = precio_final != precio_normal
        if cat['tipo'] == 'giftcards':
            stock = db.execute("SELECT COUNT(*) as c FROM pines WHERE producto_id = ? AND estado = 'disponible'", (prod['id'],)).fetchone()['c']
            d['stock_disponible'] = stock
            mg_product_id = int((d.get('moogold_product_id') or 0))
            mg_variation_id = int((d.get('moogold_variation_id') or 0))
            if mg_product_id > 0 and mg_variation_id > 0:
                mg_stock = _moogold_stock_variacion(mg_product_id, mg_variation_id, moogold_stock_cache)
                d['moogold_stock_status'] = mg_stock.get('status', '')
                d['moogold_disponible'] = bool(mg_stock.get('disponible', True))
            d['stock_ilimitado'] = bool(
                (int((d.get('usa_pincentral') or 0)) and (int((d.get('pincentral_entrega_directa') or 0)) or int((d.get('pincentral_recarga_directa') or 0))))
                or mg_product_id > 0
                or int((d.get('gamepoint_product_id') or 0)) > 0
                or bool(str(d.get('bloodstrike_package_id') or '').strip())
            )
        productos.append(d)

    db.close()
    return render_template('catalogo_juego.html', categoria=cat, productos=productos)


@app.route('/lista-precios')
@app.route('/listade-precios')
@login_required
def lista_precios():
    db = get_db()
    rows = db.execute(
        "SELECT p.id, p.nombre, p.precio, p.precio_suscriptor, c.nombre as categoria_nombre "
        "FROM productos p "
        "JOIN categorias c ON p.categoria_id = c.id "
        "WHERE p.activo = 1 AND c.activo = 1 "
        "ORDER BY c.orden, c.nombre, p.orden, p.nombre"
    ).fetchall()
    db.close()

    productos = []
    for r in rows:
        precio_normal = float(r['precio'] or 0)
        precio_sub_raw = float(r['precio_suscriptor'] or 0)
        productos.append({
            'id': r['id'],
            'nombre': r['nombre'],
            'categoria_nombre': r['categoria_nombre'] or 'Sin categoría',
            'precio_normal': precio_normal,
            'precio_suscriptor': (precio_sub_raw if precio_sub_raw > 0 else precio_normal),
            'tiene_precio_suscriptor': precio_sub_raw > 0,
        })

    return render_template('lista_precios.html', productos=productos)


@app.route('/producto/<int:id>')
@login_required
def producto(id):
    import json as _json
    db = get_db()
    prod = db.execute("SELECT p.*, c.nombre as categoria_nombre, c.slug as categoria_slug, c.tipo as categoria_tipo, c.verificar_nombre, c.verificar_nombre_tipo FROM productos p JOIN categorias c ON p.categoria_id = c.id WHERE p.id = ? AND p.activo = 1", (id,)).fetchone()
    if not prod:
        db.close()
        flash('Producto no encontrado', 'error')
        return redirect(url_for('catalogo'))
    user_row = db.execute("SELECT suscripcion_hasta FROM usuarios WHERE id = ?", (session['user_id'],)).fetchone()
    stock_disponible = 0
    if prod['categoria_tipo'] == 'giftcards':
        stock_disponible = db.execute("SELECT COUNT(*) as c FROM pines WHERE producto_id = ? AND estado = 'disponible'", (id,)).fetchone()['c']
    db.close()
    # Convertir a dict y parsear gamepoint_fields JSON
    prod_dict = dict(prod)
    if prod_dict.get('categoria_tipo') == 'giftcards':
        prod_dict['stock_disponible'] = stock_disponible
        mg_product_id = int((prod_dict.get('moogold_product_id') or 0))
        mg_variation_id = int((prod_dict.get('moogold_variation_id') or 0))
        if mg_product_id > 0 and mg_variation_id > 0:
            mg_stock = _moogold_stock_variacion(mg_product_id, mg_variation_id)
            prod_dict['moogold_stock_status'] = mg_stock.get('status', '')
            prod_dict['moogold_disponible'] = bool(mg_stock.get('disponible', True))
        prod_dict['stock_ilimitado'] = bool(
            (int((prod_dict.get('usa_pincentral') or 0)) and (int((prod_dict.get('pincentral_entrega_directa') or 0)) or int((prod_dict.get('pincentral_recarga_directa') or 0))))
            or mg_product_id > 0
            or int((prod_dict.get('gamepoint_product_id') or 0)) > 0
            or bool(str(prod_dict.get('bloodstrike_package_id') or '').strip())
        )
    precio_normal = float(prod_dict.get('precio', 0) or 0)
    precio_final = _precio_producto_para_usuario(prod, user_row)
    prod_dict['precio_normal'] = precio_normal
    prod_dict['precio'] = precio_final
    prod_dict['suscripcion_activa'] = _suscripcion_activa_desde_row(user_row)
    prod_dict['precio_suscriptor_aplicado'] = precio_final != precio_normal
    prod_dict['campos_cliente_parsed'] = _parse_campos_cliente(prod_dict.get('campos_cliente'))
    if prod_dict['campos_cliente_parsed']:
        prod_dict['gamepoint_fields'] = []
    elif prod_dict.get('gamepoint_fields'):
        try:
            prod_dict['gamepoint_fields'] = _json.loads(prod_dict['gamepoint_fields'])
        except Exception:
            prod_dict['gamepoint_fields'] = []
    else:
        prod_dict['gamepoint_fields'] = []
    if prod_dict.get('moogold_fields'):
        prod_dict['moogold_fields'] = _moogold_parse_field_defs(prod_dict.get('moogold_fields'))
    else:
        prod_dict['moogold_fields'] = []
    if int(prod_dict.get('usa_pincentral') or 0) and int(prod_dict.get('pincentral_recarga_directa') or 0):
        prod_dict['pincentral_fields_parsed'] = _pincentral_parse_fields(prod_dict.get('pincentral_fields'))
    else:
        prod_dict['pincentral_fields_parsed'] = []
    saldo = get_saldo(session['user_id'])
    player_id_prefill = request.args.get('player_id', '').strip()
    levelpass_verificado = False
    player_name_cache = ''
    if player_id_prefill and prod_dict.get('freefire_levelpass'):
        cached = _get_cached_levelpass(player_id_prefill, prod_dict['id'])
        if cached and cached.get('available'):
            levelpass_verificado = True
            player_name_cache = cached.get('nombre')
    return render_template(
        'producto.html',
        producto=prod_dict,
        saldo=saldo,
        player_id_prefill=player_id_prefill,
        levelpass_verificado=levelpass_verificado,
        player_name_cache=player_name_cache,
    )


# ===== COMPRAR =====
@app.route('/comprar', methods=['POST'])
@login_required
def comprar():
    producto_id = int(request.form.get('producto_id', 0))
    cantidad = int(request.form.get('cantidad', 1))
    id_juego = request.form.get('id_juego', '').strip()
    input2 = request.form.get('input2', '').strip()

    db = get_db()
    prod = db.execute("SELECT p.*, c.nombre as categoria_nombre, c.tipo as categoria_tipo, c.validar_id_api, c.validar_id_api_tipo FROM productos p JOIN categorias c ON p.categoria_id = c.id WHERE p.id = ? AND p.activo = 1", (producto_id,)).fetchone()
    if not prod:
        flash('Producto no encontrado', 'error')
        db.close()
        return redirect(url_for('catalogo'))

    if int((prod['rechazo_automatico'] if 'rechazo_automatico' in prod.keys() else 0) or 0):
        flash('Este producto está temporalmente deshabilitado. Intenta más tarde.', 'error')
        db.close()
        return redirect(url_for('producto', id=producto_id))

    usa_razer = prod['usa_razer'] if 'usa_razer' in prod.keys() else 0
    usa_deltaforce = prod['usa_deltaforce'] if 'usa_deltaforce' in prod.keys() else 0
    moogold_category_id = int((prod['moogold_category_id'] if 'moogold_category_id' in prod.keys() else 0) or 0)
    moogold_variation_id = int((prod['moogold_variation_id'] if 'moogold_variation_id' in prod.keys() else 0) or 0)
    moogold_fields_raw = (prod['moogold_fields'] if 'moogold_fields' in prod.keys() else '') or ''
    moogold_field_names = _moogold_parse_fields(moogold_fields_raw)
    mg_inputs = _extract_named_inputs(request.form, moogold_field_names)
    if moogold_field_names and not id_juego:
        id_juego = str(mg_inputs.get('mg_field_0', '') or mg_inputs.get(moogold_field_names[0], '') or '').strip()
    usa_moogold = moogold_category_id > 0 and moogold_variation_id > 0
    bloodstrike_package_id = str((prod['bloodstrike_package_id'] if 'bloodstrike_package_id' in prod.keys() else '') or '').strip()
    usa_bloodstrike = bool(bloodstrike_package_id)
    usa_pincentral = int((prod['usa_pincentral'] if 'usa_pincentral' in prod.keys() else 0) or 0)
    pincentral_entrega_directa = int((prod['pincentral_entrega_directa'] if 'pincentral_entrega_directa' in prod.keys() else 0) or 0)
    pincentral_recarga_directa = int((prod['pincentral_recarga_directa'] if 'pincentral_recarga_directa' in prod.keys() else 0) or 0)
    if prod['categoria_tipo'] == 'giftcards' and usa_pincentral and pincentral_entrega_directa:
        cantidad = 1
    requiere_id_moogold = usa_moogold and bool(moogold_field_names)
    freefire_levelpass = str((prod['freefire_levelpass'] if 'freefire_levelpass' in prod.keys() else '') or '').strip()
    if (prod['usa_api'] or usa_razer or usa_deltaforce or requiere_id_moogold or usa_bloodstrike or (usa_pincentral and pincentral_recarga_directa) or freefire_levelpass) and not id_juego:
        flash('Debes ingresar el ID del jugador para esta recarga.', 'error')
        db.close()
        return redirect(url_for('producto', id=producto_id))

    # Validar ID del jugador vía API si la categoría lo exige
    nombre_jugador_api = ''
    if int((prod['validar_id_api'] if 'validar_id_api' in prod.keys() else 0) or 0) and id_juego:
        tipo_val = str((prod['validar_id_api_tipo'] if 'validar_id_api_tipo' in prod.keys() else '') or '').strip() or 'freefire'
        val_api = verificar_nombre_jugador(tipo_val, id_juego, input2)
        if not val_api.get('ok'):
            flash(val_api.get('error') or 'ID de jugador no válido. Verifica el ID antes de comprar.', 'error')
            db.close()
            return redirect(url_for('producto', id=producto_id))
        nombre_jugador_api = val_api.get('nombre', '')

    if freefire_levelpass:
        cached = _get_cached_levelpass(id_juego, producto_id)
        if cached:
            lp_check = {'ok': True, 'available': cached.get('available')}
        else:
            lp_check = _verificar_freefire_levelpass(id_juego, freefire_levelpass, validar_id_tipo='freefire')
            if lp_check.get('ok'):
                _set_cached_levelpass(id_juego, producto_id, lp_check.get('available'), lp_check.get('nombre'))
        if not lp_check.get('ok') or not lp_check.get('available'):
            flash(lp_check.get('error') or 'Error al validar la disponibilidad del pase. No es posible comprar este producto.', 'error')
            db.close()
            return redirect(url_for('producto', id=producto_id))

    moogold_product_id = int((prod['moogold_product_id'] if 'moogold_product_id' in prod.keys() else 0) or 0)
    moogold_variation_id = int((prod['moogold_variation_id'] if 'moogold_variation_id' in prod.keys() else 0) or 0)
    if prod['categoria_tipo'] == 'giftcards' and moogold_product_id > 0 and moogold_variation_id > 0:
        mg_stock = _moogold_stock_variacion(moogold_product_id, moogold_variation_id)
        if not mg_stock.get('disponible', True):
            flash('Producto agotado temporalmente. Intenta más tarde.', 'error')
            db.close()
            return redirect(url_for('producto', id=producto_id))

    usa_stock_externo = bool(
        (usa_pincentral and (pincentral_entrega_directa or pincentral_recarga_directa))
        or moogold_product_id > 0
        or int((prod['gamepoint_product_id'] if 'gamepoint_product_id' in prod.keys() else 0) or 0) > 0
        or usa_bloodstrike
    )
    if prod['categoria_tipo'] == 'giftcards' and not usa_stock_externo:
        cant_pines_requeridos = min(cantidad, 50)
        stock_disponible = db.execute("SELECT COUNT(*) as c FROM pines WHERE producto_id = ? AND estado = 'disponible'", (producto_id,)).fetchone()['c']
        if stock_disponible < cant_pines_requeridos:
            flash(f'Producto agotado. Stock disponible: {stock_disponible}.', 'error')
            db.close()
            return redirect(url_for('producto', id=producto_id))

    user_id = session['user_id']
    user_row = db.execute("SELECT suscripcion_hasta FROM usuarios WHERE id = ?", (user_id,)).fetchone()
    precio_unitario = _precio_producto_para_usuario(prod, user_row)
    total = precio_unitario * cantidad

    desc_compra = f"Compra: {prod['nombre']} x{cantidad}"
    if precio_unitario != float((prod['precio'] if 'precio' in prod.keys() else 0) or 0):
        desc_compra += " (tarifa suscriptor)"
    resultado = descontar_saldo(user_id, total, desc_compra)
    if resultado is None:
        saldo = get_saldo(user_id)
        flash(f'Saldo insuficiente. Tu saldo es ${saldo:.4f} y el total es ${total:.4f}', 'error')
        db.close()
        return redirect(url_for('producto', id=producto_id))

    db.execute("INSERT INTO pedidos (usuario_id, producto_id, cantidad, total, id_juego, nombre_jugador, estado) VALUES (?,?,?,?,?,?,?)",
               (user_id, producto_id, cantidad, total, id_juego, nombre_jugador_api, 'procesando'))
    pedido_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Actualizar transacción con pedido_id
    db.execute("UPDATE transacciones SET pedido_id = ? WHERE id = (SELECT id FROM transacciones WHERE usuario_id = ? AND pedido_id IS NULL ORDER BY id DESC LIMIT 1)",
               (pedido_id, user_id))
    db.commit()

    # Gift Card con entrega directa PinCentral (sin almacenar en almacén, 1 PIN por pedido)
    if prod['categoria_tipo'] == 'giftcards' and usa_pincentral and pincentral_entrega_directa:
        from pincentral_api import autorizar_pins, capturar_pins

        product_code = str((prod['pincentral_product_code'] if 'pincentral_product_code' in prod.keys() else '') or '').strip()
        if not product_code:
            db.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
            db.commit()
            db.close()
            recargar_saldo(user_id, total, f"Reembolso: Código PinCentral no configurado pedido #{pedido_id}")
            flash('Este producto no está configurado correctamente. Se reembolsó tu saldo.', 'error')
            return redirect(url_for('pedido_detalle', id=pedido_id))

        user = db.execute("SELECT nombre, email FROM usuarios WHERE id = ?", (user_id,)).fetchone()
        db.close()
        client_name = (user['nombre'] if user else '') or ''
        client_email = (user['email'] if user else '') or ''
        order_id = f"PCD{pedido_id}"

        try:
            auth = autorizar_pins(product_code, 1, order_id, client_name=client_name, client_email=client_email)
            auth_data = auth.get('data', {}) if isinstance(auth.get('data', {}), dict) else {}
            auth_status = _pincentral_status_normalizado(auth_data.get('status', ''))
            tx_id = str(auth_data.get('id', '') or '').strip()

            if (not auth.get('ok')) or (not _pincentral_autorizado(auth_status)) or not tx_id:
                error_msg = auth.get('error') or auth_data.get('message') or f"Estado autorización: {auth_data.get('status', 'desconocido')}"
                db_err = get_db()
                db_err.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
                db_err.commit()
                db_err.close()
                recargar_saldo(user_id, total, f"Reembolso: Error API PinCentral pedido #{pedido_id}")
                flash('No fue posible autorizar la entrega. Se reembolsó tu saldo.', 'error')
                return redirect(url_for('pedido_detalle', id=pedido_id))

            cap = capturar_pins(tx_id)
            cap_data = cap.get('data', {}) if isinstance(cap.get('data', {}), dict) else {}
            cap_status = _pincentral_status_normalizado(cap_data.get('status', ''))
            pins = cap_data.get('pins', []) if isinstance(cap_data.get('pins', []), list) else []
            errores_key = _pincentral_detectar_key_vacia(
                pins,
                contexto='pedido_directo',
                pedido_id=pedido_id,
                producto_id=producto_id,
                product_code=product_code,
                order_id=order_id,
                transaction_id=tx_id,
            )
            codigos = _formatear_pins_pincentral(pins)

            if (not cap.get('ok')) or (not _pincentral_capturado(cap_status)) or not codigos or errores_key:
                error_msg = cap.get('error') or cap_data.get('message') or f"Estado captura: {cap_data.get('status', 'desconocido')}"
                if errores_key:
                    error_msg = f"PinCentral devolvió key vacío: {'; '.join(errores_key)}"
                db_err = get_db()
                db_err.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
                db_err.commit()
                db_err.close()
                recargar_saldo(user_id, total, f"Reembolso: Error captura PinCentral pedido #{pedido_id}")
                flash('No fue posible completar la entrega. Se reembolsó tu saldo.', 'error')
                return redirect(url_for('pedido_detalle', id=pedido_id))

            db_ok = get_db()
            db_ok.execute(
                "UPDATE pedidos SET estado = 'completado', cantidad = 1, codigo_entregado = ?, referencia_externa = ? WHERE id = ?",
                (codigos, tx_id, pedido_id),
            )
            db_ok.commit()
            db_ok.close()
            enviar_webhook(user_id, {
                'evento': 'pedido_actualizado',
                'pedido_id': pedido_id,
                'estado': 'completado',
                'referencia': tx_id,
                'cantidad_codigos': 1,
                'mensaje': 'Código entregado'
            })
            flash(f'Pedido #{pedido_id} completado. Código entregado.', 'success')
            return redirect(url_for('pedido_detalle', id=pedido_id))
        except Exception as e:
            db_err = get_db()
            db_err.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
            db_err.commit()
            db_err.close()
            recargar_saldo(user_id, total, f"Reembolso: Excepción PinCentral directo pedido #{pedido_id}")
            flash('Error inesperado en la entrega. Se reembolsó tu saldo.', 'error')
            return redirect(url_for('pedido_detalle', id=pedido_id))

    # Si el producto usa PinCentral Recarga directa
    if usa_pincentral and pincentral_recarga_directa:
        from pincentral_api import crear_recarga, validar_recarga

        product_code = str((prod['pincentral_product_code'] if 'pincentral_product_code' in prod.keys() else '') or '').strip()
        if not product_code:
            db.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
            db.commit()
            db.close()
            recargar_saldo(user_id, total, f"Reembolso: Código PinCentral recarga no configurado pedido #{pedido_id}")
            flash('Este producto no está configurado correctamente. Se reembolsó tu saldo.', 'error')
            return redirect(url_for('pedido_detalle', id=pedido_id))

        user = db.execute("SELECT nombre, email FROM usuarios WHERE id = ?", (user_id,)).fetchone()
        db.close()
        nombre_partes = str((user['nombre'] if user else '') or '').split(' ', 1)
        first_name = nombre_partes[0] if nombre_partes else ''
        last_name = nombre_partes[1] if len(nombre_partes) > 1 else ''
        recargas_total = max(1, min(int((prod['pincentral_recarga_cantidad'] if 'pincentral_recarga_cantidad' in prod.keys() else 1) or 1), 20))

        # Validar la cuenta del jugador antes de intentar la recarga
        input2 = request.form.get('input2', '').strip()
        additional_data_2 = request.form.get('additional_data_2', '').strip()
        validacion = validar_recarga(
            product_code=product_code,
            service_user_id=id_juego,
            additional_data=input2,
            additional_data_2=additional_data_2,
        )
        val_data = validacion.get('data', {}) if isinstance(validacion.get('data', {}), dict) else {}
        val_status = val_data.get('status')
        val_ok = validacion.get('ok') and (
            val_status is True or str(val_status).strip().lower() in ('true', '1', 'ok', 'success')
        )
        if not val_ok:
            db2 = get_db()
            db2.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
            db2.commit()
            db2.close()
            error_msg = validacion.get('error') or val_data.get('message') or 'Cuenta o ID de jugador inválido'
            recargar_saldo(user_id, total, f"Reembolso: Validación PinCentral fallida pedido #{pedido_id}: {error_msg}")
            flash(f'La validación falló: {error_msg}. Se reembolsó tu saldo.', 'error')
            return redirect(url_for('pedido_detalle', id=pedido_id))

        try:
            refs = []
            receipts = []
            errores = []
            pendientes = 0
            for idx in range(1, recargas_total + 1):
                order_id = f"PCR{pedido_id}-{idx}"
                resultado_pc = crear_recarga(
                    product_code=product_code,
                    service_user_id=id_juego,
                    order_id=order_id,
                    additional_data=request.form.get('input2', '').strip(),
                    additional_data_2=request.form.get('additional_data_2', '').strip(),
                )
                data_pc = resultado_pc.get('data', {}) if isinstance(resultado_pc.get('data', {}), dict) else {}
                estado_pc = _pincentral_estado_recarga(data_pc)
                ref = str(data_pc.get('id') or data_pc.get('receipt') or '').strip()
                receipt = str(data_pc.get('receipt') or '').strip()
                if ref:
                    refs.append(f"{idx}/{recargas_total}: {ref}")
                if receipt:
                    receipts.append(f"{idx}/{recargas_total}: {receipt}")
                if resultado_pc.get('ok') and estado_pc == 'completed':
                    continue
                if resultado_pc.get('ok') and estado_pc in ('created', 'retry'):
                    pendientes += 1
                    continue
                error_msg = resultado_pc.get('error') or data_pc.get('message') or f"Estado: {data_pc.get('status', 'desconocido')}"
                errores.append(f"{idx}/{recargas_total}: {error_msg}")
                break
            ref_text = '\n'.join(refs)
            receipt_text = '\n'.join(receipts) or id_juego
            db2 = get_db()
            if not errores and pendientes == 0:
                db2.execute("UPDATE pedidos SET estado = 'completado', nombre_jugador = ?, referencia_externa = ? WHERE id = ?", (receipt_text, ref_text, pedido_id))
                db2.commit()
                db2.close()
                flash(f'Pedido #{pedido_id} completado ({recargas_total} recarga(s)).', 'success')
                return redirect(url_for('pedido_detalle', id=pedido_id))
            if refs:
                db2.execute("UPDATE pedidos SET estado = 'procesando', nombre_jugador = ?, referencia_externa = ? WHERE id = ?", (receipt_text, ref_text, pedido_id))
                db2.commit()
                db2.close()
                flash(f'Pedido #{pedido_id} quedó procesando/parcial. Referencias guardadas.', 'warning')
                return redirect(url_for('pedido_detalle', id=pedido_id))
            db2.execute("UPDATE pedidos SET estado = 'cancelado', referencia_externa = ? WHERE id = ?", (ref_text, pedido_id))
            db2.commit()
            db2.close()
            recargar_saldo(user_id, total, f"Reembolso: Error recarga PinCentral pedido #{pedido_id}")
            flash('La recarga fue rechazada. Se reembolsó tu saldo.', 'error')
            return redirect(url_for('pedido_detalle', id=pedido_id))
        except Exception as e:
            db2 = get_db()
            db2.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
            db2.commit()
            db2.close()
            recargar_saldo(user_id, total, f"Reembolso: Excepción PinCentral recarga pedido #{pedido_id}")
            flash('Error inesperado en la recarga. Se reembolsó tu saldo.', 'error')
            return redirect(url_for('pedido_detalle', id=pedido_id))

    # Si el producto usa Blood Strike API
    if usa_bloodstrike:
        proveedor_recarga = str((prod['categoria_nombre'] if 'categoria_nombre' in prod.keys() else '') or '').strip() or ('Free Fire Promo Bonus' if bloodstrike_package_id.startswith('pinxtore_') else ('Free Fire' if bloodstrike_package_id.startswith('diamonds') else 'Blood Strike'))
        referencia_recarga = f"BS{pedido_id}" if proveedor_recarga == 'Blood Strike' else f"FF{pedido_id}"
        db.execute("UPDATE pedidos SET estado = 'procesando', referencia_externa = ? WHERE id = ?", (referencia_recarga, pedido_id))
        db.commit()
        db.close()
        threading.Thread(
            target=procesar_pedido_bloodstrike_background,
            args=(pedido_id, user_id, total, id_juego, bloodstrike_package_id),
            daemon=True,
        ).start()
        flash(f'Pedido #{pedido_id} recibido. La recarga seguirá procesándose en segundo plano.', 'warning')
        return redirect(url_for('pedido_detalle', id=pedido_id))

    # Si el producto usa GamePoint API (recarga directa o gift card)
    if prod['gamepoint_product_id'] and prod['gamepoint_package_id']:
        # Guardar datos necesarios y CERRAR DB antes de llamar API externa
        gp_product_id = prod['gamepoint_product_id']
        gp_package_id = prod['gamepoint_package_id']
        es_manual = prod['recarga_manual'] if 'recarga_manual' in prod.keys() else 0
        db.close()
        try:
            from gamepoint_api import recarga_completa
            merchant_code = f"PED{pedido_id}"
            gp_fields = {"input1": id_juego} if id_juego else {}
            input2 = request.form.get('input2', '').strip()
            if input2:
                gp_fields["input2"] = input2
            resultado_api = recarga_completa(
                product_id=gp_product_id,
                fields=gp_fields,
                package_id=gp_package_id,
                merchant_code=merchant_code,
                wait=False
            )
            # Reabrir DB para guardar resultado
            db2 = get_db()
            if resultado_api.get('ok'):
                nombre_jugador = resultado_api.get('ingamename', '')
                ref = resultado_api.get('referenceno', '')
                es_giftcard_gp = (prod['categoria_tipo'] == 'giftcards')
                codigo = resultado_api.get('item', '') if es_giftcard_gp else ''
                gp_status = str(resultado_api.get('status', '') or '').strip().lower()
                if es_manual and gp_status == 'pending':
                    estado_final = 'procesando'
                else:
                    estado_final = 'completado'
                db2.execute("UPDATE pedidos SET estado = ?, nombre_jugador = ?, codigo_entregado = ?, referencia_externa = ? WHERE id = ?", (estado_final, nombre_jugador or ref, codigo, ref, pedido_id))
                db2.commit()
                db2.close()
                if es_manual:
                    if estado_final == 'procesando':
                        flash(f'Pedido #{pedido_id} recibido (Ref: {ref}). Se confirmará automáticamente cuando termine el proceso.', 'success')
                    else:
                        flash(f'Pedido #{pedido_id} completado. Recarga aplicada a {nombre_jugador or id_juego} (Ref: {ref}).', 'success')
                elif es_giftcard_gp and codigo:
                    flash(f'Pedido #{pedido_id} completado. Código: {codigo}', 'success')
                else:
                    flash(f'Pedido #{pedido_id} completado. Recarga aplicada a {nombre_jugador or id_juego} (Ref: {ref}).', 'success')
                return redirect(url_for('pedido_detalle', id=pedido_id))
            else:
                if es_manual:
                    # Para recarga_manual: si falló de forma final, cancelar y reembolsar.
                    gp_status = str(resultado_api.get('status', '') or '').strip().lower()
                    ref = resultado_api.get('referenceno', '')
                    if gp_status == 'failed':
                        db2.execute("UPDATE pedidos SET estado = 'cancelado', referencia_externa = ? WHERE id = ?", (ref, pedido_id))
                        db2.commit()
                        db2.close()
                        recargar_saldo(user_id, total, f"Reembolso: Error GamePoint pedido #{pedido_id}")
                        error_msg = resultado_api.get('error', resultado_api.get('message', 'Pedido rechazado'))
                        flash(f'Pedido #{pedido_id} rechazado. Se reembolsó ${total:.4f} a tu cartera.', 'error')
                        return redirect(url_for('pedido_detalle', id=pedido_id))
                    if ref:
                        db2.execute("UPDATE pedidos SET estado = 'procesando', referencia_externa = ? WHERE id = ?", (ref, pedido_id))
                    else:
                        db2.execute("UPDATE pedidos SET estado = 'procesando' WHERE id = ?", (pedido_id,))
                        # Sin referencia — notificar admin por Telegram
                        from telegram_bot import enviar_telegram
                        enviar_telegram(
                            f"⚠️ <b>Pedido #{pedido_id} SIN REFERENCIA</b>\n\n"
                            f"🎮 Producto: {prod['nombre']}\n"
                            f"👤 ID Juego: {id_juego}\n"
                            f"💵 Total: ${total:.4f}\n"
                            f"❌ Error: {resultado_api.get('error', resultado_api.get('message', 'Sin respuesta'))}\n\n"
                            f"📋 Revisa manualmente en GamePoint y marca como completado o cancelado."
                        )
                    db2.commit()
                    db2.close()
                    error_msg = resultado_api.get('error', resultado_api.get('message', 'Error desconocido'))
                    flash(f'Pedido #{pedido_id} enviado pero respuesta incierta ({error_msg}). Se verificará automáticamente.', 'warning')
                    return redirect(url_for('pedido_detalle', id=pedido_id))
                else:
                    db2.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
                    db2.commit()
                    db2.close()
                    recargar_saldo(user_id, total, f"Reembolso: Error GamePoint pedido #{pedido_id}")
                    error_msg = resultado_api.get('error', resultado_api.get('message', 'Error desconocido'))
                    flash(f'Error en recarga: {error_msg}. Se reembolsó ${total:.4f} a tu cartera.', 'error')
                    return redirect(url_for('pedido_detalle', id=pedido_id))
        except Exception as e:
            db2 = get_db()
            if es_manual:
                db2.execute("UPDATE pedidos SET estado = 'procesando' WHERE id = ?", (pedido_id,))
                db2.commit()
                db2.close()
                # Sin referencia por excepción — notificar admin
                from telegram_bot import enviar_telegram
                enviar_telegram(
                    f"⚠️ <b>Pedido #{pedido_id} SIN REFERENCIA (excepción)</b>\n\n"
                    f"🎮 Producto: {prod['nombre']}\n"
                    f"👤 ID Juego: {id_juego}\n"
                    f"💵 Total: ${total:.4f}\n"
                    f"❌ Error: {str(e)}\n\n"
                    f"📋 Revisa manualmente en GamePoint y marca como completado o cancelado."
                )
                flash(f'Pedido #{pedido_id} enviado pero hubo un error de conexión. Se verificará automáticamente.', 'warning')
                return redirect(url_for('pedido_detalle', id=pedido_id))
            else:
                db2.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
                db2.commit()
                db2.close()
                recargar_saldo(user_id, total, f"Reembolso: Excepción GamePoint pedido #{pedido_id}")
                flash(f'Error inesperado en la recarga. Se reembolsó ${total:.4f} a tu cartera.', 'error')
                return redirect(url_for('pedido_detalle', id=pedido_id))

    # Si el producto usa MooGold API
    elif usa_moogold:
        from moogold_api import crear_orden

        mg_input2 = str(request.form.get('input2', '') or mg_inputs.get('mg_field_1', '')).strip()
        account_fields = _moogold_build_account_fields(moogold_fields_raw, id_juego, mg_input2, mg_inputs)
        partner_order_id = f"MGW{pedido_id}"
        moogold_category_orden = _moogold_category_efectiva(prod['categoria_tipo'], moogold_category_id)

        db.close()
        try:
            resultado_api = crear_orden(
                category=moogold_category_orden,
                variation_id=moogold_variation_id,
                quantity=cantidad,
                account_fields=account_fields,
                partner_order_id=partner_order_id,
            )
            db2 = get_db()
            if resultado_api.get('ok'):
                data_mg = resultado_api.get('data') if isinstance(resultado_api.get('data'), dict) else {}
                ref = _moogold_extract_ref(data_mg)
                mg_status = str(data_mg.get('status', '') or '').strip().lower()
                nombre_jugador = _moogold_extract_nombre(data_mg, id_juego)
                codigo = _moogold_extract_code(data_mg)

                if prod['categoria_tipo'] == 'giftcards' and not codigo and ref:
                    detail_data = _moogold_order_detail_safe(ref)
                    if detail_data:
                        mg_status = str(detail_data.get('order_status', '') or detail_data.get('status', '') or mg_status).strip().lower()
                        nombre_jugador = _moogold_extract_nombre(detail_data, nombre_jugador)
                        codigo = _moogold_extract_code(detail_data) or codigo

                estado_final = _estado_interno_desde_moogold(mg_status) if mg_status else 'procesando'
                if codigo and estado_final != 'cancelado':
                    estado_final = 'completado'

                if estado_final == 'completado' and codigo:
                    db2.execute(
                        "UPDATE pedidos SET estado = ?, nombre_jugador = ?, codigo_entregado = ?, referencia_externa = ?, referencia_cliente = ? WHERE id = ?",
                        (estado_final, nombre_jugador, codigo, ref, partner_order_id, pedido_id),
                    )
                else:
                    db2.execute(
                        "UPDATE pedidos SET estado = ?, nombre_jugador = ?, referencia_externa = ?, referencia_cliente = ? WHERE id = ?",
                        (estado_final, nombre_jugador, ref, partner_order_id, pedido_id),
                    )
                db2.commit()
                db2.close()

                if estado_final == 'completado':
                    flash(f'Pedido #{pedido_id} completado (Ref: {ref or "sin referencia"}).', 'success')
                else:
                    flash(f'Pedido #{pedido_id} recibido (Ref: {ref or "pendiente"}). Estado: {estado_final}.', 'warning')
                return redirect(url_for('pedido_detalle', id=pedido_id))

            db2.execute("UPDATE pedidos SET estado = 'cancelado', referencia_cliente = ? WHERE id = ?", (partner_order_id, pedido_id))
            db2.commit()
            db2.close()
            recargar_saldo(user_id, total, f"Reembolso: Error MooGold pedido #{pedido_id}")
            flash(f"No fue posible procesar el pedido. Se reembolsó ${total:.4f}.", 'error')
            return redirect(url_for('pedido_detalle', id=pedido_id))
        except Exception as e:
            db2 = get_db()
            db2.execute("UPDATE pedidos SET estado = 'cancelado', referencia_cliente = ? WHERE id = ?", (partner_order_id, pedido_id))
            db2.commit()
            db2.close()
            recargar_saldo(user_id, total, f"Reembolso: Excepción MooGold pedido #{pedido_id}")
            flash(f'Error inesperado al procesar el pedido. Se reembolsó ${total:.4f}.', 'error')
            return redirect(url_for('pedido_detalle', id=pedido_id))

    # Si el producto usa API Razer (separada), recarga directa por paquete
    elif (prod['usa_razer'] if 'usa_razer' in prod.keys() else 0) and id_juego:
        paquete_principal = int((prod['razer_paquete'] if 'razer_paquete' in prod.keys() else 0) or 0)
        paquete_extra = int((prod['razer_paquete_extra'] if 'razer_paquete_extra' in prod.keys() else 0) or 0)
        paquete = paquete_extra if paquete_extra > 0 else paquete_principal
        if paquete <= 0:
            db.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
            db.commit()
            db.close()
            recargar_saldo(user_id, total, f"Reembolso: Paquete Razer no configurado pedido #{pedido_id}")
            flash('Este producto no está configurado correctamente. Se reembolsó tu saldo.', 'error')
            return redirect(url_for('pedido_detalle', id=pedido_id))

        db.execute("UPDATE pedidos SET estado = 'pendiente' WHERE id = ?", (pedido_id,))
        db.commit()
        db.close()
        threading.Thread(
            target=procesar_pedido_razer_background,
            args=(pedido_id, user_id, total, id_juego, paquete, cantidad),
            daemon=True,
        ).start()
        flash(f'Pedido #{pedido_id} en procesamiento. Revisa Mis pedidos para ver si fue aprobado o rechazado.', 'warning')
        return redirect(url_for('pedido_detalle', id=pedido_id))

    # Si el producto usa API Delta Force (separada), recarga directa por paquete
    elif (prod['usa_deltaforce'] if 'usa_deltaforce' in prod.keys() else 0) and id_juego:
        paquete = int((prod['deltaforce_paquete'] if 'deltaforce_paquete' in prod.keys() else 0) or 0)
        if paquete <= 0:
            db.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
            db.commit()
            db.close()
            recargar_saldo(user_id, total, f"Reembolso: Paquete Delta Force no configurado pedido #{pedido_id}")
            flash('Este producto no está configurado correctamente. Se reembolsó tu saldo.', 'error')
            return redirect(url_for('pedido_detalle', id=pedido_id))

        db.execute("UPDATE pedidos SET estado = 'pendiente' WHERE id = ?", (pedido_id,))
        db.commit()
        db.close()
        threading.Thread(
            target=procesar_pedido_deltaforce_background,
            args=(pedido_id, user_id, total, id_juego, paquete, cantidad),
            daemon=True,
        ).start()
        flash(f'Pedido #{pedido_id} en procesamiento. Revisa Mis pedidos para ver si fue aprobado o rechazado.', 'warning')
        return redirect(url_for('pedido_detalle', id=pedido_id))

    # Si el producto usa API PinCentral (PINs remotos) y no es giftcard de almacén
    elif (prod['usa_pincentral'] if 'usa_pincentral' in prod.keys() else 0) and prod['categoria_tipo'] != 'giftcards':
        product_code = str((prod['pincentral_product_code'] if 'pincentral_product_code' in prod.keys() else '') or '').strip()
        if not product_code:
            db.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
            db.commit()
            db.close()
            recargar_saldo(user_id, total, f"Reembolso: Código de producto PinCentral no configurado pedido #{pedido_id}")
            flash('Este producto no está configurado correctamente. Se reembolsó tu saldo.', 'error')
            return redirect(url_for('pedido_detalle', id=pedido_id))

        db.execute("UPDATE pedidos SET estado = 'pendiente' WHERE id = ?", (pedido_id,))
        db.commit()
        db.close()
        threading.Thread(
            target=procesar_pedido_pincentral_background,
            args=(pedido_id, user_id, total, product_code, cantidad),
            daemon=True,
        ).start()
        flash(f'Pedido #{pedido_id} recibido. Revisa Mis pedidos para ver cuándo se entregan los códigos.', 'warning')
        return redirect(url_for('pedido_detalle', id=pedido_id))

    # Si el producto usa API Hype Games (Free Fire), canjear PIN(es) automáticamente
    elif prod['usa_api'] and id_juego:
        from hype_api import canjear_pin_completo
        # Restock automático si el stock está bajo
        restock_pines(producto_id)
        try:
            num_canjes = prod['canjes_por_compra'] or 1
        except (IndexError, KeyError):
            num_canjes = 1
        monto_api = prod['monto_api']

        # Determinar de qué producto tomar los pines
        pin_producto_id = producto_id
        if num_canjes > 1:
            try:
                origen = prod['pin_origen_producto_id'] or 0
            except (IndexError, KeyError):
                origen = 0
            if origen > 0:
                pin_producto_id = origen
            else:
                base = db.execute(
                    "SELECT id FROM productos WHERE usa_api = 1 AND monto_api = ? AND canjes_por_compra = 1 AND id != ? LIMIT 1",
                    (monto_api, producto_id)
                ).fetchone()
                if base:
                    pin_producto_id = base['id']
            restock_pines(pin_producto_id)

        # Reservar N PINes atómicamente
        db.execute("BEGIN IMMEDIATE")
        pin_rows = db.execute(
            "SELECT * FROM pines WHERE producto_id = ? AND estado = 'disponible' ORDER BY fecha_agregado ASC LIMIT ?",
            (pin_producto_id, num_canjes)
        ).fetchall()

        if len(pin_rows) < num_canjes:
            db.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
            db.commit()
            db.close()
            recargar_saldo(user_id, total, f"Reembolso: Sin PINes suficientes pedido #{pedido_id} (necesarios: {num_canjes}, disponibles: {len(pin_rows)})")
            flash(f'No hay suficientes PINes para este producto ({len(pin_rows)}/{num_canjes}). Se reembolsó tu saldo.', 'error')
            return redirect(url_for('pedido_detalle', id=pedido_id))

        # Marcar todos los pines como usados
        pin_ids = []
        pin_codes = []
        for pr in pin_rows:
            pin_ids.append(pr['id'])
            pin_codes.append(decrypt_pin(pr['pin']))
            db.execute("UPDATE pines SET estado = 'usado', usado_por = ?, pedido_id = ?, fecha_usado = datetime('now','localtime') WHERE id = ?",
                       (user_id, pedido_id, pr['id']))
        db.commit()
        db.close()

        # Ejecutar canjes secuencialmente
        canjes_ok = 0
        nombre_jugador = ''
        error_msg = ''
        max_intentos_pin_error = 3
        for i, pin_code in enumerate(pin_codes):
            try:
                resultado_api = None
                for intento in range(1, max_intentos_pin_error + 1):
                    resultado_api = canjear_pin_completo(pin_code, id_juego, monto_api)
                    etapa_auditoria = f'canje_{i + 1}_try_{intento}'
                    _registrar_auditoria_recarga(
                        pedido_id=pedido_id,
                        usuario_id=user_id,
                        producto_id=producto_id,
                        proveedor='hype',
                        etapa=etapa_auditoria,
                        estado='ok' if resultado_api.get('ok') else 'error',
                        detalle=resultado_api.get('error', resultado_api.get('mensaje', '')),
                        referencia=str(resultado_api.get('reference', '') or ''),
                        payload=resultado_api,
                    )
                    if resultado_api.get('ok'):
                        break
                    should_retry_same_pin = bool(resultado_api.get('pin_error')) or bool(resultado_api.get('retry_same_pin'))
                    if not should_retry_same_pin:
                        break
                    if intento < max_intentos_pin_error:
                        continue

                if resultado_api.get('ok'):
                    canjes_ok += 1
                    nombre_jugador = resultado_api.get('username', '') or nombre_jugador
                else:
                    pin_error = bool(resultado_api.get('pin_error'))
                    paso_error = resultado_api.get('paso', 0)
                    if pin_error:
                        _marcar_pin_error_hype(
                            pin_id=pin_ids[i],
                            pedido_id=pedido_id,
                            id_juego=id_juego,
                            pin_code=pin_code,
                            motivo=f"{resultado_api.get('error', 'Error del proveedor Hype')} (tras {max_intentos_pin_error} intentos con el mismo PIN)",
                        )
                        error_msg = f"{resultado_api.get('error', 'Error en canje')} | Falló tras {max_intentos_pin_error} intentos con el mismo PIN"
                        break

                    db_fix = get_db()
                    if paso_error < 3:
                        db_fix.execute("UPDATE pines SET estado = 'disponible', usado_por = NULL, pedido_id = NULL, fecha_usado = NULL WHERE id = ?", (pin_ids[i],))
                    else:
                        db_fix.execute("UPDATE pines SET estado = 'error' WHERE id = ?", (pin_ids[i],))
                    db_fix.commit()
                    db_fix.close()
                    error_msg = resultado_api.get('error', 'Error en canje')
                    break
            except Exception as e:
                _registrar_auditoria_recarga(
                    pedido_id=pedido_id,
                    usuario_id=user_id,
                    producto_id=producto_id,
                    proveedor='hype',
                    etapa=f'canje_{i + 1}',
                    estado='exception',
                    detalle=str(e),
                    payload={'error': str(e)},
                )
                db_fix = get_db()
                db_fix.execute("UPDATE pines SET estado = 'disponible', usado_por = NULL, pedido_id = NULL, fecha_usado = NULL WHERE id = ?", (pin_ids[i],))
                db_fix.commit()
                db_fix.close()
                error_msg = str(e)
                break

        db3 = get_db()
        if canjes_ok == num_canjes:
            db3.execute("UPDATE pedidos SET estado = 'completado', nombre_jugador = ? WHERE id = ?", (nombre_jugador, pedido_id))
            db3.commit()
            db3.close()
            verificar_stock_bajo(pin_producto_id)
            flash(f'Pedido #{pedido_id} completado. {canjes_ok} recarga(s) aplicada(s) a {nombre_jugador} (ID: {id_juego}).', 'success')
            return redirect(url_for('pedido_detalle', id=pedido_id))
        elif canjes_ok > 0:
            # Parcialmente completado: no reembolsar lo que sí se canjeó
            monto_parcial = (total / num_canjes) * (num_canjes - canjes_ok)
            db3.execute("UPDATE pedidos SET estado = 'completado', nombre_jugador = ? WHERE id = ?",
                       (f"{nombre_jugador} (parcial {canjes_ok}/{num_canjes})", pedido_id))
            db3.commit()
            db3.close()
            # Devolver pines no canjeados
            db4 = get_db()
            for j in range(canjes_ok, len(pin_ids)):
                db4.execute("UPDATE pines SET estado = 'disponible', usado_por = NULL, pedido_id = NULL, fecha_usado = NULL WHERE id = ? AND estado = 'usado'", (pin_ids[j],))
            db4.commit()
            db4.close()
            recargar_saldo(user_id, monto_parcial, f"Reembolso parcial: {canjes_ok}/{num_canjes} canjes OK pedido #{pedido_id}")
            verificar_stock_bajo(pin_producto_id)
            flash(f'Pedido #{pedido_id}: {canjes_ok}/{num_canjes} recargas completadas. Se reembolsó ${monto_parcial:.4f} por las fallidas.', 'warning')
            return redirect(url_for('pedido_detalle', id=pedido_id))
        else:
            db3.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
            db3.commit()
            db3.close()
            # Devolver todos los pines
            db4 = get_db()
            for pid in pin_ids:
                db4.execute("UPDATE pines SET estado = 'disponible', usado_por = NULL, pedido_id = NULL, fecha_usado = NULL WHERE id = ? AND estado = 'usado'", (pid,))
            db4.commit()
            db4.close()
            recargar_saldo(user_id, total, f"Reembolso: Error canje pedido #{pedido_id}")
            flash(f'Error en canje automático: {error_msg}. Se reembolsó ${total:.4f} a tu cartera.', 'error')
            return redirect(url_for('pedido_detalle', id=pedido_id))

    # Producto de categoría Gift Card — verificar si tiene pines en almacén para entregar
    if prod['categoria_tipo'] == 'giftcards':
        cant_pines = min(cantidad, 50)
        pines_disponibles = db.execute("SELECT * FROM pines WHERE producto_id = ? AND estado = 'disponible' LIMIT ?", (producto_id, cant_pines)).fetchall()
        if len(pines_disponibles) >= cant_pines:
            codigos = []
            for pin_row in pines_disponibles:
                db.execute("UPDATE pines SET estado = 'usado', usado_por = ?, pedido_id = ?, fecha_usado = datetime('now','localtime') WHERE id = ?",
                           (user_id, pedido_id, pin_row['id']))
                codigos.append(decrypt_pin(pin_row['pin']))
            todos_codigos = '\n'.join(codigos)
            db.execute("UPDATE pedidos SET estado = 'completado', codigo_entregado = ? WHERE id = ?", (todos_codigos, pedido_id))
            db.commit()
            db.close()
            verificar_stock_bajo(producto_id)
            if (prod['usa_pincentral'] if 'usa_pincentral' in prod.keys() else 0):
                restock_pincentral_almacen_async(producto_id)
            if (prod['usa_jadh'] if 'usa_jadh' in prod.keys() else 0):
                restock_jadh_almacen_async(producto_id)
            flash(f'Pedido #{pedido_id} completado. {len(codigos)} código(s) entregado(s).', 'success')
            return redirect(url_for('pedido_detalle', id=pedido_id))
        else:
            if (prod['usa_pincentral'] if 'usa_pincentral' in prod.keys() else 0):
                restock_pincentral_almacen_async(producto_id)
            if (prod['usa_jadh'] if 'usa_jadh' in prod.keys() else 0):
                restock_jadh_almacen_async(producto_id)
            db.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
            db.commit()
            db.close()
            recargar_saldo(user_id, total, f"Reembolso: Sin stock gift card pedido #{pedido_id}")
            disponibles = len(pines_disponibles)
            flash(f'Stock insuficiente. Se necesitan {cant_pines} códigos pero solo hay {disponibles}. Se reembolsó tu saldo.', 'error')
            return redirect(url_for('pedido_detalle', id=pedido_id))

    db.close()
    flash(f'Pedido #{pedido_id} registrado. Se descontaron ${total:.4f} de tu cartera.', 'success')
    return redirect(url_for('pedido_detalle', id=pedido_id))


@app.route('/pedido/<int:id>')
@login_required
def pedido_detalle(id):
    db = get_db()
    pedido = db.execute("SELECT p.*, pr.nombre as producto_nombre FROM pedidos p JOIN productos pr ON p.producto_id = pr.id WHERE p.id = ? AND p.usuario_id = ?", (id, session['user_id'])).fetchone()
    if not pedido:
        db.close()
        flash('Pedido no encontrado', 'error')
        return redirect(url_for('mis_pedidos'))

    try:
        _sincronizar_pedido_moogold_si_pendiente(db, pedido)
        pedido = db.execute(
            "SELECT p.*, pr.nombre as producto_nombre FROM pedidos p JOIN productos pr ON p.producto_id = pr.id WHERE p.id = ? AND p.usuario_id = ?",
            (id, session['user_id']),
        ).fetchone()
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()

    return render_template('pedido.html', pedido=pedido)


@app.route('/mis-pines')
@login_required
def mis_pines():
    db = get_db()
    search_query = str(request.args.get('q', '') or '').strip()
    search_like = f"%{search_query.lower()}%"
    try:
        page = int(request.args.get('page', '1'))
    except (ValueError, TypeError):
        page = 1
    if page < 1:
        page = 1
    per_page = 20
    offset = (page - 1) * per_page

    where_clause = (
        "WHERE p.usuario_id = ? "
        "AND p.codigo_entregado IS NOT NULL AND p.codigo_entregado != '' "
        "AND c.tipo = 'giftcards'"
    )
    params = [session['user_id']]
    if search_query:
        where_clause += (
            " AND ("
            "CAST(p.id AS TEXT) LIKE ? OR "
            "LOWER(pr.nombre) LIKE ? OR "
            "LOWER(p.codigo_entregado) LIKE ? OR "
            "LOWER(COALESCE(p.estado, '')) LIKE ? OR "
            "LOWER(COALESCE(p.fecha_pedido, '')) LIKE ?"
            ")"
        )
        params.extend([search_like, search_like, search_like, search_like, search_like])

    total_pines = db.execute(
        "SELECT COUNT(*) as c FROM pedidos p "
        "JOIN productos pr ON p.producto_id = pr.id "
        "JOIN categorias c ON pr.categoria_id = c.id "
        + where_clause,
        tuple(params),
    ).fetchone()['c']

    pines_params = list(params)
    pines_params.extend([per_page, offset])
    pines = db.execute(
        "SELECT p.id as pedido_id, p.codigo_entregado, p.cantidad, p.total, p.estado, p.fecha_pedido, pr.nombre as producto_nombre "
        "FROM pedidos p JOIN productos pr ON p.producto_id = pr.id "
        "JOIN categorias c ON pr.categoria_id = c.id "
        + where_clause +
        " ORDER BY p.fecha_pedido DESC LIMIT ? OFFSET ?",
        tuple(pines_params),
    ).fetchall()
    has_prev = page > 1
    has_more = (offset + len(pines)) < total_pines
    db.close()
    return render_template(
        'mis_pines.html',
        pines=pines,
        page=page,
        per_page=per_page,
        total_pines=total_pines,
        has_prev=has_prev,
        has_more=has_more,
        search_query=search_query,
    )


@app.route('/mis-pedidos')
@login_required
def mis_pedidos():
    db = get_db()
    search_query = str(request.args.get('q', '') or '').strip()
    search_like = f"%{search_query.lower()}%"
    try:
        page = int(request.args.get('page', '1'))
    except (ValueError, TypeError):
        page = 1
    if page < 1:
        page = 1
    per_page = 20
    offset = (page - 1) * per_page

    where_clause = (
        "WHERE p.usuario_id = ? AND c.tipo != 'giftcards'"
    )
    params = [session['user_id']]
    if search_query:
        where_clause += (
            " AND ("
            "CAST(p.id AS TEXT) LIKE ? OR "
            "LOWER(pr.nombre) LIKE ? OR "
            "LOWER(COALESCE(p.id_juego, '')) LIKE ? OR "
            "LOWER(COALESCE(p.estado, '')) LIKE ? OR "
            "LOWER(COALESCE(p.fecha_pedido, '')) LIKE ?"
            ")"
        )
        params.extend([search_like, search_like, search_like, search_like, search_like])

    total_pedidos = db.execute(
        "SELECT COUNT(*) as c FROM pedidos p "
        "JOIN productos pr ON p.producto_id = pr.id "
        "JOIN categorias c ON pr.categoria_id = c.id "
        + where_clause,
        tuple(params)
    ).fetchone()['c']

    pedidos_params = list(params)
    pedidos_params.extend([per_page, offset])
    pedidos = db.execute(
        "SELECT p.*, pr.nombre as producto_nombre FROM pedidos p "
        "JOIN productos pr ON p.producto_id = pr.id "
        "JOIN categorias c ON pr.categoria_id = c.id "
        + where_clause +
        " ORDER BY p.fecha_pedido DESC LIMIT ? OFFSET ?",
        tuple(pedidos_params)
    ).fetchall()
    has_prev = page > 1
    has_more = (offset + len(pedidos)) < total_pedidos
    db.close()
    return render_template(
        'mis_pedidos.html',
        pedidos=pedidos,
        page=page,
        per_page=per_page,
        total_pedidos=total_pedidos,
        has_prev=has_prev,
        has_more=has_more,
        search_query=search_query,
    )


# ===== ESTADÍSTICAS USUARIO =====
@app.route('/estadisticas')
@login_required
def estadisticas():
    from datetime import datetime, timedelta
    uid = session['user_id']
    fecha_desde = request.args.get('desde', '')
    fecha_hasta = request.args.get('hasta', '')
    # Default: hoy
    if not fecha_desde:
        fecha_desde = datetime.now().strftime('%Y-%m-%d')
    if not fecha_hasta:
        fecha_hasta = datetime.now().strftime('%Y-%m-%d')

    db = get_db()
    # Stats generales en el rango
    stats = db.execute(
        "SELECT COUNT(*) as total_pedidos, "
        "COALESCE(SUM(CASE WHEN estado='completado' THEN total ELSE 0 END), 0) as total_gastado, "
        "COALESCE(SUM(CASE WHEN estado='completado' THEN 1 ELSE 0 END), 0) as completados, "
        "COALESCE(SUM(CASE WHEN estado='cancelado' THEN 1 ELSE 0 END), 0) as cancelados, "
        "COALESCE(SUM(CASE WHEN estado='procesando' THEN 1 ELSE 0 END), 0) as procesando "
        "FROM pedidos WHERE usuario_id = ? AND date(fecha_pedido) >= ? AND date(fecha_pedido) <= ?",
        (uid, fecha_desde, fecha_hasta)
    ).fetchone()

    # Stats del día de hoy
    hoy = datetime.now().strftime('%Y-%m-%d')
    stats_hoy = db.execute(
        "SELECT COUNT(*) as total, "
        "COALESCE(SUM(CASE WHEN estado='completado' THEN total ELSE 0 END), 0) as gastado "
        "FROM pedidos WHERE usuario_id = ? AND date(fecha_pedido) = ?",
        (uid, hoy)
    ).fetchone()

    # Productos más comprados en el rango
    top_productos = db.execute(
        "SELECT pr.nombre, COUNT(*) as veces, SUM(p.total) as total_gastado "
        "FROM pedidos p JOIN productos pr ON p.producto_id = pr.id "
        "WHERE p.usuario_id = ? AND p.estado = 'completado' "
        "AND date(p.fecha_pedido) >= ? AND date(p.fecha_pedido) <= ? "
        "GROUP BY pr.id ORDER BY veces DESC LIMIT 10",
        (uid, fecha_desde, fecha_hasta)
    ).fetchall()

    # Ventas por día en el rango (para gráfico)
    ventas_diarias = db.execute(
        "SELECT date(fecha_pedido) as dia, COUNT(*) as cantidad, "
        "COALESCE(SUM(CASE WHEN estado='completado' THEN total ELSE 0 END), 0) as monto "
        "FROM pedidos WHERE usuario_id = ? "
        "AND date(fecha_pedido) >= ? AND date(fecha_pedido) <= ? "
        "GROUP BY date(fecha_pedido) ORDER BY dia",
        (uid, fecha_desde, fecha_hasta)
    ).fetchall()

    # Últimos pedidos en el rango
    ultimos = db.execute(
        "SELECT p.*, pr.nombre as producto_nombre FROM pedidos p "
        "JOIN productos pr ON p.producto_id = pr.id "
        "WHERE p.usuario_id = ? AND date(p.fecha_pedido) >= ? AND date(p.fecha_pedido) <= ? "
        "ORDER BY p.fecha_pedido DESC LIMIT 20",
        (uid, fecha_desde, fecha_hasta)
    ).fetchall()

    db.close()
    return render_template('estadisticas.html',
        stats=stats, stats_hoy=stats_hoy, top_productos=top_productos,
        ventas_diarias=ventas_diarias, ultimos=ultimos,
        fecha_desde=fecha_desde, fecha_hasta=fecha_hasta)


# ===== PERFIL =====
@app.route('/perfil', methods=['GET', 'POST'])
@login_required
def perfil():
    db = get_db()
    user = db.execute("SELECT * FROM usuarios WHERE id = ?", (session['user_id'],)).fetchone()
    if request.method == 'POST':
        accion = request.form.get('accion')
        if accion == 'datos':
            nombre = request.form.get('nombre', '').strip()
            email = request.form.get('email', '').strip()
            telefono = request.form.get('telefono', '').strip()
            if nombre and email:
                try:
                    db.execute("UPDATE usuarios SET nombre=?, email=?, telefono=? WHERE id=?",
                               (nombre, email, telefono, session['user_id']))
                    db.commit()
                    flash('Datos actualizados correctamente', 'success')
                except Exception:
                    flash('Error: el email ya está en uso', 'error')
        elif accion == 'password':
            actual = request.form.get('password_actual', '')
            nueva = request.form.get('password_nueva', '')
            confirmar = request.form.get('password_confirmar', '')
            if not check_password_hash(user['password'], actual):
                flash('La contraseña actual es incorrecta', 'error')
            elif len(nueva) < 6:
                flash('La nueva contraseña debe tener al menos 6 caracteres', 'error')
            elif nueva != confirmar:
                flash('Las contraseñas no coinciden', 'error')
            else:
                db.execute("UPDATE usuarios SET password=? WHERE id=?",
                           (generate_password_hash(nueva), session['user_id']))
                db.commit()
                flash('Contraseña cambiada correctamente', 'success')
        elif accion == 'suscripcion':
            precio_cfg = _config_get(db, 'suscripcion_mensual_precio', '0').replace(',', '.').strip()
            try:
                precio_mensual = float(precio_cfg)
            except (TypeError, ValueError):
                precio_mensual = 0.0
            if precio_mensual <= 0:
                flash('La suscripción mensual no está disponible en este momento.', 'error')
            else:
                db.close()
                descuento = descontar_saldo(session['user_id'], precio_mensual, 'Pago suscripción mensual (30 días)')
                if descuento is None:
                    flash(f'Saldo insuficiente para la suscripción. Precio actual: ${precio_mensual:.4f}', 'error')
                    return redirect(url_for('perfil'))

                db2 = get_db()
                user_sub = db2.execute("SELECT suscripcion_hasta FROM usuarios WHERE id = ?", (session['user_id'],)).fetchone()
                ahora = datetime.now()
                actual_hasta = _parse_local_datetime(user_sub['suscripcion_hasta'] if user_sub and 'suscripcion_hasta' in user_sub.keys() else '')
                base = actual_hasta if actual_hasta and actual_hasta > ahora else ahora
                nuevo_hasta = base + timedelta(days=30)
                db2.execute(
                    "UPDATE usuarios SET suscripcion_hasta = ? WHERE id = ?",
                    (nuevo_hasta.strftime('%Y-%m-%d %H:%M:%S'), session['user_id'])
                )
                db2.commit()
                db2.close()
                flash(f'Suscripción mensual activada hasta {nuevo_hasta.strftime("%Y-%m-%d %H:%M")}.', 'success')
                return redirect(url_for('perfil'))
        db.close()
        return redirect(url_for('perfil'))
    saldo = get_saldo(session['user_id'])
    suscripcion_activa = _suscripcion_activa_desde_row(user)
    suscripcion_hasta = user['suscripcion_hasta'] if user and 'suscripcion_hasta' in user.keys() else ''
    precio_cfg = _config_get(db, 'suscripcion_mensual_precio', '0').replace(',', '.').strip()
    try:
        suscripcion_mensual_precio = float(precio_cfg)
    except (TypeError, ValueError):
        suscripcion_mensual_precio = 0.0
    db.close()
    return render_template(
        'perfil.html',
        user=user,
        saldo=saldo,
        suscripcion_activa=suscripcion_activa,
        suscripcion_hasta=suscripcion_hasta,
        suscripcion_mensual_precio=suscripcion_mensual_precio,
    )


# ===== CARTERA =====
@app.route('/cartera', methods=['GET', 'POST'])
@login_required
def cartera():
    if request.method == 'POST':
        db = get_db()
        user = db.execute("SELECT id, suscripcion_hasta, autorenovar_suscripcion FROM usuarios WHERE id = ?", (session['user_id'],)).fetchone()
        accion = request.form.get('accion', '').strip()
        if accion == 'suscripcion':
            precio_cfg = _config_get(db, 'suscripcion_mensual_precio', '0').replace(',', '.').strip()
            try:
                precio_mensual = float(precio_cfg)
            except (TypeError, ValueError):
                precio_mensual = 0.0

            if precio_mensual <= 0:
                flash('La suscripción mensual no está disponible en este momento.', 'error')
            else:
                db.close()
                descuento = descontar_saldo(session['user_id'], precio_mensual, 'Pago suscripción mensual (30 días)')
                if descuento is None:
                    flash(f'Saldo insuficiente para la suscripción. Precio actual: ${precio_mensual:.4f}', 'error')
                    return redirect(url_for('cartera'))

                db2 = get_db()
                user_sub = db2.execute("SELECT suscripcion_hasta FROM usuarios WHERE id = ?", (session['user_id'],)).fetchone()
                ahora = datetime.now()
                actual_hasta = _parse_local_datetime(user_sub['suscripcion_hasta'] if user_sub and 'suscripcion_hasta' in user_sub.keys() else '')
                base = actual_hasta if actual_hasta and actual_hasta > ahora else ahora
                nuevo_hasta = base + timedelta(days=30)
                db2.execute(
                    "UPDATE usuarios SET suscripcion_hasta = ? WHERE id = ?",
                    (nuevo_hasta.strftime('%Y-%m-%d %H:%M:%S'), session['user_id'])
                )
                db2.commit()
                db2.close()
                flash(f'Suscripción mensual activada hasta {nuevo_hasta.strftime("%Y-%m-%d %H:%M")}.', 'success')
                return redirect(url_for('cartera'))
        elif accion == 'toggle_autorenovacion':
            db.execute(
                "UPDATE usuarios SET autorenovar_suscripcion = ? WHERE id = ?",
                (0, session['user_id'])
            )
            db.commit()
            flash('Autorenovación desactivada.', 'warning')

        db.close()
        return redirect(url_for('cartera'))

    auto_result = {'accion': 'deshabilitada'}

    db = get_db()
    user = db.execute("SELECT id, suscripcion_hasta, autorenovar_suscripcion FROM usuarios WHERE id = ?", (session['user_id'],)).fetchone()
    saldo = get_saldo(session['user_id'])
    transacciones = db.execute("SELECT t.*, u.nombre as admin_nombre FROM transacciones t LEFT JOIN usuarios u ON t.admin_id = u.id WHERE t.usuario_id = ? ORDER BY t.fecha DESC LIMIT 50", (session['user_id'],)).fetchall()
    suscripcion_activa = _suscripcion_activa_desde_row(user)
    autorenovacion_activa = _bool_autorenovar_desde_row(user)
    suscripcion_hasta = user['suscripcion_hasta'] if user and 'suscripcion_hasta' in user.keys() else ''
    precio_cfg = _config_get(db, 'suscripcion_mensual_precio', '0').replace(',', '.').strip()
    try:
        suscripcion_mensual_precio = float(precio_cfg)
    except (TypeError, ValueError):
        suscripcion_mensual_precio = 0.0
    db.close()
    return render_template(
        'cartera.html',
        saldo=saldo,
        transacciones=transacciones,
        suscripcion_activa=suscripcion_activa,
        autorenovacion_activa=autorenovacion_activa,
        suscripcion_hasta=suscripcion_hasta,
        suscripcion_mensual_precio=suscripcion_mensual_precio,
        autorenovacion_saldo_insuficiente=False,
    )


# ===== SOLICITAR RECARGA =====
@app.route('/solicitar-recarga', methods=['GET', 'POST'])
@login_required
def solicitar_recarga():
    if request.method == 'POST':
        monto = request.form.get('monto', '0')
        metodo_pago = request.form.get('metodo_pago', '').strip()
        referencia = request.form.get('referencia', '').strip()
        try:
            monto = float(monto)
        except (ValueError, TypeError):
            monto = 0
        db = get_db()
        if monto <= 0:
            db.close()
            flash('El monto debe ser mayor a 0', 'error')
            return redirect(url_for('solicitar_recarga'))
        if not metodo_pago:
            db.close()
            flash('Selecciona un método de pago', 'error')
            return redirect(url_for('solicitar_recarga'))
        if _es_binance(metodo_pago):
            ref_digits = _extraer_digitos(referencia)
            if len(ref_digits) < 8:
                db.close()
                flash('Para Binance debes ingresar una referencia con al menos 8 dígitos.', 'error')
                return redirect(url_for('solicitar_recarga'))
        # Verificar que no tenga otra solicitud pendiente
        pendiente = db.execute("SELECT id FROM solicitudes_recarga WHERE usuario_id = ? AND estado = 'pendiente'", (session['user_id'],)).fetchone()
        if pendiente:
            db.close()
            flash('Ya tienes una solicitud de recarga pendiente. Espera a que sea procesada.', 'error')
            return redirect(url_for('solicitar_recarga'))
        cur = db.execute("INSERT INTO solicitudes_recarga (usuario_id, monto, metodo_pago, referencia) VALUES (?,?,?,?)",
                         (session['user_id'], monto, metodo_pago, referencia))
        solicitud_id = cur.lastrowid
        db.commit()

        # Binance se procesa automáticamente: match exacto -> aprobar, sin match -> rechazar
        if _es_binance(metodo_pago):
            sol = db.execute("SELECT * FROM solicitudes_recarga WHERE id = ?", (solicitud_id,)).fetchone()
            verif = _verificar_pago_binance_solicitud(db, sol)
            if verif.get('ok'):
                # Liberar escritura pendiente antes de abrir otra conexión en recargar_saldo
                db.commit()
                monto_base = float(sol['monto'])
                user_sol = db.execute("SELECT suscripcion_hasta FROM usuarios WHERE id = ?", (sol['usuario_id'],)).fetchone()
                suscripcion_activa = _suscripcion_activa_desde_row(user_sol)
                if suscripcion_activa:
                    bonus_pct = 0
                else:
                    bonus_row = db.execute(
                        "SELECT porcentaje_bonus FROM bonus_recarga WHERE activo = 1 AND monto_minimo <= ? ORDER BY monto_minimo DESC LIMIT 1",
                        (monto_base,)
                    ).fetchone()
                    bonus_pct = bonus_row['porcentaje_bonus'] if bonus_row else 0
                monto_bonus = round(monto_base * bonus_pct / 100, 4) if bonus_pct != 0 else 0
                monto_total = monto_base + monto_bonus

                desc_recarga = f"Recarga auto-aprobada Binance (solicitud #{solicitud_id})"
                if monto_bonus != 0:
                    accion = 'Bonus' if monto_bonus > 0 else 'Descuento'
                    desc_recarga += f" + {accion} {bonus_pct}% (${monto_bonus:.4f})"
                recargar_saldo(sol['usuario_id'], monto_total, desc_recarga)

                match = verif.get('match', {})
                nota_auto = f"Aprobada automáticamente por API Binance. Ref: {match.get('referencia_full', '')}"
                db.execute(
                    "UPDATE solicitudes_recarga SET estado = 'aprobada', nota_admin = ?, fecha_respuesta = datetime('now','localtime') WHERE id = ?",
                    (nota_auto, solicitud_id),
                )
                db.commit()
                db.close()

                msg = f'Pago Binance verificado automáticamente. Recarga aplicada por ${monto_base:.4f}'
                if monto_bonus != 0:
                    accion = 'Bonus' if monto_bonus > 0 else 'Descuento'
                    msg += f' + {accion} {bonus_pct}% (${monto_bonus:.4f}) = ${monto_total:.4f}'
                flash(msg, 'success')
                return redirect(url_for('solicitar_recarga'))

            nota_auto = f"Rechazada automáticamente: {verif.get('error', 'No coincide con movimientos Binance')}"
            db.execute(
                "UPDATE solicitudes_recarga SET estado = 'rechazada', nota_admin = ?, fecha_respuesta = datetime('now','localtime') WHERE id = ?",
                (nota_auto, solicitud_id),
            )
            db.commit()
            db.close()
            flash(f'Pago Binance rechazado automáticamente: {verif.get("error", "No coincide")}', 'error')
            return redirect(url_for('solicitar_recarga'))

        db.commit()
        usuario = db.execute("SELECT nombre FROM usuarios WHERE id = ?", (session['user_id'],)).fetchone()
        db.close()
        notificar_recarga(usuario['nombre'] if usuario else 'Desconocido', monto, metodo_pago, referencia)
        flash(f'Solicitud de recarga por ${monto:.2f} enviada. El admin la revisará pronto.', 'success')
        return redirect(url_for('solicitar_recarga'))
    db = get_db()
    solicitudes = db.execute("SELECT * FROM solicitudes_recarga WHERE usuario_id = ? ORDER BY fecha_solicitud DESC LIMIT 20", (session['user_id'],)).fetchall()
    saldo = get_saldo(session['user_id'])
    user_sub = db.execute("SELECT suscripcion_hasta FROM usuarios WHERE id = ?", (session['user_id'],)).fetchone()
    suscripcion_activa = _suscripcion_activa_desde_row(user_sub)
    # Cargar config completa
    config_rows = db.execute("SELECT clave, valor FROM configuracion").fetchall()
    config = {r['clave']: r['valor'] for r in config_rows}
    recarga_minima = config.get('recarga_minima', '0')
    # Cargar bonuses activos
    if suscripcion_activa:
        bonuses = []
    else:
        bonuses = db.execute("SELECT monto_minimo, porcentaje_bonus FROM bonus_recarga WHERE activo = 1 ORDER BY monto_minimo ASC").fetchall()
    db.close()
    # Armar lista de métodos activos
    metodos = []
    for key in ['pago_movil', 'binance', 'zinli', 'zelle']:
        if config.get(f'metodo_{key}_activo') == '1':
            metodos.append({
                'id': key,
                'nombre': config.get(f'metodo_{key}_nombre', key),
                'datos': config.get(f'metodo_{key}_datos', ''),
                'nota': config.get(f'metodo_{key}_nota', ''),
            })
    bonuses_list = [{'monto_minimo': b['monto_minimo'], 'porcentaje_bonus': b['porcentaje_bonus']} for b in bonuses]
    solicitudes_view = []
    for s in solicitudes:
        d = dict(s)
        monto_s = float(d.get('monto', 0) or 0)
        bonus_pct = 0
        if d.get('estado') == 'aprobada' and not suscripcion_activa:
            for tier in bonuses_list:
                if monto_s >= float(tier.get('monto_minimo', 0) or 0):
                    bonus_pct = float(tier.get('porcentaje_bonus', 0) or 0)
        bonus_monto = round(monto_s * bonus_pct / 100, 4) if bonus_pct != 0 else 0
        d['bonus_pct'] = bonus_pct
        d['bonus_monto'] = bonus_monto
        d['monto_total'] = round(monto_s + bonus_monto, 4)
        solicitudes_view.append(d)

    return render_template('solicitar_recarga.html', solicitudes=solicitudes_view, saldo=saldo, metodos=metodos, bonuses=bonuses_list, recarga_minima=recarga_minima, suscripcion_activa=suscripcion_activa)


# ===== ADMIN =====
@app.route('/admin')
@admin_required
def admin_panel():
    db = get_db()
    total_users = db.execute("SELECT COUNT(*) as c FROM usuarios").fetchone()['c']
    total_pedidos = db.execute("SELECT COUNT(*) as c FROM pedidos").fetchone()['c']
    total_ventas = db.execute("SELECT COALESCE(SUM(total), 0) as c FROM pedidos WHERE estado = 'completado'").fetchone()['c']
    total_pendientes = db.execute("SELECT COUNT(*) as c FROM pedidos WHERE estado = 'pendiente'").fetchone()['c']
    ultimos_pedidos = db.execute("SELECT p.*, u.nombre as usuario_nombre, pr.nombre as producto_nombre FROM pedidos p JOIN usuarios u ON p.usuario_id = u.id JOIN productos pr ON p.producto_id = pr.id ORDER BY p.fecha_pedido DESC LIMIT 10").fetchall()
    solicitudes_pendientes = db.execute("SELECT COUNT(*) as c FROM solicitudes_recarga WHERE estado = 'pendiente'").fetchone()['c']

    ultimo_refresh_cron = db.execute(
        "SELECT id, fecha, total_cambios FROM precios_refresh_runs "
        "WHERE origen = 'cron_15m' ORDER BY id DESC LIMIT 1"
    ).fetchone()

    refresh_countdown_seconds = None
    refresh_atrasado = False
    refresh_countdown_estimado = False
    ultimo_refresh_cron_fecha = None
    if ultimo_refresh_cron:
        ultimo_refresh_cron_fecha = str(ultimo_refresh_cron['fecha'] or '').strip()
        try:
            fecha_ultimo = datetime.strptime(ultimo_refresh_cron_fecha, '%Y-%m-%d %H:%M:%S')
            fecha_proximo = fecha_ultimo + timedelta(minutes=15)
            delta_segundos = int((fecha_proximo - datetime.now()).total_seconds())
            refresh_atrasado = delta_segundos < 0
            refresh_countdown_seconds = max(delta_segundos, 0)
        except Exception:
            refresh_countdown_seconds = None

    if refresh_countdown_seconds is None:
        ahora = datetime.now()
        base_minuto = ahora.replace(second=0, microsecond=0)
        minutos_faltantes = 15 - (base_minuto.minute % 15)
        proximo_estimado = base_minuto + timedelta(minutes=minutos_faltantes)
        if proximo_estimado <= ahora:
            proximo_estimado = proximo_estimado + timedelta(minutes=15)
        refresh_countdown_seconds = max(int((proximo_estimado - ahora).total_seconds()), 0)
        refresh_countdown_estimado = True

    db.close()
    return render_template(
        'admin/panel.html',
        total_users=total_users,
        total_pedidos=total_pedidos,
        total_ventas=total_ventas,
        total_pendientes=total_pendientes,
        ultimos_pedidos=ultimos_pedidos,
        solicitudes_pendientes=solicitudes_pendientes,
        ultimo_refresh_cron=ultimo_refresh_cron,
        ultimo_refresh_cron_fecha=ultimo_refresh_cron_fecha,
        refresh_countdown_seconds=refresh_countdown_seconds,
        refresh_atrasado=refresh_atrasado,
        refresh_countdown_estimado=refresh_countdown_estimado,
    )


@app.route('/admin/precios-refresh')
@admin_required
def admin_reporte_refresh_precios():
    refresh_id = int(request.args.get('refresh_id', 0) or 0)
    db = get_db()

    if refresh_id > 0:
        run = db.execute("SELECT * FROM precios_refresh_runs WHERE id = ?", (refresh_id,)).fetchone()
    else:
        run = db.execute("SELECT * FROM precios_refresh_runs ORDER BY id DESC LIMIT 1").fetchone()

    if not run:
        db.close()
        flash('No hay ejecuciones de refresco de precios todavía.', 'warning')
        return redirect(url_for('admin_panel'))

    cambios = db.execute(
        "SELECT * FROM precios_refresh_cambios WHERE run_id = ? "
        "ORDER BY proveedor, categoria_nombre, producto_nombre, campo",
        (run['id'],)
    ).fetchall()
    db.close()

    detalles = {}
    try:
        detalles = json.loads(run['detalles_json'] or '{}')
        if not isinstance(detalles, dict):
            detalles = {}
    except Exception:
        detalles = {}

    return render_template('admin/precios_refresh.html', run=run, cambios=cambios, detalles=detalles)


def _iniciar_refresh_precios_async(origen='admin_manual_async'):
    """Ejecuta refresh de precios en segundo plano para evitar timeouts HTTP."""

    def _worker():
        db = get_db()
        try:
            result = _ejecutar_refresh_precios_proveedores(db, origen=origen)
            if result.get('ok'):
                db.commit()
            else:
                db.rollback()
                print(f"[REFRESH_PRECIOS] Falló refresh async: {result.get('error', 'sin detalle')}")
        except Exception as e:
            db.rollback()
            print(f"[REFRESH_PRECIOS] Error en refresh async: {e}")
        finally:
            db.close()

    threading.Thread(target=_worker, daemon=True).start()


@app.route('/admin/precios-refresh/ejecutar', methods=['POST'])
@admin_required
def admin_ejecutar_refresh_precios():
    try:
        _iniciar_refresh_precios_async(origen='admin_manual')
        flash('Refresh de precios iniciado en segundo plano. Revisa el reporte en 1-2 minutos.', 'success')
    except Exception as e:
        flash(f"Error al iniciar refresh de precios: {e}", 'error')
    return redirect(url_for('admin_panel'))


@app.route('/admin/estadisticas')
@admin_required
def admin_estadisticas():
    from datetime import datetime, timedelta
    fecha_desde = request.args.get('desde', '')
    fecha_hasta = request.args.get('hasta', '')
    if not fecha_desde:
        fecha_desde = datetime.now().strftime('%Y-%m-%d')
    if not fecha_hasta:
        fecha_hasta = datetime.now().strftime('%Y-%m-%d')

    db = get_db()
    hoy = datetime.now().strftime('%Y-%m-%d')

    # Stats del rango
    stats = db.execute(
        "SELECT COUNT(*) as total_pedidos, "
        "COALESCE(SUM(CASE WHEN estado='completado' THEN total ELSE 0 END), 0) as total_ventas, "
        "COALESCE(SUM(CASE WHEN estado='completado' THEN 1 ELSE 0 END), 0) as completados, "
        "COALESCE(SUM(CASE WHEN estado='cancelado' THEN 1 ELSE 0 END), 0) as cancelados, "
        "COALESCE(SUM(CASE WHEN estado='procesando' THEN 1 ELSE 0 END), 0) as procesando, "
        "COUNT(DISTINCT usuario_id) as usuarios_activos "
        "FROM pedidos WHERE date(fecha_pedido) >= ? AND date(fecha_pedido) <= ?",
        (fecha_desde, fecha_hasta)
    ).fetchone()

    # Stats de hoy
    stats_hoy = db.execute(
        "SELECT COUNT(*) as total, "
        "COALESCE(SUM(CASE WHEN estado='completado' THEN total ELSE 0 END), 0) as ventas, "
        "COUNT(DISTINCT usuario_id) as usuarios "
        "FROM pedidos WHERE date(fecha_pedido) = ?",
        (hoy,)
    ).fetchone()

    # Todos los productos vendidos del rango
    top_productos = db.execute(
        "SELECT pr.nombre, COUNT(*) as veces, SUM(p.total) as total_vendido "
        "FROM pedidos p JOIN productos pr ON p.producto_id = pr.id "
        "WHERE p.estado = 'completado' "
        "AND date(p.fecha_pedido) >= ? AND date(p.fecha_pedido) <= ? "
        "GROUP BY pr.id ORDER BY veces DESC",
        (fecha_desde, fecha_hasta)
    ).fetchall()

    # Top usuarios del rango
    top_usuarios = db.execute(
        "SELECT u.nombre, u.email, COUNT(*) as pedidos, "
        "COALESCE(SUM(CASE WHEN p.estado='completado' THEN p.total ELSE 0 END), 0) as total_gastado "
        "FROM pedidos p JOIN usuarios u ON p.usuario_id = u.id "
        "WHERE date(p.fecha_pedido) >= ? AND date(p.fecha_pedido) <= ? "
        "GROUP BY u.id ORDER BY total_gastado DESC LIMIT 10",
        (fecha_desde, fecha_hasta)
    ).fetchall()

    # Ventas por día
    ventas_diarias = db.execute(
        "SELECT date(fecha_pedido) as dia, COUNT(*) as cantidad, "
        "COALESCE(SUM(CASE WHEN estado='completado' THEN total ELSE 0 END), 0) as monto "
        "FROM pedidos WHERE date(fecha_pedido) >= ? AND date(fecha_pedido) <= ? "
        "GROUP BY date(fecha_pedido) ORDER BY dia",
        (fecha_desde, fecha_hasta)
    ).fetchall()

    # Recargas de saldo en el rango
    total_recargas = db.execute(
        "SELECT COUNT(*) as cantidad, COALESCE(SUM(monto), 0) as total "
        "FROM transacciones WHERE tipo = 'recarga' "
        "AND date(fecha) >= ? AND date(fecha) <= ?",
        (fecha_desde, fecha_hasta)
    ).fetchone()

    db.close()
    return render_template('admin/estadisticas.html',
        stats=stats, stats_hoy=stats_hoy, top_productos=top_productos,
        top_usuarios=top_usuarios, ventas_diarias=ventas_diarias,
        total_recargas=total_recargas,
        fecha_desde=fecha_desde, fecha_hasta=fecha_hasta)


@app.route('/admin/solicitudes')
@admin_required
def admin_solicitudes():
    db = get_db()
    try:
        page = int(request.args.get('page', '1'))
    except (ValueError, TypeError):
        page = 1
    if page < 1:
        page = 1
    per_page = 20
    offset = (page - 1) * per_page

    total_solicitudes = db.execute("SELECT COUNT(*) as c FROM solicitudes_recarga").fetchone()['c']
    solicitudes = db.execute(
        "SELECT s.*, u.nombre as usuario_nombre, u.email as usuario_email "
        "FROM solicitudes_recarga s JOIN usuarios u ON s.usuario_id = u.id "
        "ORDER BY CASE s.estado WHEN 'pendiente' THEN 0 ELSE 1 END, s.fecha_solicitud DESC "
        "LIMIT ? OFFSET ?",
        (per_page, offset)
    ).fetchall()
    has_prev = page > 1
    has_more = (offset + len(solicitudes)) < total_solicitudes
    db.close()
    return render_template(
        'admin/solicitudes.html',
        solicitudes=solicitudes,
        page=page,
        per_page=per_page,
        total_solicitudes=total_solicitudes,
        has_prev=has_prev,
        has_more=has_more,
    )


@app.route('/admin/solicitud/<int:id>/aprobar', methods=['POST'])
@admin_required
def admin_aprobar_solicitud(id):
    db = get_db()
    sol = db.execute("SELECT * FROM solicitudes_recarga WHERE id = ? AND estado = 'pendiente'", (id,)).fetchone()
    if not sol:
        db.close()
        flash('Solicitud no encontrada o ya procesada', 'error')
        return redirect(url_for('admin_solicitudes'))
    nota = request.form.get('nota', '').strip()
    if _es_binance(sol['metodo_pago']):
        verif = _verificar_pago_binance_solicitud(db, sol)
        if not verif.get('ok'):
            db.close()
            flash(f"No se pudo aprobar solicitud #{id}: {verif.get('error')}", 'error')
            return redirect(url_for('admin_solicitudes'))
        match = verif.get('match', {})
        if match:
            extra = f" [Binance OK ref: {match.get('referencia_full', '')}]"
            nota = (nota + extra).strip()

    monto_base = sol['monto']
    # Calcular bonus si aplica
    user_sol = db.execute("SELECT suscripcion_hasta FROM usuarios WHERE id = ?", (sol['usuario_id'],)).fetchone()
    suscripcion_activa = _suscripcion_activa_desde_row(user_sol)
    if suscripcion_activa:
        bonus_pct = 0
    else:
        bonus_row = db.execute(
            "SELECT porcentaje_bonus FROM bonus_recarga WHERE activo = 1 AND monto_minimo <= ? ORDER BY monto_minimo DESC LIMIT 1",
            (monto_base,)
        ).fetchone()
        bonus_pct = bonus_row['porcentaje_bonus'] if bonus_row else 0
    monto_bonus = round(monto_base * bonus_pct / 100, 4) if bonus_pct != 0 else 0
    monto_total = monto_base + monto_bonus
    # Aplicar la recarga al saldo del usuario
    desc_recarga = f"Recarga aprobada (solicitud #{id}) - {sol['metodo_pago']}"
    if monto_bonus != 0:
        accion = 'Bonus' if monto_bonus > 0 else 'Descuento'
        desc_recarga += f" + {accion} {bonus_pct}% (${monto_bonus:.4f})"
    recargar_saldo(sol['usuario_id'], monto_total, desc_recarga, admin_id=session['user_id'])
    db.execute("UPDATE solicitudes_recarga SET estado = 'aprobada', admin_id = ?, nota_admin = ?, fecha_respuesta = datetime('now','localtime') WHERE id = ?",
               (session['user_id'], nota, id))
    db.commit()
    db.close()
    msg = f'Solicitud #{id} aprobada. Recarga: ${monto_base:.4f}'
    if monto_bonus != 0:
        accion = 'Bonus' if monto_bonus > 0 else 'Descuento'
        msg += f' + {accion} {bonus_pct}% (${monto_bonus:.4f}) = ${monto_total:.4f}'
    flash(msg, 'success')
    return redirect(url_for('admin_solicitudes'))


@app.route('/admin/solicitud/<int:id>/rechazar', methods=['POST'])
@admin_required
def admin_rechazar_solicitud(id):
    db = get_db()
    sol = db.execute("SELECT * FROM solicitudes_recarga WHERE id = ? AND estado = 'pendiente'", (id,)).fetchone()
    if not sol:
        db.close()
        flash('Solicitud no encontrada o ya procesada', 'error')
        return redirect(url_for('admin_solicitudes'))
    nota = request.form.get('nota', '').strip() or 'Solicitud rechazada por el administrador'
    db.execute("UPDATE solicitudes_recarga SET estado = 'rechazada', admin_id = ?, nota_admin = ?, fecha_respuesta = datetime('now','localtime') WHERE id = ?",
               (session['user_id'], nota, id))
    db.commit()
    db.close()
    flash(f'Solicitud #{id} rechazada.', 'success')
    return redirect(url_for('admin_solicitudes'))


@app.route('/admin/metodos-pago', methods=['GET', 'POST'])
@admin_required
def admin_metodos_pago():
    db = get_db()
    if request.method == 'POST':
        for key in ['pago_movil', 'binance', 'zinli', 'zelle']:
            activo = '1' if request.form.get(f'{key}_activo') else '0'
            nombre = request.form.get(f'{key}_nombre', '').strip()
            datos = request.form.get(f'{key}_datos', '').strip()
            nota = request.form.get(f'{key}_nota', '').strip()
            for clave, valor in [
                (f'metodo_{key}_activo', activo),
                (f'metodo_{key}_nombre', nombre),
                (f'metodo_{key}_datos', datos),
                (f'metodo_{key}_nota', nota),
            ]:
                existing = db.execute("SELECT id FROM configuracion WHERE clave = ?", (clave,)).fetchone()
                if existing:
                    db.execute("UPDATE configuracion SET valor = ? WHERE clave = ?", (valor, clave))
                else:
                    db.execute("INSERT INTO configuracion (clave, valor) VALUES (?,?)", (clave, valor))
        # Recarga mínima
        recarga_min_raw = request.form.get('recarga_minima', '0').strip()
        try:
            recarga_min_num = float(recarga_min_raw)
        except (ValueError, TypeError):
            recarga_min_num = 0.0
        if recarga_min_num < 0:
            recarga_min_num = 0.0
        recarga_min = f"{recarga_min_num:.2f}"
        existing = db.execute("SELECT id FROM configuracion WHERE clave = 'recarga_minima'").fetchone()
        if existing:
            db.execute("UPDATE configuracion SET valor = ? WHERE clave = 'recarga_minima'", (recarga_min,))
        else:
            db.execute("INSERT INTO configuracion (clave, valor) VALUES ('recarga_minima', ?)", (recarga_min,))

        suscripcion_precio_raw = request.form.get('suscripcion_mensual_precio', '0').strip().replace(',', '.')
        try:
            suscripcion_precio_num = float(suscripcion_precio_raw)
        except (ValueError, TypeError):
            suscripcion_precio_num = 0.0
        if suscripcion_precio_num < 0:
            suscripcion_precio_num = 0.0
        _config_set(db, 'suscripcion_mensual_precio', f"{suscripcion_precio_num:.4f}")

        db.commit()
        db.close()
        flash('Métodos de pago actualizados correctamente', 'success')
        return redirect(url_for('admin_metodos_pago'))
    config_rows = db.execute("SELECT clave, valor FROM configuracion").fetchall()
    config = {r['clave']: r['valor'] for r in config_rows}
    db.close()
    recarga_minima = config.get('recarga_minima', '0')
    suscripcion_mensual_precio = config.get('suscripcion_mensual_precio', '0')
    metodos = []
    for key, icono, color in [('pago_movil', 'fa-mobile-alt', '#4CAF50'), ('binance', 'fa-coins', '#F0B90B'), ('zinli', 'fa-wallet', '#6C63FF'), ('zelle', 'fa-university', '#6D1ED4')]:
        metodos.append({
            'key': key,
            'icono': icono,
            'color': color,
            'activo': config.get(f'metodo_{key}_activo', '0'),
            'nombre': config.get(f'metodo_{key}_nombre', ''),
            'datos': config.get(f'metodo_{key}_datos', ''),
            'nota': config.get(f'metodo_{key}_nota', ''),
        })
    return render_template('admin/metodos_pago.html', metodos=metodos, recarga_minima=recarga_minima, suscripcion_mensual_precio=suscripcion_mensual_precio)


@app.route('/admin/popup-publicitario', methods=['GET', 'POST'])
@admin_required
def admin_popup_publicitario():
    db = get_db()
    if request.method == 'POST':
        activo = '1' if request.form.get('activo') else '0'
        imagen = request.form.get('imagen_url', '').strip()
        archivo = request.files.get('imagen_file')
        uploaded = save_upload(archivo)
        reiniciar_vistas = 1 if request.form.get('reiniciar_vistas') else 0

        if uploaded:
            imagen = uploaded
            reiniciar_vistas = 1

        max_vistas_raw = request.form.get('max_vistas', '1').strip()
        try:
            max_vistas = int(max_vistas_raw or 1)
        except (TypeError, ValueError):
            max_vistas = 1
        if max_vistas < 0:
            max_vistas = 0
        if max_vistas > 200:
            max_vistas = 200

        current_version_raw = _config_get(db, 'popup_publicitario_version', '1')
        try:
            current_version = int(current_version_raw or 1)
        except (TypeError, ValueError):
            current_version = 1
        if reiniciar_vistas:
            current_version += 1

        _config_set(db, 'popup_publicitario_activo', activo)
        _config_set(db, 'popup_publicitario_imagen', imagen)
        _config_set(db, 'popup_publicitario_max_vistas', str(max_vistas))
        _config_set(db, 'popup_publicitario_version', str(current_version))

        db.commit()
        db.close()
        flash('Popup publicitario actualizado correctamente.', 'success')
        return redirect(url_for('admin_popup_publicitario'))

    popup_config = {
        'activo': _config_get(db, 'popup_publicitario_activo', '0'),
        'imagen': _config_get(db, 'popup_publicitario_imagen', ''),
        'max_vistas': _config_get(db, 'popup_publicitario_max_vistas', '1'),
        'version': _config_get(db, 'popup_publicitario_version', '1'),
    }
    db.close()
    return render_template('admin/popup_publicitario.html', popup_config=popup_config)


@app.route('/api/popup-publicitario/visto', methods=['POST'])
@login_required
def api_popup_publicitario_visto():
    db = get_db()
    popup = _obtener_popup_publicitario_para_usuario(db, session['user_id'])
    if not popup:
        db.close()
        return jsonify({'ok': False, 'mostrar': False})

    version = int(popup['version'])
    max_vistas = int(popup['max_vistas'])
    row = db.execute(
        "SELECT vistas FROM popup_publicidad_vistas WHERE usuario_id = ? AND version = ?",
        (session['user_id'], version),
    ).fetchone()
    vistas_actuales = int(row['vistas']) if row else 0
    nuevas_vistas = vistas_actuales + 1
    if nuevas_vistas > max_vistas:
        nuevas_vistas = max_vistas

    db.execute(
        "INSERT INTO popup_publicidad_vistas (usuario_id, version, vistas, fecha_ultima_vista) VALUES (?,?,?,datetime('now','localtime')) "
        "ON CONFLICT(usuario_id, version) DO UPDATE SET vistas = excluded.vistas, fecha_ultima_vista = excluded.fecha_ultima_vista",
        (session['user_id'], version, nuevas_vistas),
    )
    db.commit()
    db.close()

    return jsonify({
        'ok': True,
        'mostrar': nuevas_vistas < max_vistas,
        'vistas': nuevas_vistas,
        'max_vistas': max_vistas,
    })


@app.route('/admin/bonus-recarga', methods=['GET', 'POST'])
@admin_required
def admin_bonus_recarga():
    db = get_db()
    if request.method == 'POST':
        accion = request.form.get('accion')
        if accion == 'agregar':
            monto_minimo = request.form.get('monto_minimo', '0')
            porcentaje = request.form.get('porcentaje_bonus', '0')
            try:
                monto_minimo = float(monto_minimo)
                porcentaje = float(porcentaje)
            except (ValueError, TypeError):
                flash('Valores inválidos', 'error')
                return redirect(url_for('admin_bonus_recarga'))
            if monto_minimo < 0:
                flash('El monto mínimo no puede ser negativo', 'error')
                return redirect(url_for('admin_bonus_recarga'))
            if porcentaje == 0:
                flash('El porcentaje no puede ser 0 (usa positivo para bonus o negativo para descuento)', 'error')
                return redirect(url_for('admin_bonus_recarga'))
            db.execute("INSERT INTO bonus_recarga (monto_minimo, porcentaje_bonus) VALUES (?,?)",
                       (monto_minimo, porcentaje))
            db.commit()
            etiqueta = 'Bonus' if porcentaje > 0 else 'Descuento'
            flash(f'{etiqueta} agregado: {porcentaje:+.2f}% en recargas >= ${monto_minimo:.2f}', 'success')
        elif accion == 'eliminar':
            bonus_id = request.form.get('bonus_id')
            db.execute("DELETE FROM bonus_recarga WHERE id = ?", (bonus_id,))
            db.commit()
            flash('Bonus eliminado', 'success')
        elif accion == 'toggle':
            bonus_id = request.form.get('bonus_id')
            b = db.execute("SELECT activo FROM bonus_recarga WHERE id = ?", (bonus_id,)).fetchone()
            if b:
                db.execute("UPDATE bonus_recarga SET activo = ? WHERE id = ?", (0 if b['activo'] else 1, bonus_id))
                db.commit()
                flash('Bonus actualizado', 'success')
        db.close()
        return redirect(url_for('admin_bonus_recarga'))
    bonuses = db.execute("SELECT * FROM bonus_recarga ORDER BY monto_minimo ASC").fetchall()
    db.close()
    return render_template('admin/bonus_recarga.html', bonuses=bonuses)


@app.route('/admin/telegram', methods=['GET', 'POST'])
@admin_required
def admin_telegram():
    db = get_db()
    if request.method == 'POST':
        accion = str(request.form.get('accion', 'guardar') or 'guardar').strip().lower()
        token = request.form.get('telegram_bot_token', '').strip()
        chat_id = request.form.get('telegram_chat_id', '').strip()
        activo = '1' if request.form.get('telegram_activo') else '0'
        token_precios = request.form.get('telegram_precios_bot_token', '').strip()
        chat_id_precios = request.form.get('telegram_precios_chat_id', '').strip()
        activo_precios = '1' if request.form.get('telegram_precios_activo') else '0'
        for clave, valor in [
            ('telegram_bot_token', token),
            ('telegram_chat_id', chat_id),
            ('telegram_activo', activo),
            ('telegram_precios_bot_token', token_precios),
            ('telegram_precios_chat_id', chat_id_precios),
            ('telegram_precios_activo', activo_precios),
        ]:
            existing = db.execute("SELECT id FROM configuracion WHERE clave = ?", (clave,)).fetchone()
            if existing:
                db.execute("UPDATE configuracion SET valor = ? WHERE clave = ?", (valor, clave))
            else:
                db.execute("INSERT INTO configuracion (clave, valor) VALUES (?,?)", (clave, valor))
        db.commit()

        if accion == 'probar_general':
            if activo == '1' and token and chat_id:
                tg_test = enviar_telegram(
                    "🧪 <b>Mensaje de prueba</b>\n\n"
                    "Canal: <b>Notificaciones generales</b>\n"
                    "Evento ejemplo: nueva recarga o stock bajo.",
                    async_send=False,
                )
                if tg_test.get('ok'):
                    flash('Se envió el mensaje de prueba al bot general.', 'success')
                else:
                    flash(f"No se pudo enviar el mensaje de prueba al bot general: {tg_test.get('error', 'sin detalle')}", 'error')
            else:
                flash('Para probar el bot general debes activarlo y completar Token + Chat ID.', 'warning')
        elif accion == 'probar_precios':
            if activo_precios == '1' and token_precios and chat_id_precios:
                tg_test = enviar_telegram_con_keys(
                    "🧪 <b>Mensaje de prueba de precios</b>\n\n"
                    "Refresco: <b>15 min</b>\n"
                    "Proveedor: <b>GamePoint/MooGold</b>\n"
                    "Cambios detectados: <b>3</b>",
                    token_key='telegram_precios_bot_token',
                    chat_id_key='telegram_precios_chat_id',
                    activo_key='telegram_precios_activo',
                    fallback_to_default=False,
                    async_send=False,
                )
                if tg_test.get('ok'):
                    flash('Se envió el mensaje de prueba al bot de precios.', 'success')
                else:
                    flash(f"No se pudo enviar el mensaje de prueba al bot de precios: {tg_test.get('error', 'sin detalle')}", 'error')
            else:
                flash('Para probar el bot de precios debes activarlo y completar Token + Chat ID.', 'warning')
        else:
            flash('Configuración de Telegram guardada', 'success')

        db.close()
        return redirect(url_for('admin_telegram'))
    config_rows = db.execute("SELECT clave, valor FROM configuracion WHERE clave LIKE 'telegram_%'").fetchall()
    config = {r['clave']: r['valor'] for r in config_rows}
    db.close()
    return render_template('admin/telegram.html', config=config)


def _asegurar_bloqueo_api_pases_de_nivel(db):
    db.execute("CREATE TABLE IF NOT EXISTS usuario_api_categorias_bloqueadas (usuario_id INTEGER NOT NULL, categoria_id INTEGER NOT NULL, fecha TEXT DEFAULT (datetime('now','localtime')), PRIMARY KEY (usuario_id, categoria_id), FOREIGN KEY (usuario_id) REFERENCES usuarios(id), FOREIGN KEY (categoria_id) REFERENCES categorias(id))")
    categoria = db.execute("SELECT id FROM categorias WHERE lower(nombre) = lower(?) LIMIT 1", ('Pases De Nivel',)).fetchone()
    if not categoria:
        return
    db.execute(
        """
        INSERT OR IGNORE INTO usuario_api_categorias_bloqueadas (usuario_id, categoria_id)
        SELECT id, ? FROM usuarios WHERE rol != 'admin'
        """,
        (int(categoria['id']),),
    )


@app.route('/admin/usuarios')
@admin_required
def admin_usuarios():
    db = get_db()
    db.execute("CREATE TABLE IF NOT EXISTS usuario_api_categorias_bloqueadas (usuario_id INTEGER NOT NULL, categoria_id INTEGER NOT NULL, fecha TEXT DEFAULT (datetime('now','localtime')), PRIMARY KEY (usuario_id, categoria_id), FOREIGN KEY (usuario_id) REFERENCES usuarios(id), FOREIGN KEY (categoria_id) REFERENCES categorias(id))")
    usuarios = db.execute("SELECT u.*, COALESCE(c.saldo, 0) as saldo FROM usuarios u LEFT JOIN carteras c ON u.id = c.usuario_id ORDER BY u.fecha_registro DESC").fetchall()
    categorias_api = db.execute("""
        SELECT c.id, c.nombre, c.tipo, COUNT(p.id) as total_productos
        FROM categorias c
        JOIN productos p ON p.categoria_id = c.id
        WHERE p.activo = 1 AND (
            p.usa_api = 1 OR p.usa_razer = 1 OR p.usa_deltaforce = 1 OR p.usa_pincentral = 1
            OR COALESCE(p.gamepoint_product_id, 0) > 0 OR COALESCE(p.moogold_product_id, 0) > 0
            OR COALESCE(p.bloodstrike_package_id, '') != ''
        )
        GROUP BY c.id, c.nombre, c.tipo, c.orden
        ORDER BY c.orden, c.nombre
    """).fetchall()
    bloqueos_rows = db.execute("SELECT usuario_id, categoria_id FROM usuario_api_categorias_bloqueadas").fetchall()
    api_categorias_bloqueadas = {}
    for row in bloqueos_rows:
        api_categorias_bloqueadas.setdefault(int(row['usuario_id']), []).append(int(row['categoria_id']))
    db.close()
    return render_template('admin/usuarios.html', usuarios=usuarios, categorias_api=categorias_api, api_categorias_bloqueadas=api_categorias_bloqueadas)


@app.route('/admin/usuario/<int:id>/toggle', methods=['POST'])
@admin_required
def admin_toggle_usuario(id):
    db = get_db()
    user = db.execute("SELECT id, activo, aprobado, rol FROM usuarios WHERE id = ?", (id,)).fetchone()
    if not user or user['rol'] == 'admin':
        db.close()
        flash('No se puede modificar este usuario.', 'error')
        return redirect(url_for('admin_usuarios'))
    nuevo_estado = 0 if user['activo'] else 1
    if nuevo_estado:
        db.execute("UPDATE usuarios SET activo = 1, aprobado = 1 WHERE id = ?", (id,))
    else:
        db.execute("UPDATE usuarios SET activo = 0 WHERE id = ?", (id,))
    db.commit()
    db.close()
    if nuevo_estado:
        flash('Usuario aprobado y activado.', 'success')
    else:
        flash('Usuario desactivado.', 'success')
    return redirect(url_for('admin_usuarios'))


@app.route('/admin/usuario/<int:id>/api-compras/toggle', methods=['POST'])
@admin_required
def admin_toggle_api_compras_usuario(id):
    db = get_db()
    user = db.execute("SELECT id, nombre, rol, api_compras_habilitadas FROM usuarios WHERE id = ?", (id,)).fetchone()
    if not user or user['rol'] == 'admin':
        db.close()
        flash('No se puede modificar este permiso.', 'error')
        return redirect(url_for('admin_usuarios'))
    nuevo_estado = 0 if int((user['api_compras_habilitadas'] if 'api_compras_habilitadas' in user.keys() else 1) or 0) else 1
    db.execute("UPDATE usuarios SET api_compras_habilitadas = ? WHERE id = ?", (nuevo_estado, id))
    db.commit()
    db.close()
    if nuevo_estado:
        flash(f'Compras vía API habilitadas para {user["nombre"]}.', 'success')
    else:
        flash(f'Compras vía API deshabilitadas para {user["nombre"]}.', 'success')
    return redirect(url_for('admin_usuarios'))


@app.route('/admin/usuario/<int:id>/api-categorias', methods=['POST'])
@admin_required
def admin_usuario_api_categorias(id):
    db = get_db()
    user = db.execute("SELECT id, nombre, rol FROM usuarios WHERE id = ?", (id,)).fetchone()
    if not user or user['rol'] == 'admin':
        db.close()
        flash('No se pueden modificar categorías API para este usuario.', 'error')
        return redirect(url_for('admin_usuarios'))
    db.execute("CREATE TABLE IF NOT EXISTS usuario_api_categorias_bloqueadas (usuario_id INTEGER NOT NULL, categoria_id INTEGER NOT NULL, fecha TEXT DEFAULT (datetime('now','localtime')), PRIMARY KEY (usuario_id, categoria_id), FOREIGN KEY (usuario_id) REFERENCES usuarios(id), FOREIGN KEY (categoria_id) REFERENCES categorias(id))")
    bloqueadas = []
    for value in request.form.getlist('categorias_bloqueadas'):
        try:
            categoria_id = int(value)
        except (TypeError, ValueError):
            categoria_id = 0
        if categoria_id > 0:
            bloqueadas.append(categoria_id)
    db.execute("DELETE FROM usuario_api_categorias_bloqueadas WHERE usuario_id = ?", (id,))
    for categoria_id in sorted(set(bloqueadas)):
        db.execute("INSERT OR IGNORE INTO usuario_api_categorias_bloqueadas (usuario_id, categoria_id) VALUES (?, ?)", (id, categoria_id))
    db.commit()
    db.close()
    flash(f'Categorías API bloqueadas actualizadas para {user["nombre"]}.', 'success')
    return redirect(url_for('admin_usuarios'))


@app.route('/admin/usuario/<int:id>/editar', methods=['POST'])
@admin_required
def admin_editar_usuario(id):
    db = get_db()
    user = db.execute("SELECT * FROM usuarios WHERE id = ?", (id,)).fetchone()
    if not user:
        db.close()
        flash('Usuario no encontrado', 'error')
        return redirect(url_for('admin_usuarios'))
    nombre = request.form.get('nombre', '').strip()
    email = request.form.get('email', '').strip()
    telefono = request.form.get('telefono', '').strip()
    nueva_pass = request.form.get('password', '').strip()
    if nombre and email:
        try:
            if nueva_pass and len(nueva_pass) >= 6:
                db.execute("UPDATE usuarios SET nombre=?, email=?, telefono=?, password=? WHERE id=?",
                           (nombre, email, telefono, generate_password_hash(nueva_pass), id))
            else:
                db.execute("UPDATE usuarios SET nombre=?, email=?, telefono=? WHERE id=?",
                           (nombre, email, telefono, id))
            db.commit()
            flash(f'Usuario "{nombre}" actualizado', 'success')
        except Exception:
            flash('Error: el email ya está en uso', 'error')
    db.close()
    return redirect(url_for('admin_usuarios'))


@app.route('/admin/usuario/<int:id>/quitar-suscripcion', methods=['POST'])
@admin_required
def admin_quitar_suscripcion(id):
    db = get_db()
    user = db.execute("SELECT id, nombre, rol FROM usuarios WHERE id = ?", (id,)).fetchone()
    if not user or user['rol'] == 'admin':
        db.close()
        flash('No se puede modificar esta suscripción.', 'error')
        return redirect(url_for('admin_usuarios'))
    db.execute("UPDATE usuarios SET suscripcion_hasta = '', autorenovar_suscripcion = 0 WHERE id = ?", (id,))
    db.commit()
    db.close()
    flash(f'Suscripción removida a {user["nombre"]}.', 'success')
    return redirect(url_for('admin_usuarios'))


@app.route('/admin/usuario/<int:id>/descontar', methods=['POST'])
@admin_required
def admin_descontar_saldo(id):
    db = get_db()
    user = db.execute("SELECT u.*, COALESCE(c.saldo, 0) as saldo FROM usuarios u LEFT JOIN carteras c ON u.id = c.usuario_id WHERE u.id = ?", (id,)).fetchone()
    if not user:
        db.close()
        flash('Usuario no encontrado', 'error')
        return redirect(url_for('admin_usuarios'))
    try:
        monto = float(request.form.get('monto', 0))
    except (ValueError, TypeError):
        monto = 0
    motivo = request.form.get('motivo', 'Descuento administrativo').strip() or 'Descuento administrativo'
    if monto <= 0:
        db.close()
        flash('El monto debe ser mayor a 0', 'error')
        return redirect(url_for('admin_usuarios'))
    if monto > user['saldo']:
        db.close()
        flash(f'El usuario solo tiene ${user["saldo"]:.4f} de saldo', 'error')
        return redirect(url_for('admin_usuarios'))
    db.close()
    nuevo_saldo = descontar_saldo(id, monto, motivo)
    flash(f'Se descontó ${monto:.4f} a {user["nombre"]}. Nuevo saldo: ${nuevo_saldo:.4f}', 'success')
    return redirect(url_for('admin_usuarios'))


@app.route('/admin/usuario/<int:id>/eliminar', methods=['POST'])
@admin_required
def admin_eliminar_usuario(id):
    db = get_db()
    user = db.execute("SELECT * FROM usuarios WHERE id = ?", (id,)).fetchone()
    if not user:
        db.close()
        flash('Usuario no encontrado', 'error')
        return redirect(url_for('admin_usuarios'))
    if user['rol'] == 'admin':
        db.close()
        flash('No se puede eliminar un administrador', 'error')
        return redirect(url_for('admin_usuarios'))
    nombre = user['nombre']
    try:
        db.execute("BEGIN IMMEDIATE")
        # Tablas auxiliares que referencian directamente al usuario
        db.execute("DELETE FROM usuario_tokens WHERE usuario_id = ?", (id,))
        db.execute("DELETE FROM popup_publicidad_vistas WHERE usuario_id = ?", (id,))
        db.execute("DELETE FROM usuario_api_productos_bloqueados WHERE usuario_id = ?", (id,))
        db.execute("DELETE FROM usuario_api_categorias_bloqueadas WHERE usuario_id = ?", (id,))
        # Transacciones propias o gestionadas por el usuario
        db.execute("DELETE FROM transacciones WHERE usuario_id = ? OR admin_id = ? OR pedido_id IN (SELECT id FROM pedidos WHERE usuario_id = ?)", (id, id, id))
        # Auditoría de recargas asociadas al usuario o a sus pedidos
        db.execute("DELETE FROM recargas_auditoria WHERE usuario_id = ? OR pedido_id IN (SELECT id FROM pedidos WHERE usuario_id = ?)", (id, id))
        # Referencias de pago ligadas al usuario o a sus solicitudes
        db.execute(
            "DELETE FROM referencias_pago_usadas WHERE usuario_id = ? OR solicitud_id IN (SELECT id FROM solicitudes_recarga WHERE usuario_id = ? OR admin_id = ?)",
            (id, id, id)
        )
        # Solicitudes de recarga propias o gestionadas por el usuario
        db.execute("DELETE FROM solicitudes_recarga WHERE usuario_id = ? OR admin_id = ?", (id, id))
        # Cartera y pines usados
        db.execute("DELETE FROM carteras WHERE usuario_id = ?", (id,))
        db.execute("UPDATE pines SET usado_por = NULL, pedido_id = NULL WHERE usado_por = ?", (id,))
        # Pedidos (después de limpiar tablas que los referencian)
        db.execute("DELETE FROM pedidos WHERE usuario_id = ?", (id,))
        # Usuario
        db.execute("DELETE FROM usuarios WHERE id = ?", (id,))
        db.commit()
        flash(f'Usuario "{nombre}" eliminado permanentemente', 'success')
    except Exception as e:
        db.rollback()
        flash(f'No se pudo eliminar el usuario: {e}', 'error')
    finally:
        db.close()
    return redirect(url_for('admin_usuarios'))


@app.route('/admin/recargas', methods=['GET', 'POST'])
@admin_required
def admin_recargas():
    if request.method == 'POST':
        usuario_id = int(request.form.get('usuario_id', 0))
        monto = float(request.form.get('monto', 0))
        descripcion = request.form.get('descripcion', 'Recarga de saldo').strip()
        if usuario_id > 0 and monto > 0:
            user = get_user_by_id(usuario_id)
            if user:
                nuevo_saldo = recargar_saldo(usuario_id, monto, descripcion, session['user_id'])
                flash(f'Recarga de ${monto:.4f} aplicada a {user["nombre"]}. Nuevo saldo: ${nuevo_saldo:.4f}', 'success')
            else:
                flash('Usuario no encontrado', 'error')
        else:
            flash('Datos inválidos', 'error')
        return redirect(url_for('admin_recargas'))

    db = get_db()
    usuarios = db.execute("SELECT u.*, COALESCE(c.saldo, 0) as saldo FROM usuarios u LEFT JOIN carteras c ON u.id = c.usuario_id ORDER BY u.nombre").fetchall()
    transacciones = db.execute("SELECT t.*, u.nombre as usuario_nombre, a.nombre as admin_nombre FROM transacciones t JOIN usuarios u ON t.usuario_id = u.id LEFT JOIN usuarios a ON t.admin_id = a.id ORDER BY t.fecha DESC LIMIT 30").fetchall()
    db.close()
    return render_template('admin/recargas.html', usuarios=usuarios, transacciones=transacciones)


@app.route('/admin/productos', methods=['GET', 'POST'])
@admin_required
def admin_productos():
    db = get_db()
    if request.method == 'POST':
        mg_margin_txt = str(request.form.get('moogold_margin_percent', '') or '').strip().replace(',', '.')
        if mg_margin_txt:
            mg_margin = _to_decimal(mg_margin_txt)
            if mg_margin is not None and mg_margin >= 0:
                _config_set(db, 'moogold_margin_percent', str(mg_margin))

        mg_margin_sub_txt = str(request.form.get('moogold_margin_percent_subscriber', '') or '').strip().replace(',', '.')
        if mg_margin_sub_txt:
            mg_margin_sub = _to_decimal(mg_margin_sub_txt)
            if mg_margin_sub is not None and mg_margin_sub >= 0:
                _config_set(db, 'moogold_margin_percent_subscriber', str(mg_margin_sub))

        accion = request.form.get('accion')
        if accion == 'crear':
            nombre = request.form.get('nombre', '').strip()
            descripcion = request.form.get('descripcion', '').strip()
            campos_cliente = request.form.get('campos_cliente', '').strip()
            freefire_levelpass = request.form.get('freefire_levelpass', '').strip()
            precio = float(request.form.get('precio', 0) or 0)
            precio_suscriptor = float(request.form.get('precio_suscriptor', 0) or 0)
            categoria_id = int(request.form.get('categoria_id', 0) or 0)
            icono = request.form.get('icono', 'fa-gem').strip()
            usa_api = 1 if request.form.get('usa_api') else 0
            monto_api = int(request.form.get('monto_api', 0) or 0)
            usa_razer = 1 if request.form.get('usa_razer') else 0
            razer_paquete = int(request.form.get('razer_paquete', 0) or 0)
            razer_paquete_extra = int(request.form.get('razer_paquete_extra', 0) or 0)
            usa_deltaforce = 1 if request.form.get('usa_deltaforce') else 0
            deltaforce_paquete = int(request.form.get('deltaforce_paquete', 0) or 0)
            usa_pincentral = 1 if request.form.get('usa_pincentral') else 0
            pincentral_product_code = request.form.get('pincentral_product_code', '').strip()
            pincentral_entrega_directa = 1 if request.form.get('pincentral_entrega_directa') else 0
            pincentral_recarga_directa = 1 if request.form.get('pincentral_recarga_directa') else 0
            pincentral_fields = request.form.get('pincentral_fields', '').strip()
            pincentral_recarga_cantidad = max(1, min(int(request.form.get('pincentral_recarga_cantidad', 1) or 1), 20))
            usa_jadh = 1 if request.form.get('usa_jadh') else 0
            jadh_item_id = request.form.get('jadh_item_id', '').strip() or '32'
            jadh_diamonds = int(request.form.get('jadh_diamonds', 0) or 0)
            jadh_package_id = request.form.get('jadh_package_id', '').strip()
            gamepoint_product_id = int(request.form.get('gamepoint_product_id', 0) or 0)
            gamepoint_package_id = int(request.form.get('gamepoint_package_id', 0) or 0)
            gamepoint_fields = request.form.get('gamepoint_fields', '').strip()
            bloodstrike_package_id = request.form.get('bloodstrike_package_id', '').strip()
            usa_moogold = 1 if request.form.get('usa_moogold') else 0
            moogold_category_id = int(request.form.get('moogold_category_id', 0) or 0)
            moogold_product_id = int(request.form.get('moogold_product_id', 0) or 0)
            moogold_variation_id = int(request.form.get('moogold_variation_id', 0) or 0)
            moogold_fields = request.form.get('moogold_fields', '').strip()
            rechazo_automatico = 1 if request.form.get('rechazo_automatico') else 0
            recarga_manual = 1 if request.form.get('recarga_manual') else 0
            orden = int(request.form.get('orden', 0) or 0)
            pin_origen_producto_id = int(request.form.get('pin_origen_producto_id', 0) or 0)
            stock_minimo = int(request.form.get('stock_minimo', 0) or 0)
            stock_objetivo = int(request.form.get('stock_objetivo', 0) or 0)
            canjes_por_compra = int(request.form.get('canjes_por_compra', 1)) or 1
            if not usa_pincentral:
                pincentral_entrega_directa = 0
                pincentral_recarga_directa = 0
                pincentral_fields = ''
                pincentral_recarga_cantidad = 1
            if pincentral_recarga_directa:
                pincentral_entrega_directa = 0
                if not pincentral_fields:
                    pincentral_fields = 'id_juego:ID del jugador'
                pincentral_recarga_cantidad = max(1, min(pincentral_recarga_cantidad, 20))
            if not usa_moogold:
                moogold_category_id = 0
                moogold_product_id = 0
                moogold_variation_id = 0
                moogold_fields = ''
            if not usa_jadh:
                jadh_item_id = '32'
                jadh_diamonds = 0
                jadh_package_id = ''
            if precio_suscriptor < 0:
                precio_suscriptor = 0
            if nombre and precio > 0 and categoria_id > 0:
                db.execute("INSERT INTO productos (nombre, descripcion, campos_cliente, freefire_levelpass, precio, precio_suscriptor, categoria_id, icono, usa_api, monto_api, usa_razer, razer_paquete, razer_paquete_extra, usa_deltaforce, deltaforce_paquete, usa_pincentral, pincentral_product_code, pincentral_entrega_directa, pincentral_recarga_directa, pincentral_fields, pincentral_recarga_cantidad, usa_jadh, jadh_item_id, jadh_diamonds, jadh_package_id, gamepoint_product_id, gamepoint_package_id, gamepoint_fields, bloodstrike_package_id, moogold_category_id, moogold_product_id, moogold_variation_id, moogold_fields, rechazo_automatico, recarga_manual, orden, pin_origen_producto_id, stock_minimo, stock_objetivo, canjes_por_compra) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (nombre, descripcion, campos_cliente, freefire_levelpass, precio, precio_suscriptor, categoria_id, icono, usa_api, monto_api, usa_razer, razer_paquete, razer_paquete_extra, usa_deltaforce, deltaforce_paquete, usa_pincentral, pincentral_product_code, pincentral_entrega_directa, pincentral_recarga_directa, pincentral_fields, pincentral_recarga_cantidad, usa_jadh, jadh_item_id, jadh_diamonds, jadh_package_id, gamepoint_product_id, gamepoint_package_id, gamepoint_fields, bloodstrike_package_id, moogold_category_id, moogold_product_id, moogold_variation_id, moogold_fields, rechazo_automatico, recarga_manual, orden, pin_origen_producto_id, stock_minimo, stock_objetivo, canjes_por_compra))
                db.commit()
                flash(f'Producto "{nombre}" creado', 'success')
        elif accion == 'editar':
            prod_id = int(request.form.get('producto_id', 0) or 0)
            nombre = request.form.get('nombre', '').strip()
            descripcion = request.form.get('descripcion', '').strip()
            campos_cliente = request.form.get('campos_cliente', '').strip()
            freefire_levelpass = request.form.get('freefire_levelpass', '').strip()
            precio = float(request.form.get('precio', 0) or 0)
            precio_suscriptor = float(request.form.get('precio_suscriptor', 0) or 0)
            categoria_id = int(request.form.get('categoria_id', 0) or 0)
            activo = 1 if request.form.get('activo') else 0
            usa_api = 1 if request.form.get('usa_api') else 0
            monto_api = int(request.form.get('monto_api', 0) or 0)
            usa_razer = 1 if request.form.get('usa_razer') else 0
            razer_paquete = int(request.form.get('razer_paquete', 0) or 0)
            razer_paquete_extra = int(request.form.get('razer_paquete_extra', 0) or 0)
            usa_deltaforce = 1 if request.form.get('usa_deltaforce') else 0
            deltaforce_paquete = int(request.form.get('deltaforce_paquete', 0) or 0)
            usa_pincentral = 1 if request.form.get('usa_pincentral') else 0
            pincentral_product_code = request.form.get('pincentral_product_code', '').strip()
            pincentral_entrega_directa = 1 if request.form.get('pincentral_entrega_directa') else 0
            pincentral_recarga_directa = 1 if request.form.get('pincentral_recarga_directa') else 0
            pincentral_fields = request.form.get('pincentral_fields', '').strip()
            pincentral_recarga_cantidad = max(1, min(int(request.form.get('pincentral_recarga_cantidad', 1) or 1), 20))
            usa_jadh = 1 if request.form.get('usa_jadh') else 0
            jadh_item_id = request.form.get('jadh_item_id', '').strip() or '32'
            jadh_diamonds = int(request.form.get('jadh_diamonds', 0) or 0)
            jadh_package_id = request.form.get('jadh_package_id', '').strip()
            gamepoint_product_id = int(request.form.get('gamepoint_product_id', 0) or 0)
            gamepoint_package_id = int(request.form.get('gamepoint_package_id', 0) or 0)
            gamepoint_fields = request.form.get('gamepoint_fields', '').strip()
            bloodstrike_package_id = request.form.get('bloodstrike_package_id', '').strip()
            usa_moogold = 1 if request.form.get('usa_moogold') else 0
            moogold_category_id = int(request.form.get('moogold_category_id', 0) or 0)
            moogold_product_id = int(request.form.get('moogold_product_id', 0) or 0)
            moogold_variation_id = int(request.form.get('moogold_variation_id', 0) or 0)
            moogold_fields = request.form.get('moogold_fields', '').strip()
            rechazo_automatico = 1 if request.form.get('rechazo_automatico') else 0
            recarga_manual = 1 if request.form.get('recarga_manual') else 0
            orden = int(request.form.get('orden', 0) or 0)
            pin_origen_producto_id = int(request.form.get('pin_origen_producto_id', 0) or 0)
            stock_minimo = int(request.form.get('stock_minimo', 0) or 0)
            stock_objetivo = int(request.form.get('stock_objetivo', 0) or 0)
            canjes_por_compra = int(request.form.get('canjes_por_compra', 1)) or 1
            if not usa_pincentral:
                pincentral_entrega_directa = 0
                pincentral_recarga_directa = 0
                pincentral_fields = ''
                pincentral_recarga_cantidad = 1
            if pincentral_recarga_directa:
                pincentral_entrega_directa = 0
                if not pincentral_fields:
                    pincentral_fields = 'id_juego:ID del jugador'
                pincentral_recarga_cantidad = max(1, min(pincentral_recarga_cantidad, 20))
            if not usa_moogold:
                moogold_category_id = 0
                moogold_product_id = 0
                moogold_variation_id = 0
                moogold_fields = ''
            if not usa_jadh:
                jadh_item_id = '32'
                jadh_diamonds = 0
                jadh_package_id = ''
            if precio_suscriptor < 0:
                precio_suscriptor = 0
            if prod_id > 0 and nombre and precio > 0:
                db.execute("UPDATE productos SET nombre=?, descripcion=?, campos_cliente=?, freefire_levelpass=?, precio=?, precio_suscriptor=?, categoria_id=?, activo=?, usa_api=?, monto_api=?, usa_razer=?, razer_paquete=?, razer_paquete_extra=?, usa_deltaforce=?, deltaforce_paquete=?, usa_pincentral=?, pincentral_product_code=?, pincentral_entrega_directa=?, pincentral_recarga_directa=?, pincentral_fields=?, pincentral_recarga_cantidad=?, usa_jadh=?, jadh_item_id=?, jadh_diamonds=?, jadh_package_id=?, gamepoint_product_id=?, gamepoint_package_id=?, gamepoint_fields=?, bloodstrike_package_id=?, moogold_category_id=?, moogold_product_id=?, moogold_variation_id=?, moogold_fields=?, rechazo_automatico=?, recarga_manual=?, orden=?, pin_origen_producto_id=?, stock_minimo=?, stock_objetivo=?, canjes_por_compra=? WHERE id=?",
                           (nombre, descripcion, campos_cliente, freefire_levelpass, precio, precio_suscriptor, categoria_id, activo, usa_api, monto_api, usa_razer, razer_paquete, razer_paquete_extra, usa_deltaforce, deltaforce_paquete, usa_pincentral, pincentral_product_code, pincentral_entrega_directa, pincentral_recarga_directa, pincentral_fields, pincentral_recarga_cantidad, usa_jadh, jadh_item_id, jadh_diamonds, jadh_package_id, gamepoint_product_id, gamepoint_package_id, gamepoint_fields, bloodstrike_package_id, moogold_category_id, moogold_product_id, moogold_variation_id, moogold_fields, rechazo_automatico, recarga_manual, orden, pin_origen_producto_id, stock_minimo, stock_objetivo, canjes_por_compra, prod_id))
                db.commit()
                flash(f'Producto actualizado', 'success')
        elif accion == 'eliminar':
            prod_id = int(request.form.get('producto_id', 0) or 0)
            if prod_id > 0:
                try:
                    db.execute("DELETE FROM productos WHERE id = ?", (prod_id,))
                    db.commit()
                    flash('Producto eliminado', 'success')
                except sqlite3.IntegrityError:
                    db.rollback()
                    db.execute("UPDATE productos SET activo = 0 WHERE id = ?", (prod_id,))
                    db.commit()
                    flash('No se puede eliminar porque tiene pedidos asociados. Se desactivó en su lugar.', 'error')
        return redirect(url_for('admin_productos'))

    productos = db.execute("SELECT p.*, c.nombre as categoria_nombre FROM productos p LEFT JOIN categorias c ON p.categoria_id = c.id ORDER BY c.orden, c.nombre, p.orden, p.nombre").fetchall()
    categorias = db.execute("SELECT * FROM categorias ORDER BY orden").fetchall()
    # Productos giftcard para selector de restock
    productos_giftcard_raw = db.execute(
        "SELECT p.id, p.nombre FROM productos p JOIN categorias c ON p.categoria_id = c.id WHERE c.tipo = 'giftcards' AND p.activo = 1 ORDER BY p.nombre"
    ).fetchall()
    productos_giftcard = []
    for gc in productos_giftcard_raw:
        stock = db.execute("SELECT COUNT(*) as c FROM pines WHERE producto_id = ? AND estado = 'disponible'", (gc['id'],)).fetchone()['c']
        productos_giftcard.append({'id': gc['id'], 'nombre': gc['nombre'], 'stock': stock})
    moogold_margin_percent = _config_get(db, 'moogold_margin_percent', '6')
    moogold_margin_percent_subscriber = _config_get(db, 'moogold_margin_percent_subscriber', moogold_margin_percent)
    db.close()
    return render_template(
        'admin/productos.html',
        productos=productos,
        categorias=categorias,
        productos_giftcard=productos_giftcard,
        categorias_moogold=_moogold_category_catalogo(),
        moogold_margin_percent=moogold_margin_percent,
        moogold_margin_percent_subscriber=moogold_margin_percent_subscriber,
    )


@app.route('/admin/productos/eliminar-lote', methods=['POST'])
@admin_required
def admin_productos_eliminar_lote():
    data = request.get_json()
    ids = data.get('ids', [])
    db = get_db()
    eliminados = 0
    desactivados = 0
    for prod_id in ids:
        try:
            db.execute("DELETE FROM productos WHERE id = ?", (prod_id,))
            eliminados += 1
        except sqlite3.IntegrityError:
            db.rollback()
            db.execute("UPDATE productos SET activo = 0 WHERE id = ?", (prod_id,))
            desactivados += 1
    db.commit()
    db.close()
    return jsonify({'ok': True, 'eliminados': eliminados, 'desactivados': desactivados})


@app.route('/admin/productos/editar-masivo', methods=['POST'])
@admin_required
def admin_productos_editar_masivo():
    data = request.get_json()
    productos = data.get('productos', [])
    db = get_db()
    actualizados = 0
    for p in productos:
        try:
            db.execute(
                "UPDATE productos SET nombre=?, precio=?, precio_suscriptor=?, activo=?, recarga_manual=?, gamepoint_product_id=?, gamepoint_package_id=?, moogold_product_id=?, moogold_variation_id=? WHERE id=?",
                (p['nombre'], float(p['precio']), float(p.get('precio_suscriptor', 0) or 0), int(p['activo']), int(p.get('recarga_manual', 0)),
                 int(p.get('gamepoint_product_id', 0)), int(p.get('gamepoint_package_id', 0)),
                 int(p.get('moogold_product_id', 0)), int(p.get('moogold_variation_id', 0)), int(p['id']))
            )
            actualizados += 1
        except Exception:
            pass
    db.commit()
    db.close()
    return jsonify({'ok': True, 'actualizados': actualizados})


@app.route('/admin/productos/orden', methods=['POST'])
@admin_required
def admin_producto_orden():
    data = request.get_json()
    prod_id = data.get('id')
    direccion = data.get('dir')  # 'up' o 'down'
    db = get_db()
    prod = db.execute("SELECT id, categoria_id FROM productos WHERE id = ?", (prod_id,)).fetchone()
    if not prod:
        db.close()
        return jsonify({'ok': False})
    cat_id = prod['categoria_id']
    # Obtener todos los productos de esta categoría ordenados
    todos = db.execute("SELECT id FROM productos WHERE categoria_id = ? ORDER BY orden ASC, id ASC", (cat_id,)).fetchall()
    ids = [r['id'] for r in todos]
    # Normalizar: asignar orden 0,1,2,3...
    for i, pid in enumerate(ids):
        db.execute("UPDATE productos SET orden = ? WHERE id = ?", (i, pid))
    db.commit()
    # Encontrar posición actual
    pos = ids.index(prod_id)
    if direccion == 'up' and pos > 0:
        ids[pos], ids[pos - 1] = ids[pos - 1], ids[pos]
    elif direccion == 'down' and pos < len(ids) - 1:
        ids[pos], ids[pos + 1] = ids[pos + 1], ids[pos]
    # Reasignar orden final
    for i, pid in enumerate(ids):
        db.execute("UPDATE productos SET orden = ? WHERE id = ?", (i, pid))
    db.commit()
    db.close()
    return jsonify({'ok': True})


@app.route('/admin/gamepoint', methods=['GET', 'POST'])
@admin_required
def admin_gamepoint_catalogo():
    db = get_db()

    if request.method == 'POST':
        tasa_txt = str(request.form.get('myr_usd_rate', '') or '').strip().replace(',', '.')
        margen_txt = str(request.form.get('margin_percent', '') or '').strip().replace(',', '.')
        margen_sub_txt = str(request.form.get('margin_percent_subscriber', '') or '').strip().replace(',', '.')

        tasa_myr_usd = _to_decimal(tasa_txt)
        margen_porcentaje = _to_decimal(margen_txt, Decimal('0'))
        margen_subscriber = _to_decimal(margen_sub_txt, Decimal('0'))

        if tasa_myr_usd is None or tasa_myr_usd <= 0:
            db.close()
            flash('La tasa MYR→USD es inválida. Ejemplo: 0.252205', 'error')
            return redirect(url_for('admin_gamepoint_catalogo'))

        if margen_porcentaje is None or margen_porcentaje < 0:
            db.close()
            flash('El margen % es inválido. Debe ser un número mayor o igual a 0.', 'error')
            return redirect(url_for('admin_gamepoint_catalogo'))

        if margen_subscriber is None or margen_subscriber < 0:
            db.close()
            flash('El margen suscriptor % es inválido. Debe ser un número mayor o igual a 0.', 'error')
            return redirect(url_for('admin_gamepoint_catalogo'))

        _config_set(db, 'gamepoint_myr_usd_rate', str(tasa_myr_usd))
        _config_set(db, 'gamepoint_margin_percent', str(margen_porcentaje))
        _config_set(db, 'gamepoint_margin_percent_subscriber', str(margen_subscriber))

        before = _snapshot_precios_proveedores(db)

        gp_detalle_cache = {}
        resumen = _actualizar_precios_gamepoint_desde_tasa(
            db,
            tasa_myr_usd,
            margen_porcentaje,
            target_column='precio',
            detalle_cache=gp_detalle_cache,
        )
        resumen_sub = _actualizar_precios_gamepoint_desde_tasa(
            db,
            tasa_myr_usd,
            margen_subscriber,
            target_column='precio_suscriptor',
            detalle_cache=gp_detalle_cache,
        )

        after = _snapshot_precios_proveedores(db)
        registro = _registrar_refresh_run_desde_snapshots(
            db,
            before,
            after,
            origen='admin_gamepoint',
            proveedor_filtro='gamepoint',
        )
        total_cambios = int(registro.get('total_cambios') or 0)
        msg_tg = ''
        if total_cambios > 0:
            msg_tg = _build_refresh_prices_telegram_message(db, registro.get('run_id'), total_cambios)

        db.commit()
        db.close()

        telegram_notificado = False
        telegram_error = ''
        if total_cambios > 0:
            telegram_result = enviar_telegram_con_keys(
                msg_tg,
                token_key='telegram_precios_bot_token',
                chat_id_key='telegram_precios_chat_id',
                activo_key='telegram_precios_activo',
                fallback_to_default=False,
                async_send=False,
            )
            telegram_notificado = bool(telegram_result.get('ok'))
            telegram_error = str(telegram_result.get('error') or '').strip()

        msg = (
            f"Precios GamePoint actualizados. "
            f"Normal: {resumen['actualizados']}/{resumen['total']} · "
            f"Suscriptor: {resumen_sub['actualizados']}/{resumen_sub['total']}"
        )
        if total_cambios > 0:
            if telegram_notificado:
                msg += " · Telegram: enviado"
            else:
                msg += f" · Telegram: error ({telegram_error or 'sin detalle'})"
        if resumen['errores']:
            msg += f" · Ejemplo: {resumen['errores'][0]}"
        elif resumen_sub['errores']:
            msg += f" · Ejemplo (suscriptor): {resumen_sub['errores'][0]}"
        flash(msg, 'success' if resumen['actualizados'] > 0 else 'warning')
        return redirect(url_for('admin_gamepoint_catalogo'))

    myr_usd_rate = _config_get(db, 'gamepoint_myr_usd_rate', '0.252205')
    margin_percent = _config_get(db, 'gamepoint_margin_percent', '6')
    margin_percent_subscriber = _config_get(db, 'gamepoint_margin_percent_subscriber', margin_percent)
    db.close()
    return render_template(
        'admin/gamepoint.html',
        myr_usd_rate=myr_usd_rate,
        margin_percent=margin_percent,
        margin_percent_subscriber=margin_percent_subscriber,
    )


@app.route('/admin/moogold', methods=['GET', 'POST'])
@admin_required
def admin_moogold_catalogo():
    db = get_db()

    if request.method == 'POST':
        margen_txt = str(request.form.get('moogold_margin_percent', '') or '').strip().replace(',', '.')
        margen_sub_txt = str(request.form.get('moogold_margin_percent_subscriber', '') or '').strip().replace(',', '.')

        margen_porcentaje = _to_decimal(margen_txt, Decimal('0'))
        margen_subscriber = _to_decimal(margen_sub_txt, Decimal('0'))

        if margen_porcentaje is None or margen_porcentaje < 0:
            db.close()
            flash('El margen MooGold % es inválido. Debe ser un número mayor o igual a 0.', 'error')
            return redirect(url_for('admin_moogold_catalogo'))

        if margen_subscriber is None or margen_subscriber < 0:
            db.close()
            flash('El margen suscriptor MooGold % es inválido. Debe ser un número mayor o igual a 0.', 'error')
            return redirect(url_for('admin_moogold_catalogo'))

        _config_set(db, 'moogold_margin_percent', str(margen_porcentaje))
        _config_set(db, 'moogold_margin_percent_subscriber', str(margen_subscriber))

        before = _snapshot_precios_proveedores(db)

        resumen = _actualizar_precios_moogold_desde_margen(db, margen_porcentaje, target_column='precio')
        resumen_sub = _actualizar_precios_moogold_desde_margen(db, margen_subscriber, target_column='precio_suscriptor')

        after = _snapshot_precios_proveedores(db)
        registro = _registrar_refresh_run_desde_snapshots(
            db,
            before,
            after,
            origen='admin_moogold',
            proveedor_filtro='moogold',
        )
        total_cambios = int(registro.get('total_cambios') or 0)
        msg_tg = ''
        if total_cambios > 0:
            msg_tg = _build_refresh_prices_telegram_message(db, registro.get('run_id'), total_cambios)

        db.commit()
        db.close()

        telegram_notificado = False
        telegram_error = ''
        if total_cambios > 0:
            telegram_result = enviar_telegram_con_keys(
                msg_tg,
                token_key='telegram_precios_bot_token',
                chat_id_key='telegram_precios_chat_id',
                activo_key='telegram_precios_activo',
                fallback_to_default=False,
                async_send=False,
            )
            telegram_notificado = bool(telegram_result.get('ok'))
            telegram_error = str(telegram_result.get('error') or '').strip()

        msg = (
            f"Precios MooGold actualizados. "
            f"Normal: {resumen['actualizados']}/{resumen['total']} · "
            f"Suscriptor: {resumen_sub['actualizados']}/{resumen_sub['total']}"
        )
        if total_cambios > 0:
            if telegram_notificado:
                msg += " · Telegram: enviado"
            else:
                msg += f" · Telegram: error ({telegram_error or 'sin detalle'})"
        if resumen['errores']:
            msg += f" · Ejemplo: {resumen['errores'][0]}"
        elif resumen_sub['errores']:
            msg += f" · Ejemplo (suscriptor): {resumen_sub['errores'][0]}"
        flash(msg, 'success' if resumen['actualizados'] > 0 else 'warning')
        return redirect(url_for('admin_moogold_catalogo'))

    margin_percent = _config_get(db, 'moogold_margin_percent', '6')
    margin_percent_subscriber = _config_get(db, 'moogold_margin_percent_subscriber', margin_percent)
    db.close()
    return render_template(
        'admin/moogold.html',
        categorias_moogold=_moogold_category_catalogo(),
        margin_percent=margin_percent,
        margin_percent_subscriber=margin_percent_subscriber,
    )


@app.route('/admin/moogold/productos', methods=['GET'])
@admin_required
def admin_moogold_productos():
    from moogold_api import obtener_saldo, listar_productos, detalle_producto

    category_id_raw = str(request.args.get('category_id', '') or '').strip()
    product_id_raw = str(request.args.get('product_id', '') or '').strip()

    saldo = obtener_saldo()
    if product_id_raw:
        try:
            detail = detalle_producto(int(product_id_raw))
            return jsonify({'saldo': saldo, 'detalle': detail})
        except Exception as e:
            return jsonify({'saldo': saldo, 'detalle': {'ok': False, 'error': str(e)}})

    if not category_id_raw:
        return jsonify({'saldo': saldo, 'productos': {'ok': False, 'error': 'Debes enviar category_id'}}), 400

    try:
        productos = listar_productos(int(category_id_raw))
    except Exception as e:
        productos = {'ok': False, 'error': str(e)}
    return jsonify({'saldo': saldo, 'productos': productos})


@app.route('/admin/moogold/actualizar-precios', methods=['POST'])
@admin_required
def admin_moogold_actualizar_precios():
    db = get_db()
    try:
        target = str(request.form.get('target', 'precio') or 'precio').strip().lower()
        if target not in ('precio', 'precio_suscriptor'):
            target = 'precio'

        margin_field = 'moogold_margin_percent_subscriber' if target == 'precio_suscriptor' else 'moogold_margin_percent'

        margen_txt = str(request.form.get(margin_field, '') or '').strip().replace(',', '.')
        if not margen_txt:
            margen_txt = _config_get(db, margin_field, '6')

        margen_porcentaje = _to_decimal(margen_txt, Decimal('0'))
        if margen_porcentaje is None or margen_porcentaje < 0:
            return jsonify({'ok': False, 'error': 'Margen inválido. Debe ser mayor o igual a 0.'}), 400

        _config_set(db, margin_field, str(margen_porcentaje))

        prod_id = int(request.form.get('producto_id', 0) or 0)
        if prod_id > 0:
            mg_product_id = int(request.form.get('moogold_product_id', 0) or 0)
            mg_variation_id = int(request.form.get('moogold_variation_id', 0) or 0)
            if mg_product_id <= 0 or mg_variation_id <= 0:
                return jsonify({'ok': False, 'error': 'Debes seleccionar Product ID y Variation ID de MooGold.'}), 400

            resultado = _actualizar_precio_moogold_producto_desde_margen(
                db,
                margen_porcentaje,
                mg_product_id,
                mg_variation_id,
                prod_id=prod_id,
                target_column=target,
            )
            if not resultado.get('ok'):
                return jsonify({'ok': False, 'error': resultado.get('error', 'No se pudo actualizar el precio')}), 400

            db.commit()
            return jsonify({
                'ok': True,
                'actualizado': 1,
                'producto_id': prod_id,
                'target': target,
                'precio': resultado.get('precio'),
                'costo_usd': resultado.get('costo_usd'),
                'margin_percent': str(margen_porcentaje),
            })

        if str(request.form.get('apply_all', '') or '').strip() != '1':
            return jsonify({'ok': False, 'error': 'Para actualización masiva debes confirmar apply_all=1.'}), 400

        resumen = _actualizar_precios_moogold_desde_margen(db, margen_porcentaje, target_column=target)
        db.commit()
        return jsonify({'ok': True, 'resumen': resumen, 'target': target, 'margin_percent': str(margen_porcentaje)})
    except Exception as e:
        db.rollback()
        return jsonify({'ok': False, 'error': str(e)}), 500
    finally:
        db.close()


@app.route('/admin/pincentral', methods=['GET', 'POST'])
@admin_required
def admin_pincentral_catalogo():
    db = get_db()
    if request.method == 'POST':
        prod_id = int(request.form.get('producto_id', 0) or 0)
        stock_minimo = int(request.form.get('stock_minimo', 0) or 0)
        stock_objetivo = int(request.form.get('stock_objetivo', 0) or 0)

        if prod_id > 0:
            if stock_minimo < 0:
                stock_minimo = 0
            if stock_objetivo < 0:
                stock_objetivo = 0
            db.execute(
                "UPDATE productos SET stock_minimo = ?, stock_objetivo = ? WHERE id = ? AND usa_pincentral = 1",
                (stock_minimo, stock_objetivo, prod_id),
            )
            db.commit()
            restock_pincentral_almacen_async(prod_id)
            flash(f'Stock actualizado en producto #{prod_id}', 'success')
        else:
            flash('Producto inválido para actualizar stock', 'error')

    productos_rows = db.execute(
        "SELECT p.id, p.nombre, p.pincentral_product_code, p.stock_minimo, p.stock_objetivo, "
        "(SELECT COUNT(*) FROM pines pi WHERE pi.producto_id = p.id AND pi.estado = 'disponible') as stock_disponible "
        "FROM productos p WHERE p.activo = 1 AND p.usa_pincentral = 1 "
        "ORDER BY p.nombre"
    ).fetchall()
    productos_locales = []
    for p in productos_rows:
        item = dict(p)
        rem = _pincentral_restock_cooldown_remaining(item.get('id'))
        item['cooldown_remaining_seconds'] = rem
        item['cooldown_remaining_human'] = f"{(rem // 60):02d}:{(rem % 60):02d}" if rem > 0 else '00:00'
        productos_locales.append(item)
    incidentes = db.execute(
        "SELECT i.id, i.contexto, i.pedido_id, i.producto_id, i.product_code, i.order_id, i.transaction_id, i.detalle, i.payload, i.fecha, p.nombre as producto_nombre "
        "FROM pincentral_incidentes i "
        "LEFT JOIN productos p ON p.id = i.producto_id "
        "ORDER BY i.id DESC LIMIT 30"
    ).fetchall()
    db.close()
    return render_template('admin/pincentral.html', productos_locales=productos_locales, incidentes=incidentes)


@app.route('/admin/pincentral/productos', methods=['GET'])
@admin_required
def admin_pincentral_productos():
    from pincentral_api import listar_productos, consultar_stock

    product_code = (request.args.get('product') or '').strip()
    productos = listar_productos()
    if product_code:
        stock = consultar_stock(product_code)
        return jsonify({'productos': productos, 'stock': stock})
    return jsonify({'productos': productos})


@app.route('/admin/gamepoint/productos', methods=['GET'])
@admin_required
def admin_gamepoint_productos():
    from gamepoint_api import listar_productos, detalle_producto, obtener_saldo
    product_id = request.args.get('product_id')
    saldo = obtener_saldo()
    if product_id:
        detalle = detalle_producto(int(product_id))
        return jsonify({'saldo': saldo, 'detalle': detalle})
    productos = listar_productos()
    return jsonify({'saldo': saldo, 'productos': productos})


@app.route('/admin/verificar-gamepoint', methods=['POST'])
@admin_required
def admin_verificar_gamepoint():
    """Verificar pedidos GamePoint en procesando y resolverlos a completado/cancelado."""
    from gamepoint_api import consultar_orden
    db = get_db()
    pedidos = db.execute(
        "SELECT p.id, p.usuario_id, p.total, p.estado, p.referencia_externa, p.nombre_jugador "
        "FROM pedidos p JOIN productos pr ON p.producto_id = pr.id "
        "WHERE p.estado = 'procesando' AND p.referencia_externa != '' AND p.referencia_externa IS NOT NULL "
        "AND pr.gamepoint_product_id > 0 "
        "AND p.fecha_pedido >= datetime('now', 'localtime', '-48 hours')"
    ).fetchall()
    # También buscar pedidos procesando SIN referencia (fallaron antes de obtener ref)
    pedidos_sin_ref = db.execute(
        "SELECT p.id, p.usuario_id, p.total, p.estado, p.fecha_pedido "
        "FROM pedidos p JOIN productos pr ON p.producto_id = pr.id "
        "WHERE p.estado = 'procesando' AND (p.referencia_externa IS NULL OR p.referencia_externa = '') "
        "AND pr.gamepoint_product_id > 0 "
        "AND p.fecha_pedido >= datetime('now', 'localtime', '-48 hours')"
    ).fetchall()
    db.close()
    verificados = 0
    confirmados = 0
    fallidos = 0
    for ped in pedidos:
        try:
            inquiry = consultar_orden(ped['referencia_externa'])
            gp_status = inquiry.get('status', '')
            if gp_status == 'failed':
                db2 = get_db()
                db2.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (ped['id'],))
                db2.commit()
                db2.close()
                recargar_saldo(ped['usuario_id'], ped['total'],
                               f"Reembolso: GamePoint FAIL pedido #{ped['id']} ({inquiry.get('reason', 'Sin razón')})")
                fallidos += 1
                enviar_webhook(ped['usuario_id'], {
                    'evento': 'pedido_actualizado', 'pedido_id': ped['id'],
                    'estado': 'cancelado', 'referencia': ped['referencia_externa'],
                    'razon': 'La recarga fue rechazada',
                    'reembolso': float(ped['total']),
                })
            elif gp_status == 'success' and ped['estado'] == 'procesando':
                db2 = get_db()
                nombre = inquiry.get('ingamename', ped['nombre_jugador'] or '')
                db2.execute("UPDATE pedidos SET estado = 'completado', nombre_jugador = ? WHERE id = ?", (nombre or ped['nombre_jugador'], ped['id']))
                db2.commit()
                db2.close()
                confirmados += 1
                enviar_webhook(ped['usuario_id'], {
                    'evento': 'pedido_actualizado', 'pedido_id': ped['id'],
                    'estado': 'completado', 'referencia': ped['referencia_externa'],
                    'nombre_jugador': nombre or ped['nombre_jugador'],
                })
        except Exception:
            pass
        verificados += 1
    # Pedidos sin referencia — notificar por Telegram para revisión manual
    for ped in pedidos_sin_ref:
        try:
            from datetime import datetime, timedelta
            fecha_ped = datetime.strptime(ped['fecha_pedido'], '%Y-%m-%d %H:%M:%S')
            if datetime.now() - fecha_ped > timedelta(minutes=5):
                from telegram_bot import enviar_telegram
                enviar_telegram(
                    f"🔔 <b>Pedido #{ped['id']} SIN REFERENCIA - Revisión necesaria</b>\n\n"
                    f"💵 Total: ${float(ped['total']):.4f}\n"
                    f"📅 Fecha: {ped['fecha_pedido']}\n\n"
                    f"📋 Revisa en GamePoint si se procesó y marca manualmente."
                )
                fallidos += 1
                verificados += 1
        except Exception:
            pass
    flash(f'Verificación: {verificados} revisados, {confirmados} confirmados, {fallidos} fallidos reembolsados.', 'success')
    return redirect(url_for('admin_pedidos'))


@app.route('/cron/verificar-gamepoint', methods=['GET'])
def cron_verificar_gamepoint():
    """Endpoint para cron job - verifica pedidos GamePoint"""
    cron_key = request.args.get('key', '')
    if cron_key != app.secret_key:
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    from gamepoint_api import consultar_orden
    db = get_db()
    pedidos = db.execute(
        "SELECT p.id, p.usuario_id, p.total, p.estado, p.referencia_externa, p.nombre_jugador "
        "FROM pedidos p JOIN productos pr ON p.producto_id = pr.id "
        "WHERE p.estado = 'procesando' AND p.referencia_externa != '' AND p.referencia_externa IS NOT NULL "
        "AND pr.gamepoint_product_id > 0 "
        "AND p.fecha_pedido >= datetime('now', 'localtime', '-48 hours')"
    ).fetchall()
    db.close()
    verificados = 0
    confirmados = 0
    fallidos = 0
    detalles = []
    for ped in pedidos:
        try:
            inquiry = consultar_orden(ped['referencia_externa'])
            gp_status = inquiry.get('status', '')
            if gp_status == 'failed':
                db2 = get_db()
                db2.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (ped['id'],))
                db2.commit()
                db2.close()
                recargar_saldo(ped['usuario_id'], ped['total'],
                               f"Reembolso auto: GamePoint FAIL pedido #{ped['id']} ({inquiry.get('reason', '')})")
                fallidos += 1
                detalles.append({'pedido': ped['id'], 'ref': ped['referencia_externa'], 'accion': 'reembolsado', 'reason': inquiry.get('reason', '')})
                enviar_webhook(ped['usuario_id'], {
                    'evento': 'pedido_actualizado',
                    'pedido_id': ped['id'],
                    'estado': 'cancelado',
                    'referencia': ped['referencia_externa'],
                    'razon': 'La recarga fue rechazada',
                    'reembolso': float(ped['total']),
                })
            elif gp_status == 'success' and ped['estado'] == 'procesando':
                db2 = get_db()
                nombre = inquiry.get('ingamename', ped['nombre_jugador'] or '')
                db2.execute("UPDATE pedidos SET estado = 'completado', nombre_jugador = ? WHERE id = ?", (nombre or ped['nombre_jugador'], ped['id']))
                db2.commit()
                db2.close()
                confirmados += 1
                detalles.append({'pedido': ped['id'], 'ref': ped['referencia_externa'], 'accion': 'confirmado'})
                enviar_webhook(ped['usuario_id'], {
                    'evento': 'pedido_actualizado',
                    'pedido_id': ped['id'],
                    'estado': 'completado',
                    'referencia': ped['referencia_externa'],
                    'nombre_jugador': nombre or ped['nombre_jugador'],
                })
        except Exception as e:
            detalles.append({'pedido': ped['id'], 'error': str(e)})
        verificados += 1
    # Pedidos procesando SIN referencia — notificar Telegram para revisión manual
    # (no auto-completar ni auto-cancelar, ya que no se puede verificar con GamePoint)
    db3 = get_db()
    pedidos_sin_ref = db3.execute(
        "SELECT p.id, p.usuario_id, p.total, p.fecha_pedido, pr.nombre as producto_nombre, p.id_juego "
        "FROM pedidos p JOIN productos pr ON p.producto_id = pr.id "
        "WHERE p.estado = 'procesando' AND (p.referencia_externa IS NULL OR p.referencia_externa = '') "
        "AND pr.gamepoint_product_id > 0 "
        "AND p.fecha_pedido >= datetime('now', 'localtime', '-48 hours') "
        "AND p.fecha_pedido <= datetime('now', 'localtime', '-5 minutes') "
        "AND p.fecha_pedido >= datetime('now', 'localtime', '-10 minutes')"
    ).fetchall()
    db3.close()
    for ped in pedidos_sin_ref:
        verificados += 1
        detalles.append({'pedido': ped['id'], 'accion': 'sin_ref_pendiente_revision'})
        from telegram_bot import enviar_telegram
        enviar_telegram(
            f"🔔 <b>Pedido #{ped['id']} SIN REFERENCIA - Revisión necesaria</b>\n\n"
            f"🎮 Producto: {ped['producto_nombre']}\n"
            f"🆔 ID Juego: {ped['id_juego'] or 'N/A'}\n"
            f"💵 Total: ${float(ped['total']):.4f}\n"
            f"📅 Fecha: {ped['fecha_pedido']}\n\n"
            f"📋 Revisa en GamePoint si se procesó y marca manualmente como completado o cancelado."
        )
    # Ejecutar restock automático de pines después de verificar
    restock_count = restock_pines()
    return jsonify({'ok': True, 'verificados': verificados, 'confirmados': confirmados, 'fallidos': fallidos, 'restock': restock_count, 'detalles': detalles})


@app.route('/cron/restock-pines', methods=['GET'])
def cron_restock_pines():
    """Endpoint para cron job - reabastece pines de productos Hype desde Gift Cards"""
    cron_key = request.args.get('key', '')
    if cron_key != app.secret_key:
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    transferidos = restock_pines()
    return jsonify({'ok': True, 'transferidos': transferidos})


@app.route('/cron/refresh-precios', methods=['GET'])
def cron_refresh_precios():
    """Endpoint para cron job - refresca precios GamePoint/MooGold y reporta cambios."""
    cron_key = request.args.get('key', '')
    if cron_key != app.secret_key:
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403

    db = get_db()
    try:
        result = _ejecutar_refresh_precios_proveedores(db, origen='cron_15m')
        if not result.get('ok'):
            db.rollback()
            db.close()
            return jsonify(result), 400

        run_id = int(result.get('run_id') or 0)

        base_url = str(request.args.get('base_url', '') or '').strip().rstrip('/')
        if not base_url:
            base_url = str(_config_get(db, 'admin_public_base_url', '') or '').strip().rstrip('/')
        if not base_url:
            base_url = request.url_root.rstrip('/')

        link_path = url_for('admin_reporte_refresh_precios', refresh_id=run_id)
        if base_url:
            reporte_url = f"{base_url}{link_path}"
        else:
            reporte_url = link_path

        total_cambios = int(result.get('total_cambios') or 0)
        telegram_msg = ''
        if total_cambios > 0:
            telegram_msg = _build_refresh_prices_telegram_message(db, run_id, total_cambios)

        db.commit()
        db.close()

        autorenovacion_global = {'renovadas': 0, 'saldo_insuficiente': 0, 'errores': 0, 'procesadas': 0}

        telegram_notificado = False
        telegram_error = ''
        if total_cambios > 0:
            telegram_result = enviar_telegram_con_keys(
                telegram_msg,
                token_key='telegram_precios_bot_token',
                chat_id_key='telegram_precios_chat_id',
                activo_key='telegram_precios_activo',
                fallback_to_default=False,
                async_send=False,
            )
            telegram_notificado = bool(telegram_result.get('ok'))
            telegram_error = str(telegram_result.get('error') or '').strip()
            if not telegram_notificado:
                print(f"[REFRESH_PRECIOS] No se pudo enviar Telegram de precios: {telegram_error or 'sin detalle'}")

        return jsonify({
            'ok': True,
            'run_id': run_id,
            'total_cambios': total_cambios,
            'reporte_url': reporte_url,
            'autorenovacion': autorenovacion_global,
            'telegram_notificado': telegram_notificado,
            'telegram_error': telegram_error,
        })
    except Exception as e:
        db.rollback()
        db.close()
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/admin/categorias/orden', methods=['POST'])
@admin_required
def admin_categoria_orden():
    data = request.get_json()
    cat_id = data.get('id')
    direccion = data.get('dir')
    db = get_db()
    cat = db.execute("SELECT id FROM categorias WHERE id = ?", (cat_id,)).fetchone()
    if not cat:
        db.close()
        return jsonify({'ok': False})
    # Obtener todas las categorías ordenadas
    todas = db.execute("SELECT id FROM categorias ORDER BY orden ASC, id ASC").fetchall()
    ids = [r['id'] for r in todas]
    # Normalizar
    for i, cid in enumerate(ids):
        db.execute("UPDATE categorias SET orden = ? WHERE id = ?", (i, cid))
    db.commit()
    # Encontrar posición actual
    pos = ids.index(cat_id)
    if direccion == 'up' and pos > 0:
        ids[pos], ids[pos - 1] = ids[pos - 1], ids[pos]
    elif direccion == 'down' and pos < len(ids) - 1:
        ids[pos], ids[pos + 1] = ids[pos + 1], ids[pos]
    # Reasignar orden final
    for i, cid in enumerate(ids):
        db.execute("UPDATE categorias SET orden = ? WHERE id = ?", (i, cid))
    db.commit()
    db.close()
    return jsonify({'ok': True})


@app.route('/admin/categorias/eliminar-lote', methods=['POST'])
@admin_required
def admin_categorias_eliminar_lote():
    data = request.get_json()
    ids = data.get('ids', [])
    db = get_db()
    eliminadas = 0
    omitidas = 0
    for cat_id in ids:
        prods = db.execute("SELECT COUNT(*) as c FROM productos WHERE categoria_id = ?", (cat_id,)).fetchone()
        if prods['c'] > 0:
            omitidas += 1
        else:
            db.execute("DELETE FROM categorias WHERE id = ?", (cat_id,))
            eliminadas += 1
    db.commit()
    db.close()
    return jsonify({'ok': True, 'eliminadas': eliminadas, 'omitidas': omitidas})


@app.route('/admin/categorias', methods=['GET', 'POST'])
@admin_required
def admin_categorias():
    db = get_db()
    if request.method == 'POST':
        accion = request.form.get('accion')
        if accion == 'crear':
            nombre = request.form.get('nombre', '').strip()
            slug = request.form.get('slug', '').strip().lower().replace(' ', '')
            icono = request.form.get('icono', 'fa-gamepad').strip()
            imagen = request.form.get('imagen_url', '').strip()
            archivo = request.files.get('imagen_file')
            uploaded = save_upload(archivo)
            if uploaded:
                imagen = uploaded
            tipo = request.form.get('tipo', 'juegos')
            descripcion = request.form.get('descripcion', '').strip()
            orden = int(request.form.get('orden', 0) or 0)
            verificar_nombre = 1 if request.form.get('verificar_nombre') else 0
            verificar_nombre_tipo = request.form.get('verificar_nombre_tipo', '').strip()
            validar_id_api = 1 if request.form.get('validar_id_api') else 0
            validar_id_api_tipo = request.form.get('validar_id_api_tipo', '').strip()
            if nombre and slug:
                try:
                    db.execute("INSERT INTO categorias (nombre, slug, icono, imagen, tipo, descripcion, orden, verificar_nombre, verificar_nombre_tipo, validar_id_api, validar_id_api_tipo) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                               (nombre, slug, icono, imagen, tipo, descripcion, orden, verificar_nombre, verificar_nombre_tipo, validar_id_api, validar_id_api_tipo))
                    db.commit()
                    flash(f'Categoría "{nombre}" creada', 'success')
                except Exception:
                    flash('Error: el slug ya existe', 'error')
        elif accion == 'editar':
            cat_id = int(request.form.get('categoria_id', 0))
            nombre = request.form.get('nombre', '').strip()
            slug = request.form.get('slug', '').strip().lower().replace(' ', '')
            icono = request.form.get('icono', 'fa-gamepad').strip()
            imagen = request.form.get('imagen_url', '').strip()
            archivo = request.files.get('imagen_file')
            uploaded = save_upload(archivo)
            if uploaded:
                imagen = uploaded
            elif not imagen:
                old = db.execute("SELECT imagen FROM categorias WHERE id = ?", (cat_id,)).fetchone()
                if old:
                    imagen = old['imagen']
            tipo = request.form.get('tipo', 'juegos')
            descripcion = request.form.get('descripcion', '').strip()
            orden = int(request.form.get('orden', 0) or 0)
            activo = 1 if request.form.get('activo') else 0
            verificar_nombre = 1 if request.form.get('verificar_nombre') else 0
            verificar_nombre_tipo = request.form.get('verificar_nombre_tipo', '').strip()
            validar_id_api = 1 if request.form.get('validar_id_api') else 0
            validar_id_api_tipo = request.form.get('validar_id_api_tipo', '').strip()
            if cat_id > 0 and nombre and slug:
                db.execute("UPDATE categorias SET nombre=?, slug=?, icono=?, imagen=?, tipo=?, descripcion=?, orden=?, activo=?, verificar_nombre=?, verificar_nombre_tipo=?, validar_id_api=?, validar_id_api_tipo=? WHERE id=?",
                           (nombre, slug, icono, imagen, tipo, descripcion, orden, activo, verificar_nombre, verificar_nombre_tipo, validar_id_api, validar_id_api_tipo, cat_id))
                db.commit()
                flash('Categoría actualizada', 'success')
        elif accion == 'eliminar':
            cat_id = int(request.form.get('categoria_id', 0))
            if cat_id > 0:
                prods = db.execute("SELECT COUNT(*) as c FROM productos WHERE categoria_id = ?", (cat_id,)).fetchone()
                if prods['c'] > 0:
                    flash(f'No se puede eliminar: tiene {prods["c"]} producto(s) asociados', 'error')
                else:
                    db.execute("DELETE FROM categorias WHERE id = ?", (cat_id,))
                    db.commit()
                    flash('Categoría eliminada', 'success')
        return redirect(url_for('admin_categorias'))

    categorias = db.execute("SELECT c.*, (SELECT COUNT(*) FROM productos p WHERE p.categoria_id = c.id) as total_productos FROM categorias c ORDER BY c.orden").fetchall()
    db.close()
    return render_template('admin/categorias.html', categorias=categorias)


@app.route('/admin/almacen', methods=['GET', 'POST'])
@admin_required
def admin_almacen():
    db = get_db()
    if request.method == 'POST':
        accion = request.form.get('accion')
        if accion == 'agregar':
            producto_id = int(request.form.get('producto_id', 0))
            pines_text = request.form.get('pines', '').strip()
            if producto_id > 0 and pines_text:
                pines_list = [p.strip() for p in pines_text.split('\n') if p.strip()]
                lote_id = f"MANUAL-{datetime.now().strftime('%Y%m%d%H%M%S')}-{producto_id}"
                count = 0
                duplicados = 0
                for pin in pines_list:
                    ok_insert, reason = _insertar_pin_disponible(db, producto_id, pin, lote_id)
                    if ok_insert:
                        count += 1
                    elif reason == 'duplicado':
                        duplicados += 1
                db.commit()
                if duplicados > 0:
                    flash(f'{count} PIN(es) agregados. {duplicados} duplicado(s) omitido(s).', 'warning')
                else:
                    flash(f'{count} PIN(es) agregados al almacén', 'success')
            else:
                flash('Selecciona un producto y agrega al menos un PIN', 'error')
        elif accion == 'eliminar':
            pin_id = int(request.form.get('pin_id', 0))
            if pin_id > 0:
                db.execute("DELETE FROM pines WHERE id = ? AND estado = 'disponible'", (pin_id,))
                db.commit()
                flash('PIN eliminado', 'success')
        elif accion == 'eliminar_todos':
            producto_id = int(request.form.get('producto_id', 0))
            if producto_id > 0:
                db.execute("DELETE FROM pines WHERE producto_id = ? AND estado = 'disponible'", (producto_id,))
                db.commit()
                flash('PINes disponibles eliminados', 'success')
        elif accion == 'revertir_ultimo_lote':
            producto_id = int(request.form.get('producto_id', 0))
            if producto_id > 0:
                ultimo = db.execute(
                    """
                    SELECT lote_id, fecha_agregado, COUNT(*) as total
                    FROM pines
                    WHERE producto_id = ? AND estado = 'disponible'
                    GROUP BY COALESCE(NULLIF(lote_id, ''), fecha_agregado)
                    ORDER BY MAX(id) DESC
                    LIMIT 1
                    """,
                    (producto_id,),
                ).fetchone()
                if ultimo:
                    lote_id = str((ultimo['lote_id'] if 'lote_id' in ultimo.keys() else '') or '').strip()
                    if lote_id:
                        cur = db.execute("DELETE FROM pines WHERE producto_id = ? AND estado = 'disponible' AND lote_id = ?", (producto_id, lote_id))
                    else:
                        cur = db.execute("DELETE FROM pines WHERE producto_id = ? AND estado = 'disponible' AND fecha_agregado = ? AND IFNULL(lote_id, '') = ''", (producto_id, ultimo['fecha_agregado']))
                    db.commit()
                    flash(f'Último lote revertido: {cur.rowcount} PIN(es) disponibles eliminados.', 'success')
                else:
                    flash('No hay lote disponible para revertir en este producto.', 'error')
        elif accion == 'stock_minimo':
            producto_id = int(request.form.get('producto_id', 0))
            stock_min = int(request.form.get('stock_minimo', 0))
            if producto_id > 0:
                db.execute("UPDATE productos SET stock_minimo = ? WHERE id = ?", (stock_min, producto_id))
                db.commit()
                prod_nombre = db.execute("SELECT nombre FROM productos WHERE id = ?", (producto_id,)).fetchone()
                flash(f'Alerta Telegram para "{prod_nombre["nombre"]}" configurada: stock mínimo = {stock_min}', 'success')
        return redirect(url_for('admin_almacen'))

    # Productos de categoría Gift Card + productos con APIs que consumen/entregan pines
    productos_api = db.execute(
        "SELECT p.*, c.nombre as categoria_nombre FROM productos p JOIN categorias c ON p.categoria_id = c.id "
        "WHERE p.activo = 1 AND (c.tipo = 'giftcards' OR p.usa_api = 1 OR p.usa_pincentral = 1) "
        "ORDER BY c.nombre, p.nombre"
    ).fetchall()
    productos_por_juego = {}
    for p in productos_api:
        juego = str(p['categoria_nombre'] or 'Sin categoría').strip() or 'Sin categoría'
        if juego not in productos_por_juego:
            productos_por_juego[juego] = []
        productos_por_juego[juego].append(p)
    # Stock por producto
    stock = {}
    for p in productos_api:
        count = db.execute("SELECT COUNT(*) as c FROM pines WHERE producto_id = ? AND estado = 'disponible'", (p['id'],)).fetchone()
        stock[p['id']] = count['c']
    # Filtro por producto
    filtro = request.args.get('producto_id', '')
    q = request.args.get('q', '').strip()
    try:
        page = int(request.args.get('page', '1'))
    except (ValueError, TypeError):
        page = 1
    if page < 1:
        page = 1
    per_page = 20
    offset = (page - 1) * per_page

    where_clauses = []
    params = []
    if filtro:
        where_clauses.append("pi.producto_id = ?")
        params.append(int(filtro))
    if q:
        like_q = f"%{q}%"
        q_pin_hash = pin_hash(q)
        where_clauses.append(
            "(CAST(pi.id AS TEXT) LIKE ? OR pr.nombre LIKE ? OR pi.estado LIKE ? OR "
            "IFNULL(pi.usado_por, '') LIKE ? OR IFNULL(pi.fecha_agregado, '') LIKE ? OR IFNULL(pi.pedido_id, '') LIKE ? "
            "OR pi.pin_hash = ?)"
        )
        params.extend([like_q, like_q, like_q, like_q, like_q, like_q, q_pin_hash])
    where_sql = f" WHERE {' AND '.join(where_clauses)}" if where_clauses else ''

    total_pines = db.execute(
        "SELECT COUNT(*) as c FROM pines pi JOIN productos pr ON pi.producto_id = pr.id" + where_sql,
        tuple(params)
    ).fetchone()['c']

    pines = db.execute(
        "SELECT pi.*, pr.nombre as producto_nombre "
        "FROM pines pi JOIN productos pr ON pi.producto_id = pr.id" + where_sql +
        " ORDER BY pi.estado ASC, pi.fecha_agregado DESC LIMIT ? OFFSET ?",
        tuple(params + [per_page, offset])
    ).fetchall()

    pines_seg = []
    for pin_row in pines:
        p = dict(pin_row)
        p['pin_mask'] = mask_pin(p.get('pin', ''))
        pines_seg.append(p)

    has_prev = page > 1
    has_more = (offset + len(pines_seg)) < total_pines

    db.close()
    return render_template(
        'admin/almacen.html',
        productos_api=productos_api,
        productos_por_juego=productos_por_juego,
        stock=stock,
        pines=pines_seg,
        filtro=filtro,
        q=q,
        page=page,
        per_page=per_page,
        total_pines=total_pines,
        has_prev=has_prev,
        has_more=has_more,
    )


@app.route('/admin/almacen/errores', methods=['GET', 'POST'])
@admin_required
def admin_almacen_errores():
    db = get_db()

    if request.method == 'POST':
        accion = request.form.get('accion', 'disponible').strip().lower()
        pin_id = int(request.form.get('pin_id', 0))
        if pin_id > 0:
            row = db.execute("SELECT id FROM pines WHERE id = ? AND estado = 'error'", (pin_id,)).fetchone()
            if row:
                if accion == 'eliminar':
                    db.execute("DELETE FROM pines WHERE id = ? AND estado = 'error'", (pin_id,))
                    db.commit()
                    flash(f'PIN #{pin_id} eliminado del almacén de errores', 'success')
                else:
                    db.execute(
                        "UPDATE pines SET estado = 'disponible', usado_por = NULL, pedido_id = NULL, fecha_usado = NULL WHERE id = ?",
                        (pin_id,),
                    )
                    db.commit()
                    flash(f'PIN #{pin_id} marcado nuevamente como disponible', 'success')
            else:
                flash('PIN no encontrado en estado error', 'error')
        else:
            flash('PIN inválido', 'error')

        q_back = request.form.get('q', '').strip()
        try:
            page_back = int(request.form.get('page', '1'))
        except (ValueError, TypeError):
            page_back = 1
        db.close()
        return redirect(url_for('admin_almacen_errores', q=q_back, page=page_back))

    q = request.args.get('q', '').strip()
    try:
        page = int(request.args.get('page', '1'))
    except (ValueError, TypeError):
        page = 1
    if page < 1:
        page = 1
    per_page = 30
    offset = (page - 1) * per_page

    where_clauses = ["pi.estado = 'error'"]
    params = []
    if q:
        like_q = f"%{q}%"
        q_pin_hash = pin_hash(q)
        where_clauses.append(
            "(CAST(pi.id AS TEXT) LIKE ? OR pr.nombre LIKE ? OR IFNULL(pi.usado_por, '') LIKE ? OR "
            "IFNULL(pi.fecha_agregado, '') LIKE ? OR IFNULL(pi.fecha_usado, '') LIKE ? OR IFNULL(pi.pedido_id, '') LIKE ? OR pi.pin_hash = ?)"
        )
        params.extend([like_q, like_q, like_q, like_q, like_q, like_q, q_pin_hash])

    where_sql = ' WHERE ' + ' AND '.join(where_clauses)

    total_pines = db.execute(
        "SELECT COUNT(*) as c FROM pines pi JOIN productos pr ON pi.producto_id = pr.id" + where_sql,
        tuple(params),
    ).fetchone()['c']

    pines = db.execute(
        "SELECT pi.*, pr.nombre as producto_nombre "
        "FROM pines pi JOIN productos pr ON pi.producto_id = pr.id" + where_sql +
        " ORDER BY IFNULL(pi.fecha_usado, pi.fecha_agregado) DESC LIMIT ? OFFSET ?",
        tuple(params + [per_page, offset]),
    ).fetchall()

    pines_seg = []
    for pin_row in pines:
        p = dict(pin_row)
        p['pin_mask'] = mask_pin(p.get('pin', ''))
        pines_seg.append(p)

    has_prev = page > 1
    has_more = (offset + len(pines_seg)) < total_pines

    db.close()
    return render_template(
        'admin/almacen_errores.html',
        pines=pines_seg,
        q=q,
        page=page,
        per_page=per_page,
        total_pines=total_pines,
        has_prev=has_prev,
        has_more=has_more,
    )


@app.route('/admin/almacen/pin/<int:pin_id>', methods=['GET'])
@admin_required
def admin_almacen_ver_pin(pin_id):
    db = get_db()
    pin_row = db.execute("SELECT id, pin FROM pines WHERE id = ?", (pin_id,)).fetchone()
    db.close()
    if not pin_row:
        return jsonify({'ok': False, 'error': 'PIN no encontrado'}), 404
    return jsonify({'ok': True, 'pin': decrypt_pin(pin_row['pin'])})


@app.route('/admin/pedidos')
@admin_required
def admin_pedidos():
    db = get_db()
    q = request.args.get('q', '').strip()
    estado = request.args.get('estado', '').strip().lower()
    estados_validos = {'pendiente', 'procesando', 'completado', 'cancelado'}
    if estado not in estados_validos:
        estado = ''
    try:
        page = int(request.args.get('page', '1'))
    except (ValueError, TypeError):
        page = 1
    if page < 1:
        page = 1
    per_page = 20
    offset = (page - 1) * per_page

    where_clauses = []
    params = []
    if estado:
        where_clauses.append("p.estado = ?")
        params.append(estado)

    if q:
        like_q = f"%{q}%"
        where_clauses.append(
            "(CAST(p.id AS TEXT) LIKE ? OR u.nombre LIKE ? OR pr.nombre LIKE ? OR "
            "IFNULL(p.id_juego, '') LIKE ? OR IFNULL(p.codigo_entregado, '') LIKE ? OR "
            "IFNULL(p.referencia_externa, '') LIKE ? OR p.estado LIKE ?)"
        )
        params.extend([like_q, like_q, like_q, like_q, like_q, like_q, like_q])

    where_sql = ''
    if where_clauses:
        where_sql = ' WHERE ' + ' AND '.join(where_clauses)

    total_pedidos = db.execute(
        "SELECT COUNT(*) as c FROM pedidos p "
        "JOIN usuarios u ON p.usuario_id = u.id "
        "JOIN productos pr ON p.producto_id = pr.id" + where_sql,
        tuple(params)
    ).fetchone()['c']
    pedidos = db.execute(
        "SELECT p.*, u.nombre as usuario_nombre, pr.nombre as producto_nombre "
        "FROM pedidos p "
        "JOIN usuarios u ON p.usuario_id = u.id "
        "JOIN productos pr ON p.producto_id = pr.id" + where_sql +
        " ORDER BY p.fecha_pedido DESC LIMIT ? OFFSET ?",
        tuple(params + [per_page, offset])
    ).fetchall()

    pagos_suscripcion = []
    if not estado or estado == 'completado':
        sub_where = ["t.tipo = 'compra'", "LOWER(IFNULL(t.descripcion, '')) LIKE ?"]
        sub_params = ['%suscripci%']
        if q:
            like_q = f"%{q}%"
            sub_where.append(
                "(CAST(t.id AS TEXT) LIKE ? OR u.nombre LIKE ? OR IFNULL(t.descripcion, '') LIKE ? OR CAST(t.monto AS TEXT) LIKE ? OR IFNULL(t.fecha, '') LIKE ?)"
            )
            sub_params.extend([like_q, like_q, like_q, like_q, like_q])

        pagos_suscripcion = db.execute(
            "SELECT t.id, t.usuario_id, t.monto as total, t.fecha as fecha_pedido, t.descripcion, "
            "u.nombre as usuario_nombre "
            "FROM transacciones t "
            "JOIN usuarios u ON t.usuario_id = u.id "
            "WHERE " + " AND ".join(sub_where) + " "
            "ORDER BY t.fecha DESC LIMIT 100",
            tuple(sub_params)
        ).fetchall()

    # Obtener PINes usados por cada pedido
    pines_por_pedido = {}
    pedido_ids = [ped['id'] for ped in pedidos]
    if pedido_ids:
        placeholders = ','.join(['?'] * len(pedido_ids))
        pines_rows = db.execute(
            f"SELECT id, pin, pedido_id FROM pines WHERE pedido_id IN ({placeholders}) ORDER BY id ASC",
            tuple(pedido_ids)
        ).fetchall()
        for pin in pines_rows:
            pines_por_pedido.setdefault(pin['pedido_id'], []).append({
                'id': pin['id'],
                'pin_mask': mask_pin(pin['pin'])
            })

    has_prev = page > 1
    has_more = (offset + len(pedidos)) < total_pedidos
    db.close()
    return render_template(
        'admin/pedidos.html',
        pedidos=pedidos,
        pagos_suscripcion=pagos_suscripcion,
        pines_por_pedido=pines_por_pedido,
        q=q,
        estado=estado,
        page=page,
        per_page=per_page,
        total_pedidos=total_pedidos,
        has_prev=has_prev,
        has_more=has_more,
    )


@app.route('/admin/pedido/<int:id>/estado', methods=['POST'])
@admin_required
def admin_cambiar_estado(id):
    estado_nuevo = request.form.get('estado', 'pendiente')
    db = get_db()
    pedido = db.execute(
        "SELECT usuario_id, estado, total FROM pedidos WHERE id = ?",
        (id,),
    ).fetchone()
    estado_anterior = str(pedido['estado'] or '').strip().lower() if pedido else ''
    total = float((pedido['total'] or 0) if pedido else 0)
    usuario_id = int(pedido['usuario_id'] if pedido else 0)

    db.execute("UPDATE pedidos SET estado = ? WHERE id = ?", (estado_nuevo, id))
    db.commit()

    reembolsado = False
    if estado_nuevo == 'cancelado' and estado_anterior not in ('cancelado', 'completado') and usuario_id > 0 and total > 0:
        try:
            recargar_saldo(usuario_id, total, f"Reembolso: Pedido #{id} cancelado manualmente")
            reembolsado = True
        except Exception as e:
            flash(f'Pedido cancelado, pero no se pudo reembolsar: {e}', 'warning')

    if reembolsado:
        try:
            enviar_webhook(usuario_id, {
                'evento': 'pedido_actualizado',
                'pedido_id': id,
                'estado': 'cancelado',
                'razon': 'Pedido cancelado manualmente por administrador',
                'reembolsado': True,
                'reembolso': float(total),
            })
        except Exception:
            pass
        flash(f'Pedido #{id} cancelado y reembolsado (${total:.2f})', 'success')
    else:
        flash(f'Pedido #{id} actualizado a {estado_nuevo}', 'success')

    db.close()
    return redirect(url_for('admin_pedidos'))


# ===== API KEY MANAGEMENT =====
@app.route('/mi-api', methods=['GET', 'POST'])
@login_required
def mi_api():
    db = get_db()
    user = db.execute("SELECT * FROM usuarios WHERE id = ?", (session['user_id'],)).fetchone()
    generated_api_key = None
    if request.method == 'POST':
        generated_api_key = rotate_api_key(session['user_id'])
        user = db.execute("SELECT * FROM usuarios WHERE id = ?", (session['user_id'],)).fetchone()
        flash('API Key regenerada exitosamente. Guárdala ahora: no se mostrará completa otra vez.', 'success')
    db.close()
    return render_template('mi_api.html', user=user, generated_api_key=generated_api_key)


@app.route('/api/docs')
def api_docs():
    return render_template('api_docs.html')


# ===== API PARA REVENDEDORES =====
@app.route('/api/v1/saldo', methods=['GET'])
@api_key_required
def api_saldo():
    user = request.api_user
    saldo = get_saldo(user['id'])
    return jsonify({'ok': True, 'saldo': saldo, 'nombre': user['nombre']})


@app.route('/api/v1/productos', methods=['GET'])
@api_key_required
def api_productos():
    import json as _json
    user = request.api_user
    suscripcion_activa = _suscripcion_activa_desde_row(user)
    db = get_db()
    productos = db.execute("SELECT p.id, p.nombre, p.descripcion, p.campos_cliente, p.precio, p.precio_suscriptor, p.usa_api, p.usa_razer, p.razer_paquete, p.usa_deltaforce, p.deltaforce_paquete, p.usa_pincentral, p.pincentral_product_code, p.gamepoint_product_id, p.gamepoint_fields, p.moogold_category_id, p.moogold_variation_id, p.moogold_fields, p.bloodstrike_package_id, p.rechazo_automatico, p.recarga_manual, c.nombre as categoria FROM productos p JOIN categorias c ON p.categoria_id = c.id WHERE p.activo = 1 ORDER BY c.orden, p.nombre").fetchall()
    db.close()
    result = []
    for p in productos:
        d = dict(p)
        precio_base = float(d.get('precio', 0) or 0)
        precio_suscriptor = float(d.pop('precio_suscriptor', 0) or 0)
        if suscripcion_activa and precio_suscriptor > 0:
            d['precio_normal'] = precio_base
            d['precio'] = precio_suscriptor
        else:
            d['precio'] = precio_base
        usa_api_hype = d.pop('usa_api', 0)
        usa_api_razer = d.pop('usa_razer', 0)
        razer_paquete = d.pop('razer_paquete', 0)
        usa_api_deltaforce = d.pop('usa_deltaforce', 0)
        deltaforce_paquete = d.pop('deltaforce_paquete', 0)
        usa_api_pincentral = d.pop('usa_pincentral', 0)
        pincentral_product_code = (d.pop('pincentral_product_code', '') or '').strip()
        moogold_category_id = int(d.pop('moogold_category_id', 0) or 0)
        moogold_variation_id = int(d.pop('moogold_variation_id', 0) or 0)
        moogold_fields_raw = d.pop('moogold_fields', '') or ''
        bloodstrike_package_id = str(d.pop('bloodstrike_package_id', '') or '').strip()
        usa_bloodstrike = bool(bloodstrike_package_id)
        usa_moogold = moogold_category_id > 0 and moogold_variation_id > 0
        # Parsear campos requeridos para que el revendedor sepa qué enviar
        fields_raw = d.pop('gamepoint_fields', '') or ''
        campos = []
        if fields_raw:
            try:
                campos = _json.loads(fields_raw)
            except Exception:
                campos = []
        campos_cliente = _parse_campos_cliente(d.pop('campos_cliente', ''))
        if campos_cliente:
            d['campos_requeridos'] = [{'nombre': f['name'], 'descripcion': f['label'], 'tipo': 'string', 'opciones': []} for f in campos_cliente]
        elif campos:
            d['campos_requeridos'] = [{'nombre': f['name'], 'descripcion': f['desc'], 'tipo': f['type'], 'opciones': f.get('options', [])} for f in campos]
        elif usa_moogold:
            mg_campos = _moogold_parse_field_defs(moogold_fields_raw)
            d['campos_requeridos'] = [
                {
                    'nombre': f.get('name', ''),
                    'descripcion': f.get('desc', f.get('name', '')),
                    'tipo': f.get('type', 'text'),
                    'opciones': f.get('options', []),
                }
                for f in mg_campos
                if f.get('name', '')
            ]
        elif usa_api_hype:
            d['campos_requeridos'] = [{'nombre': 'id_juego', 'descripcion': 'ID del jugador en Free Fire', 'tipo': 'string', 'opciones': []}]
        elif usa_bloodstrike:
            d['campos_requeridos'] = [{'nombre': 'id_juego', 'descripcion': 'ID del jugador', 'tipo': 'string', 'opciones': []}]
        elif usa_api_razer:
            d['campos_requeridos'] = [{'nombre': 'id_juego', 'descripcion': 'ID del jugador', 'tipo': 'string', 'opciones': []}]
        elif usa_api_deltaforce:
            d['campos_requeridos'] = [{'nombre': 'id_juego', 'descripcion': 'ID del jugador', 'tipo': 'string', 'opciones': []}]
        elif usa_api_pincentral:
            d['campos_requeridos'] = []
        else:
            d['campos_requeridos'] = []
        d.pop('gamepoint_product_id', None)
        d.pop('rechazo_automatico', None)
        d['procesamiento_manual'] = bool(d.pop('recarga_manual', 0))
        result.append(d)
    return jsonify({'ok': True, 'productos': result, 'suscripcion_activa': bool(suscripcion_activa)})


@app.route('/api/v1/comprar', methods=['POST'])
@api_key_required
def api_comprar():
    user = request.api_user
    data = request.get_json(silent=True) or {}
    merchant_ref = str(data.get('merchant_ref') or data.get('referencia') or '').strip()
    producto_id = data.get('producto_id', 0)
    cantidad = data.get('cantidad', 1)
    id_juego = data.get('id_juego', '')
    input2 = data.get('input2', '')

    if merchant_ref and len(merchant_ref) > 80:
        return jsonify({'ok': False, 'error': 'merchant_ref demasiado largo (máx 80 chars)'}), 400

    db = get_db()
    if merchant_ref:
        pedido_existente = db.execute(
            "SELECT id, estado FROM pedidos WHERE usuario_id = ? AND referencia_cliente = ? ORDER BY id DESC LIMIT 1",
            (user['id'], merchant_ref),
        ).fetchone()
        if pedido_existente:
            db.close()
            return jsonify({
                'ok': False,
                'error': 'merchant_ref ya usado en otro pedido',
                'pedido_id': int(pedido_existente['id']),
                'estado': str(pedido_existente['estado'] or ''),
                'merchant_ref': merchant_ref,
            }), 409


    prod = db.execute("SELECT p.*, c.nombre as categoria_nombre, c.tipo as categoria_tipo, c.validar_id_api, c.validar_id_api_tipo FROM productos p JOIN categorias c ON p.categoria_id = c.id WHERE p.id = ? AND p.activo = 1", (producto_id,)).fetchone()
    if not prod:
        db.close()
        return jsonify({'ok': False, 'error': 'Producto no encontrado'}), 404

    db.execute("CREATE TABLE IF NOT EXISTS usuario_api_categorias_bloqueadas (usuario_id INTEGER NOT NULL, categoria_id INTEGER NOT NULL, fecha TEXT DEFAULT (datetime('now','localtime')), PRIMARY KEY (usuario_id, categoria_id), FOREIGN KEY (usuario_id) REFERENCES usuarios(id), FOREIGN KEY (categoria_id) REFERENCES categorias(id))")
    categoria_bloqueada_api = db.execute(
        "SELECT 1 FROM usuario_api_categorias_bloqueadas WHERE usuario_id = ? AND categoria_id = ? LIMIT 1",
        (user['id'], int((prod['categoria_id'] if 'categoria_id' in prod.keys() else 0) or 0)),
    ).fetchone()
    if categoria_bloqueada_api:
        db.close()
        return jsonify({'ok': False, 'error': 'Categoría no habilitada para compras vía API en este usuario'}), 403

    if int((prod['rechazo_automatico'] if 'rechazo_automatico' in prod.keys() else 0) or 0):
        db.close()
        return jsonify({'ok': False, 'error': 'Producto temporalmente deshabilitado'}), 503

    # Validar que se envíe id_juego si el producto lo requiere (no aplica a gift cards sin campos)
    gp_fields_raw = ''
    try:
        gp_fields_raw = prod['gamepoint_fields'] or ''
    except Exception:
        pass
    usa_razer = prod['usa_razer'] if 'usa_razer' in prod.keys() else 0
    usa_deltaforce = prod['usa_deltaforce'] if 'usa_deltaforce' in prod.keys() else 0
    moogold_category_id = int((prod['moogold_category_id'] if 'moogold_category_id' in prod.keys() else 0) or 0)
    moogold_variation_id = int((prod['moogold_variation_id'] if 'moogold_variation_id' in prod.keys() else 0) or 0)
    moogold_fields_raw = (prod['moogold_fields'] if 'moogold_fields' in prod.keys() else '') or ''
    moogold_field_names = _moogold_parse_fields(moogold_fields_raw)
    mg_inputs = _extract_named_inputs(data, moogold_field_names)
    payload_account_fields = data.get('account_fields') if isinstance(data, dict) else None
    if isinstance(payload_account_fields, dict):
        for k, v in payload_account_fields.items():
            key = str(k or '').strip()
            val = str(v or '').strip()
            if key and val:
                mg_inputs[key] = val
    if moogold_field_names and not id_juego:
        id_juego = str(mg_inputs.get('mg_field_0', '') or mg_inputs.get(moogold_field_names[0], '') or '').strip()
    if moogold_field_names and not input2 and len(moogold_field_names) > 1:
        input2 = str(mg_inputs.get('mg_field_1', '') or mg_inputs.get(moogold_field_names[1], '') or '').strip()
    usa_moogold = moogold_category_id > 0 and moogold_variation_id > 0
    bloodstrike_package_id = str((prod['bloodstrike_package_id'] if 'bloodstrike_package_id' in prod.keys() else '') or '').strip()
    usa_bloodstrike = bool(bloodstrike_package_id)
    usa_pincentral = int((prod['usa_pincentral'] if 'usa_pincentral' in prod.keys() else 0) or 0)
    pincentral_entrega_directa = int((prod['pincentral_entrega_directa'] if 'pincentral_entrega_directa' in prod.keys() else 0) or 0)
    pincentral_recarga_directa = int((prod['pincentral_recarga_directa'] if 'pincentral_recarga_directa' in prod.keys() else 0) or 0)
    if prod['categoria_tipo'] == 'giftcards' and usa_pincentral and pincentral_entrega_directa:
        cantidad = 1
    freefire_levelpass = str((prod['freefire_levelpass'] if 'freefire_levelpass' in prod.keys() else '') or '').strip()
    requiere_id = (
        prod['usa_api']
        or usa_razer
        or usa_deltaforce
        or (prod['gamepoint_product_id'] and gp_fields_raw)
        or (usa_moogold and bool(moogold_field_names))
        or usa_bloodstrike
        or (usa_pincentral and pincentral_recarga_directa)
        or freefire_levelpass
    )
    if requiere_id and not id_juego:
        db.close()
        return jsonify({'ok': False, 'error': 'Se requiere id_juego (Player ID)'}), 400

    # Validar ID del jugador vía API si la categoría lo exige (no para pases de nivel)
    nombre_jugador_api = ''
    if int((prod['validar_id_api'] if 'validar_id_api' in prod.keys() else 0) or 0) and id_juego and not freefire_levelpass:
        tipo_val = str((prod['validar_id_api_tipo'] if 'validar_id_api_tipo' in prod.keys() else '') or '').strip() or 'freefire'
        val_api = verificar_nombre_jugador(tipo_val, id_juego, str(input2 or ''))
        if not val_api.get('ok'):
            db.close()
            return jsonify({'ok': False, 'error': val_api.get('error') or 'ID de jugador no válido'}), 400
        nombre_jugador_api = val_api.get('nombre', '')

    if freefire_levelpass:
        cached = _get_cached_levelpass(id_juego, producto_id)
        if cached:
            lp_check = {'ok': True, 'available': cached.get('available')}
        else:
            lp_check = _verificar_freefire_levelpass(id_juego, freefire_levelpass, validar_id_tipo=None)
            if lp_check.get('ok'):
                _set_cached_levelpass(id_juego, producto_id, lp_check.get('available'), lp_check.get('nombre'))
        if not lp_check.get('ok') or not lp_check.get('available'):
            db.close()
            return jsonify({'ok': False, 'error': lp_check.get('error') or 'Error al validar la disponibilidad del pase'}), 400

    precio_unitario = _precio_producto_para_usuario(prod, user)
    total = precio_unitario * cantidad
    desc_compra = f"API: {prod['nombre']} x{cantidad}"
    if precio_unitario != float((prod['precio'] if 'precio' in prod.keys() else 0) or 0):
        desc_compra += " (tarifa suscriptor)"
    resultado = descontar_saldo(user['id'], total, desc_compra)
    if resultado is None:
        saldo = get_saldo(user['id'])
        db.close()
        return jsonify({'ok': False, 'error': 'Saldo insuficiente', 'saldo': saldo, 'total': total}), 400

    db.execute(
        "INSERT INTO pedidos (usuario_id, producto_id, cantidad, total, id_juego, nombre_jugador, estado, referencia_cliente) VALUES (?,?,?,?,?,?,?,?)",
        (user['id'], producto_id, cantidad, total, id_juego, nombre_jugador_api, 'procesando', merchant_ref),
    )
    pedido_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    db.execute("UPDATE transacciones SET pedido_id = ? WHERE id = (SELECT id FROM transacciones WHERE usuario_id = ? AND pedido_id IS NULL ORDER BY id DESC LIMIT 1)",
               (pedido_id, user['id']))
    db.commit()

    nombre_jugador = ''
    user_id_api = user['id']

    # Gift Card con entrega directa PinCentral (sin almacenar en almacén, 1 PIN por pedido)
    if prod['categoria_tipo'] == 'giftcards' and usa_pincentral and pincentral_entrega_directa:
        from pincentral_api import autorizar_pins, capturar_pins

        product_code = str((prod['pincentral_product_code'] if 'pincentral_product_code' in prod.keys() else '') or '').strip()
        if not product_code:
            db.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
            db.commit()
            db.close()
            recargar_saldo(user_id_api, total, f"Reembolso API: Código PinCentral no configurado pedido #{pedido_id}")
            return jsonify({
                'ok': False, 'error': 'El producto no está configurado correctamente',
                'pedido_id': pedido_id, 'reembolsado': True, 'saldo_restante': get_saldo(user_id_api)
            }), 400

        db.close()
        order_id = f"APID{pedido_id}"
        client_name = (dict(user).get('nombre', '') if user else '').strip()
        client_email = (dict(user).get('email', '') if user else '').strip()

        try:
            auth = autorizar_pins(product_code, 1, order_id, client_name=client_name, client_email=client_email)
            auth_data = auth.get('data', {}) if isinstance(auth.get('data', {}), dict) else {}
            auth_status = _pincentral_status_normalizado(auth_data.get('status', ''))
            tx_id = str(auth_data.get('id', '') or '').strip()

            if (not auth.get('ok')) or (not _pincentral_autorizado(auth_status)) or not tx_id:
                db_err = get_db()
                db_err.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
                db_err.commit()
                db_err.close()
                recargar_saldo(user_id_api, total, f"Reembolso API: Error autorización PinCentral pedido #{pedido_id}")
                error_msg = auth.get('error') or auth_data.get('message') or f"Estado autorización: {auth_data.get('status', 'desconocido')}"
                return jsonify({
                    'ok': False, 'error': 'No fue posible procesar el pedido',
                    'pedido_id': pedido_id, 'reembolsado': True, 'saldo_restante': get_saldo(user_id_api)
                }), 400

            cap = capturar_pins(tx_id)
            cap_data = cap.get('data', {}) if isinstance(cap.get('data', {}), dict) else {}
            cap_status = _pincentral_status_normalizado(cap_data.get('status', ''))
            pins = cap_data.get('pins', []) if isinstance(cap_data.get('pins', []), list) else []
            errores_key = _pincentral_detectar_key_vacia(
                pins,
                contexto='api_pedido_directo',
                pedido_id=pedido_id,
                producto_id=producto_id,
                product_code=product_code,
                order_id=order_id,
                transaction_id=tx_id,
            )
            codigos = _formatear_pins_pincentral(pins)

            if (not cap.get('ok')) or (not _pincentral_capturado(cap_status)) or not codigos or errores_key:
                db_err = get_db()
                db_err.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
                db_err.commit()
                db_err.close()
                recargar_saldo(user_id_api, total, f"Reembolso API: Error captura PinCentral pedido #{pedido_id}")
                error_msg = cap.get('error') or cap_data.get('message') or f"Estado captura: {cap_data.get('status', 'desconocido')}"
                if errores_key:
                    error_msg = f"PinCentral devolvió key vacío: {'; '.join(errores_key)}"
                return jsonify({
                    'ok': False, 'error': 'No fue posible procesar el pedido',
                    'pedido_id': pedido_id, 'reembolsado': True, 'saldo_restante': get_saldo(user_id_api)
                }), 400

            db_ok = get_db()
            db_ok.execute(
                "UPDATE pedidos SET estado = 'completado', cantidad = 1, codigo_entregado = ?, referencia_externa = ? WHERE id = ?",
                (codigos, tx_id, pedido_id),
            )
            db_ok.commit()
            db_ok.close()
            enviar_webhook(user_id_api, {
                'evento': 'pedido_actualizado',
                'pedido_id': pedido_id,
                'estado': 'completado',
                'referencia': tx_id,
                'cantidad_codigos': 1,
                'mensaje': 'Código entregado'
            })
            return jsonify({
                'ok': True,
                'pedido_id': pedido_id,
                'estado': 'completado',
                'cantidad': 1,
                'codigo': codigos,
                'referencia': tx_id,
                'merchant_ref': merchant_ref,
                'total': total,
                'saldo_restante': get_saldo(user_id_api),
                'mensaje': 'Código entregado'
            })
        except Exception as e:
            db_err = get_db()
            db_err.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
            db_err.commit()
            db_err.close()
            recargar_saldo(user_id_api, total, f"Reembolso API: Excepción PinCentral directo pedido #{pedido_id}")
            return jsonify({
                'ok': False,
                'error': 'Error inesperado al procesar el pedido',
                'pedido_id': pedido_id,
                'reembolsado': True,
                'saldo_restante': get_saldo(user_id_api)
            }), 500

    # PinCentral Recarga directa
    if usa_pincentral and pincentral_recarga_directa:
        from pincentral_api import crear_recarga, validar_recarga

        product_code = str((prod['pincentral_product_code'] if 'pincentral_product_code' in prod.keys() else '') or '').strip()
        if not product_code:
            db.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
            db.commit()
            db.close()
            recargar_saldo(user_id_api, total, f"Reembolso API: Código PinCentral recarga no configurado pedido #{pedido_id}")
            return jsonify({'ok': False, 'error': 'El producto no está configurado correctamente', 'pedido_id': pedido_id, 'reembolsado': True, 'saldo_restante': get_saldo(user_id_api)}), 400

        db.close()
        nombre_partes = str((dict(user).get('nombre', '') if user else '') or '').split(' ', 1)
        first_name = nombre_partes[0] if nombre_partes else ''
        last_name = nombre_partes[1] if len(nombre_partes) > 1 else ''
        recargas_total = max(1, min(int((prod['pincentral_recarga_cantidad'] if 'pincentral_recarga_cantidad' in prod.keys() else 1) or 1), 20))

        # Validar la cuenta del jugador antes de intentar la recarga
        additional_data_2_api = str(data.get('additional_data_2', '') or '').strip()
        validacion = validar_recarga(
            product_code=product_code,
            service_user_id=id_juego,
            additional_data=input2,
            additional_data_2=additional_data_2_api,
        )
        val_data = validacion.get('data', {}) if isinstance(validacion.get('data', {}), dict) else {}
        val_status = val_data.get('status')
        val_ok = validacion.get('ok') and (
            val_status is True or str(val_status).strip().lower() in ('true', '1', 'ok', 'success')
        )
        if not val_ok:
            db2 = get_db()
            db2.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
            db2.commit()
            db2.close()
            error_msg = validacion.get('error') or val_data.get('message') or 'Cuenta o ID de jugador inválido'
            recargar_saldo(user_id_api, total, f"Reembolso API: Validación PinCentral fallida pedido #{pedido_id}: {error_msg}")
            return jsonify({'ok': False, 'error': f'La validación falló: {error_msg}', 'pedido_id': pedido_id, 'reembolsado': True, 'saldo_restante': get_saldo(user_id_api)}), 400

        try:
            refs = []
            receipts = []
            errores = []
            pendientes = 0
            for idx in range(1, recargas_total + 1):
                order_id = f"APIR{pedido_id}-{idx}"
                resultado_pc = crear_recarga(
                    product_code=product_code,
                    service_user_id=id_juego,
                    order_id=order_id,
                    additional_data=input2,
                    additional_data_2=str(data.get('additional_data_2', '') or '').strip(),
                )
                data_pc = resultado_pc.get('data', {}) if isinstance(resultado_pc.get('data', {}), dict) else {}
                estado_pc = _pincentral_estado_recarga(data_pc)
                ref = str(data_pc.get('id') or data_pc.get('receipt') or '').strip()
                receipt = str(data_pc.get('receipt') or '').strip()
                if ref:
                    refs.append(f"{idx}/{recargas_total}: {ref}")
                if receipt:
                    receipts.append(f"{idx}/{recargas_total}: {receipt}")
                if resultado_pc.get('ok') and estado_pc == 'completed':
                    continue
                if resultado_pc.get('ok') and estado_pc in ('created', 'retry'):
                    pendientes += 1
                    continue
                error_msg = resultado_pc.get('error') or data_pc.get('message') or f"Estado: {data_pc.get('status', 'desconocido')}"
                errores.append(f"{idx}/{recargas_total}: {error_msg}")
                break
            ref_text = '\n'.join(refs)
            receipt_text = '\n'.join(receipts) or id_juego
            db2 = get_db()
            if not errores and pendientes == 0:
                db2.execute("UPDATE pedidos SET estado = 'completado', nombre_jugador = ?, referencia_externa = ? WHERE id = ?", (receipt_text, ref_text, pedido_id))
                db2.commit()
                db2.close()
                enviar_webhook(user_id_api, {'evento': 'pedido_actualizado', 'pedido_id': pedido_id, 'estado': 'completado', 'referencia': ref_text, 'total': total})
                return jsonify({'ok': True, 'pedido_id': pedido_id, 'estado': 'completado', 'referencia': ref_text, 'merchant_ref': merchant_ref, 'total': total, 'saldo_restante': get_saldo(user_id_api), 'mensaje': f'Recarga completada ({recargas_total} recarga(s))'})
            if refs:
                db2.execute("UPDATE pedidos SET estado = 'procesando', nombre_jugador = ?, referencia_externa = ? WHERE id = ?", (receipt_text, ref_text, pedido_id))
                db2.commit()
                db2.close()
                return jsonify({'ok': True, 'pedido_id': pedido_id, 'estado': 'procesando', 'referencia': ref_text, 'merchant_ref': merchant_ref, 'total': total, 'saldo_restante': get_saldo(user_id_api), 'mensaje': 'Recarga procesando/parcial. Referencias guardadas.'}), 202
            db2.execute("UPDATE pedidos SET estado = 'cancelado', referencia_externa = ? WHERE id = ?", (ref_text, pedido_id))
            db2.commit()
            db2.close()
            recargar_saldo(user_id_api, total, f"Reembolso API: Error recarga PinCentral pedido #{pedido_id}")
            return jsonify({'ok': False, 'error': 'La recarga fue rechazada', 'pedido_id': pedido_id, 'reembolsado': True, 'saldo_restante': get_saldo(user_id_api), 'referencia': ref_text, 'merchant_ref': merchant_ref}), 400
        except Exception as e:
            db2 = get_db()
            db2.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
            db2.commit()
            db2.close()
            recargar_saldo(user_id_api, total, f"Reembolso API: Excepción PinCentral recarga pedido #{pedido_id}")
            return jsonify({'ok': False, 'error': 'Error inesperado al procesar el pedido', 'pedido_id': pedido_id, 'reembolsado': True, 'saldo_restante': get_saldo(user_id_api)}), 500

    # Blood Strike API
    if usa_bloodstrike:
        proveedor_recarga = str((prod['categoria_nombre'] if 'categoria_nombre' in prod.keys() else '') or '').strip() or ('Free Fire Promo Bonus' if bloodstrike_package_id.startswith('pinxtore_') else ('Free Fire' if bloodstrike_package_id.startswith('diamonds') else 'Blood Strike'))
        referencia_recarga = f"BS{pedido_id}" if proveedor_recarga == 'Blood Strike' else f"FF{pedido_id}"
        db.execute("UPDATE pedidos SET estado = 'procesando', referencia_externa = ? WHERE id = ?", (referencia_recarga, pedido_id))
        db.commit()
        db.close()
        threading.Thread(
            target=procesar_pedido_bloodstrike_background,
            args=(pedido_id, user_id_api, total, id_juego, bloodstrike_package_id),
            daemon=True,
        ).start()
        return jsonify({
            'ok': True,
            'pedido_id': pedido_id,
            'estado': 'procesando',
            'merchant_ref': merchant_ref,
            'referencia': referencia_recarga,
            'total': total,
            'saldo_restante': get_saldo(user_id_api),
            'mensaje': 'Pedido recibido y procesándose en segundo plano',
        }), 202

    # GamePoint API (recarga directa o gift card)
    if prod['gamepoint_product_id'] and prod['gamepoint_package_id']:
        gp_product_id = prod['gamepoint_product_id']
        gp_package_id = prod['gamepoint_package_id']
        es_manual = prod['recarga_manual'] if 'recarga_manual' in prod.keys() else 0
        db.close()
        try:
            from gamepoint_api import recarga_completa
            merchant_code = f"API{pedido_id}"
            gp_fields = {"input1": id_juego} if id_juego else {}
            if input2:
                gp_fields["input2"] = input2
            resultado_api = recarga_completa(
                product_id=gp_product_id,
                fields=gp_fields,
                package_id=gp_package_id,
                merchant_code=merchant_code,
                wait=False
            )
            db2 = get_db()
            if resultado_api.get('ok'):
                nombre_jugador = resultado_api.get('ingamename', '')
                ref = resultado_api.get('referenceno', '')
                es_giftcard_gp = (prod['categoria_tipo'] == 'giftcards')
                codigo = resultado_api.get('item', '') if es_giftcard_gp else ''
                gp_status = str(resultado_api.get('status', '') or '').strip().lower()
                if es_manual and gp_status == 'pending':
                    estado_final = 'procesando'
                else:
                    estado_final = 'completado'
                db2.execute("UPDATE pedidos SET estado = ?, nombre_jugador = ?, codigo_entregado = ?, referencia_externa = ? WHERE id = ?", (estado_final, nombre_jugador or ref, codigo, ref, pedido_id))
                db2.commit()
                db2.close()
                resp = {
                    'ok': True, 'pedido_id': pedido_id, 'estado': estado_final,
                    'total': total, 'saldo_restante': get_saldo(user_id_api),
                    'referencia': ref,
                    'merchant_ref': merchant_ref,
                }
                if es_manual:
                    if estado_final == 'procesando':
                        resp['mensaje'] = f'Pedido recibido (Ref: {ref}). Se confirmará automáticamente.'
                    else:
                        resp['nombre_jugador'] = nombre_jugador
                        resp['mensaje'] = f'Recarga completada para {nombre_jugador or id_juego} (Ref: {ref})'
                elif es_giftcard_gp and codigo:
                    resp['codigo'] = codigo
                    resp['mensaje'] = f'Código entregado: {codigo}'
                else:
                    resp['nombre_jugador'] = nombre_jugador
                    resp['mensaje'] = f'Recarga completada para {nombre_jugador or id_juego} (Ref: {ref})'
                # Notificar al revendedor por webhook
                enviar_webhook(user_id_api, {
                    'evento': 'pedido_actualizado',
                    'pedido_id': pedido_id,
                    'estado': estado_final,
                    'referencia': ref,
                    'nombre_jugador': nombre_jugador or '',
                    'codigo': codigo or '',
                    'total': total,
                })
                return jsonify(resp)
            else:
                if es_manual:
                    gp_status = str(resultado_api.get('status', '') or '').strip().lower()
                    ref = resultado_api.get('referenceno', '')
                    if gp_status == 'failed':
                        db2.execute("UPDATE pedidos SET estado = 'cancelado', referencia_externa = ? WHERE id = ?", (ref, pedido_id))
                        db2.commit()
                        db2.close()
                        recargar_saldo(user_id_api, total, f"Reembolso API: Error GamePoint pedido #{pedido_id}")
                        return jsonify({
                            'ok': False,
                            'error': 'Pedido rechazado',
                            'pedido_id': pedido_id,
                            'reembolsado': True,
                            'saldo_restante': get_saldo(user_id_api),
                            'referencia': ref,
                            'merchant_ref': merchant_ref,
                        }), 400
                    if ref:
                        db2.execute("UPDATE pedidos SET estado = 'procesando', referencia_externa = ? WHERE id = ?", (ref, pedido_id))
                    else:
                        db2.execute("UPDATE pedidos SET estado = 'procesando' WHERE id = ?", (pedido_id,))
                        from telegram_bot import enviar_telegram
                        enviar_telegram(
                            f"⚠️ <b>Pedido #{pedido_id} SIN REFERENCIA (API)</b>\n\n"
                            f"👤 Usuario: {user['nombre']}\n"
                            f"🎮 Producto: {prod['nombre']}\n"
                            f"🆔 ID Juego: {id_juego}\n"
                            f"💵 Total: ${total:.4f}\n"
                            f"❌ Error: {resultado_api.get('error', resultado_api.get('message', 'Sin respuesta'))}\n\n"
                            f"📋 Revisa manualmente en GamePoint y marca como completado o cancelado."
                        )
                    db2.commit()
                    db2.close()
                    return jsonify({
                        'ok': True, 'pedido_id': pedido_id, 'estado': 'procesando',
                        'total': total, 'saldo_restante': get_saldo(user_id_api),
                        'referencia': ref,
                        'merchant_ref': merchant_ref,
                        'mensaje': 'Pedido enviado pero respuesta incierta. Se verificará automáticamente.'
                    })
                else:
                    db2.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
                    db2.commit()
                    db2.close()
                    recargar_saldo(user_id_api, total, f"Reembolso API: Error GamePoint pedido #{pedido_id}")
                    return jsonify({
                        'ok': False, 'error': 'No fue posible procesar el pedido',
                        'pedido_id': pedido_id, 'reembolsado': True, 'saldo_restante': get_saldo(user_id_api)
                    }), 400
        except Exception as e:
            db2 = get_db()
            if es_manual:
                db2.execute("UPDATE pedidos SET estado = 'procesando' WHERE id = ?", (pedido_id,))
                db2.commit()
                db2.close()
                from telegram_bot import enviar_telegram
                enviar_telegram(
                    f"⚠️ <b>Pedido #{pedido_id} SIN REFERENCIA (excepción API)</b>\n\n"
                    f"👤 Usuario: {user['nombre']}\n"
                    f"🎮 Producto: {prod['nombre']}\n"
                    f"🆔 ID Juego: {id_juego}\n"
                    f"💵 Total: ${total:.4f}\n"
                    f"❌ Error: {str(e)}\n\n"
                    f"📋 Revisa manualmente en GamePoint y marca como completado o cancelado."
                )
                return jsonify({
                    'ok': True, 'pedido_id': pedido_id, 'estado': 'procesando',
                    'total': total, 'saldo_restante': get_saldo(user_id_api),
                    'merchant_ref': merchant_ref,
                    'mensaje': 'Pedido enviado pero hubo error de conexión. Se verificará automáticamente.'
                })
            else:
                db2.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
                db2.commit()
                db2.close()
                recargar_saldo(user_id_api, total, f"Reembolso API: Excepción GamePoint pedido #{pedido_id}")
                return jsonify({
                    'ok': False, 'error': 'Error inesperado al procesar el pedido', 'pedido_id': pedido_id,
                    'reembolsado': True, 'saldo_restante': get_saldo(user_id_api)
                }), 500

    # MooGold API
    elif usa_moogold:
        from moogold_api import crear_orden

        partner_order_id = merchant_ref or f"MGA{pedido_id}"
        account_fields = _moogold_build_account_fields(moogold_fields_raw, id_juego, input2, mg_inputs)
        moogold_category_orden = _moogold_category_efectiva(prod['categoria_tipo'], moogold_category_id)
        db.close()
        try:
            resultado_api = crear_orden(
                category=moogold_category_orden,
                variation_id=moogold_variation_id,
                quantity=cantidad,
                account_fields=account_fields,
                partner_order_id=partner_order_id,
            )
            db2 = get_db()
            if resultado_api.get('ok'):
                data_mg = resultado_api.get('data') if isinstance(resultado_api.get('data'), dict) else {}
                ref = _moogold_extract_ref(data_mg)
                mg_status = str(data_mg.get('status', '') or '').strip().lower()
                nombre_jugador = _moogold_extract_nombre(data_mg, id_juego)
                codigo = _moogold_extract_code(data_mg)

                if prod['categoria_tipo'] == 'giftcards' and not codigo and ref:
                    detail_data = _moogold_order_detail_safe(ref)
                    if detail_data:
                        mg_status = str(detail_data.get('order_status', '') or detail_data.get('status', '') or mg_status).strip().lower()
                        nombre_jugador = _moogold_extract_nombre(detail_data, nombre_jugador)
                        codigo = _moogold_extract_code(detail_data) or codigo

                estado_final = _estado_interno_desde_moogold(mg_status) if mg_status else 'procesando'
                if codigo and estado_final != 'cancelado':
                    estado_final = 'completado'

                if estado_final == 'completado' and codigo:
                    db2.execute(
                        "UPDATE pedidos SET estado = ?, nombre_jugador = ?, codigo_entregado = ?, referencia_externa = ?, referencia_cliente = ? WHERE id = ?",
                        (estado_final, nombre_jugador, codigo, ref, partner_order_id, pedido_id),
                    )
                else:
                    db2.execute(
                        "UPDATE pedidos SET estado = ?, nombre_jugador = ?, referencia_externa = ?, referencia_cliente = ? WHERE id = ?",
                        (estado_final, nombre_jugador, ref, partner_order_id, pedido_id),
                    )
                db2.commit()
                db2.close()

                return jsonify({
                    'ok': True,
                    'pedido_id': pedido_id,
                    'estado': estado_final,
                    'merchant_ref': partner_order_id,
                    'referencia': ref,
                    'total': total,
                    'saldo_restante': get_saldo(user_id_api),
                    'nombre_jugador': nombre_jugador,
                    'codigo': codigo,
                    'mensaje': 'Pedido recibido' if estado_final != 'completado' else 'Pedido completado',
                })

            db2.execute("UPDATE pedidos SET estado = 'cancelado', referencia_cliente = ? WHERE id = ?", (partner_order_id, pedido_id))
            db2.commit()
            db2.close()
            recargar_saldo(user_id_api, total, f"Reembolso API: Error MooGold pedido #{pedido_id}")
            return jsonify({
                'ok': False,
                'error': 'No fue posible procesar el pedido',
                'pedido_id': pedido_id,
                'merchant_ref': partner_order_id,
                'reembolsado': True,
                'saldo_restante': get_saldo(user_id_api),
            }), 400
        except Exception as e:
            db2 = get_db()
            db2.execute("UPDATE pedidos SET estado = 'cancelado', referencia_cliente = ? WHERE id = ?", (partner_order_id, pedido_id))
            db2.commit()
            db2.close()
            recargar_saldo(user_id_api, total, f"Reembolso API: Excepción MooGold pedido #{pedido_id}")
            return jsonify({
                'ok': False,
                'error': 'Error inesperado al procesar el pedido',
                'pedido_id': pedido_id,
                'merchant_ref': partner_order_id,
                'reembolsado': True,
                'saldo_restante': get_saldo(user_id_api),
            }), 500

    # API Razer separada (recarga directa)
    elif (prod['usa_razer'] if 'usa_razer' in prod.keys() else 0) and id_juego:
        paquete_principal = int((prod['razer_paquete'] if 'razer_paquete' in prod.keys() else 0) or 0)
        paquete_extra = int((prod['razer_paquete_extra'] if 'razer_paquete_extra' in prod.keys() else 0) or 0)
        paquete = paquete_extra if paquete_extra > 0 else paquete_principal
        if paquete <= 0:
            db.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
            db.commit()
            db.close()
            recargar_saldo(user_id_api, total, f"Reembolso API: Paquete Razer no configurado pedido #{pedido_id}")
            return jsonify({
                'ok': False, 'error': 'El producto no está configurado correctamente',
                'pedido_id': pedido_id, 'reembolsado': True, 'saldo_restante': get_saldo(user_id_api)
            }), 400

        db.execute("UPDATE pedidos SET estado = 'pendiente' WHERE id = ?", (pedido_id,))
        db.commit()
        db.close()
        threading.Thread(
            target=procesar_pedido_razer_background,
            args=(pedido_id, user_id_api, total, id_juego, paquete, cantidad),
            daemon=True,
        ).start()
        return jsonify({
            'ok': True,
            'pedido_id': pedido_id,
            'estado': 'pendiente',
            'merchant_ref': merchant_ref,
            'saldo_restante': get_saldo(user_id_api),
            'mensaje': 'Recarga en segundo plano. Consulta el pedido para ver si fue aprobado o rechazado.'
        })

    # API Delta Force separada (recarga directa)
    elif (prod['usa_deltaforce'] if 'usa_deltaforce' in prod.keys() else 0) and id_juego:
        paquete = int((prod['deltaforce_paquete'] if 'deltaforce_paquete' in prod.keys() else 0) or 0)
        if paquete <= 0:
            db.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
            db.commit()
            db.close()
            recargar_saldo(user_id_api, total, f"Reembolso API: Paquete Delta Force no configurado pedido #{pedido_id}")
            return jsonify({
                'ok': False, 'error': 'El producto no está configurado correctamente',
                'pedido_id': pedido_id, 'reembolsado': True, 'saldo_restante': get_saldo(user_id_api)
            }), 400

        db.execute("UPDATE pedidos SET estado = 'pendiente' WHERE id = ?", (pedido_id,))
        db.commit()
        db.close()
        threading.Thread(
            target=procesar_pedido_deltaforce_background,
            args=(pedido_id, user_id_api, total, id_juego, paquete, cantidad),
            daemon=True,
        ).start()
        return jsonify({
            'ok': True,
            'pedido_id': pedido_id,
            'estado': 'pendiente',
            'merchant_ref': merchant_ref,
            'saldo_restante': get_saldo(user_id_api),
            'mensaje': 'Recarga en segundo plano. Consulta el pedido para ver si fue aprobado o rechazado.'
        })

    # API PinCentral (solo PINs remotos) para productos no giftcard
    elif (prod['usa_pincentral'] if 'usa_pincentral' in prod.keys() else 0) and prod['categoria_tipo'] != 'giftcards':
        product_code = str((prod['pincentral_product_code'] if 'pincentral_product_code' in prod.keys() else '') or '').strip()
        if not product_code:
            db.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
            db.commit()
            db.close()
            recargar_saldo(user_id_api, total, f"Reembolso API: Código PinCentral no configurado pedido #{pedido_id}")
            return jsonify({
                'ok': False, 'error': 'El producto no está configurado correctamente',
                'pedido_id': pedido_id, 'reembolsado': True, 'saldo_restante': get_saldo(user_id_api)
            }), 400

        db.execute("UPDATE pedidos SET estado = 'pendiente' WHERE id = ?", (pedido_id,))
        db.commit()
        db.close()
        threading.Thread(
            target=procesar_pedido_pincentral_background,
            args=(pedido_id, user_id_api, total, product_code, cantidad),
            daemon=True,
        ).start()
        return jsonify({
            'ok': True,
            'pedido_id': pedido_id,
            'estado': 'pendiente',
            'merchant_ref': merchant_ref,
            'saldo_restante': get_saldo(user_id_api),
            'mensaje': 'Pedido en segundo plano. Consulta el pedido para ver códigos entregados.'
        })

    # Hype Games API (Free Fire con PINes) - Multi-canje
    elif prod['usa_api'] and id_juego:
        from hype_api import canjear_pin_completo
        # Restock automático si el stock está bajo
        restock_pines(producto_id)
        try:
            num_canjes = prod['canjes_por_compra'] or 1
        except (IndexError, KeyError):
            num_canjes = 1
        monto_api = prod['monto_api']

        # Determinar de qué producto tomar los pines
        pin_producto_id = producto_id
        if num_canjes > 1:
            try:
                origen = prod['pin_origen_producto_id'] or 0
            except (IndexError, KeyError):
                origen = 0
            if origen > 0:
                pin_producto_id = origen
            else:
                base = db.execute(
                    "SELECT id FROM productos WHERE usa_api = 1 AND monto_api = ? AND canjes_por_compra = 1 AND id != ? LIMIT 1",
                    (monto_api, producto_id)
                ).fetchone()
                if base:
                    pin_producto_id = base['id']
            restock_pines(pin_producto_id)

        # Reservar N PINes atómicamente
        db.execute("BEGIN IMMEDIATE")
        pin_rows = db.execute(
            "SELECT * FROM pines WHERE producto_id = ? AND estado = 'disponible' ORDER BY fecha_agregado ASC LIMIT ?",
            (pin_producto_id, num_canjes)
        ).fetchall()

        if len(pin_rows) < num_canjes:
            db.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
            db.commit()
            db.close()
            recargar_saldo(user_id_api, total, f"Reembolso API: Sin PINes suficientes pedido #{pedido_id} ({len(pin_rows)}/{num_canjes})")
            return jsonify({
                'ok': False, 'error': f'No hay suficientes PINes ({len(pin_rows)}/{num_canjes})',
                'pedido_id': pedido_id, 'reembolsado': True, 'saldo_restante': get_saldo(user_id_api)
            }), 400

        # Marcar todos los pines como usados
        pin_ids = []
        pin_codes = []
        for pr in pin_rows:
            pin_ids.append(pr['id'])
            pin_codes.append(decrypt_pin(pr['pin']))
            db.execute("UPDATE pines SET estado = 'usado', usado_por = ?, pedido_id = ?, fecha_usado = datetime('now','localtime') WHERE id = ?",
                       (user_id_api, pedido_id, pr['id']))
        db.commit()
        db.close()

        # Ejecutar canjes secuencialmente
        canjes_ok = 0
        nombre_jugador = ''
        error_msg = ''
        max_intentos_pin_error = 3
        for i, pin_code in enumerate(pin_codes):
            try:
                resultado_api = None
                for intento in range(1, max_intentos_pin_error + 1):
                    resultado_api = canjear_pin_completo(pin_code, id_juego, monto_api)
                    etapa_auditoria = f'canje_{i + 1}_try_{intento}'
                    _registrar_auditoria_recarga(
                        pedido_id=pedido_id,
                        usuario_id=user_id_api,
                        producto_id=producto_id,
                        proveedor='hype',
                        etapa=etapa_auditoria,
                        estado='ok' if resultado_api.get('ok') else 'error',
                        detalle=resultado_api.get('error', resultado_api.get('mensaje', '')),
                        referencia=str(resultado_api.get('reference', '') or ''),
                        payload=resultado_api,
                    )
                    if resultado_api.get('ok'):
                        break
                    should_retry_same_pin = bool(resultado_api.get('pin_error')) or bool(resultado_api.get('retry_same_pin'))
                    if not should_retry_same_pin:
                        break
                    if intento < max_intentos_pin_error:
                        continue

                if resultado_api.get('ok'):
                    canjes_ok += 1
                    nombre_jugador = resultado_api.get('username', '') or nombre_jugador
                else:
                    pin_error = bool(resultado_api.get('pin_error'))
                    paso_error = resultado_api.get('paso', 0)
                    if pin_error:
                        _marcar_pin_error_hype(
                            pin_id=pin_ids[i],
                            pedido_id=pedido_id,
                            id_juego=id_juego,
                            pin_code=pin_code,
                            motivo=f"{resultado_api.get('error', 'Error del proveedor Hype')} (tras {max_intentos_pin_error} intentos con el mismo PIN)",
                        )
                        error_msg = f"{resultado_api.get('error', 'Error en canje')} | Falló tras {max_intentos_pin_error} intentos con el mismo PIN"
                        break

                    db_fix = get_db()
                    if paso_error < 3:
                        db_fix.execute("UPDATE pines SET estado = 'disponible', usado_por = NULL, pedido_id = NULL, fecha_usado = NULL WHERE id = ?", (pin_ids[i],))
                    else:
                        db_fix.execute("UPDATE pines SET estado = 'error' WHERE id = ?", (pin_ids[i],))
                    db_fix.commit()
                    db_fix.close()
                    error_msg = resultado_api.get('error', 'Error en canje')
                    break
            except Exception as e:
                _registrar_auditoria_recarga(
                    pedido_id=pedido_id,
                    usuario_id=user_id_api,
                    producto_id=producto_id,
                    proveedor='hype',
                    etapa=f'canje_{i + 1}',
                    estado='exception',
                    detalle=str(e),
                    payload={'error': str(e)},
                )
                db_fix = get_db()
                db_fix.execute("UPDATE pines SET estado = 'disponible', usado_por = NULL, pedido_id = NULL, fecha_usado = NULL WHERE id = ?", (pin_ids[i],))
                db_fix.commit()
                db_fix.close()
                error_msg = str(e)
                break

        db3 = get_db()
        if canjes_ok == num_canjes:
            db3.execute("UPDATE pedidos SET estado = 'completado', nombre_jugador = ? WHERE id = ?", (nombre_jugador, pedido_id))
            db3.commit()
            db3.close()
            verificar_stock_bajo(pin_producto_id)
            return jsonify({
                'ok': True, 'pedido_id': pedido_id, 'estado': 'completado',
                'total': total, 'saldo_restante': get_saldo(user_id_api),
                'nombre_jugador': nombre_jugador, 'canjes_realizados': canjes_ok,
                'mensaje': f'{canjes_ok} recarga(s) aplicada(s) a {nombre_jugador} (ID: {id_juego})'
            })
        elif canjes_ok > 0:
            monto_parcial = (total / num_canjes) * (num_canjes - canjes_ok)
            db3.execute("UPDATE pedidos SET estado = 'completado', nombre_jugador = ? WHERE id = ?",
                       (f"{nombre_jugador} (parcial {canjes_ok}/{num_canjes})", pedido_id))
            db3.commit()
            db3.close()
            db4 = get_db()
            for j in range(canjes_ok, len(pin_ids)):
                db4.execute("UPDATE pines SET estado = 'disponible', usado_por = NULL, pedido_id = NULL, fecha_usado = NULL WHERE id = ? AND estado = 'usado'", (pin_ids[j],))
            db4.commit()
            db4.close()
            recargar_saldo(user_id_api, monto_parcial, f"Reembolso parcial API: {canjes_ok}/{num_canjes} canjes OK pedido #{pedido_id}")
            verificar_stock_bajo(pin_producto_id)
            return jsonify({
                'ok': True, 'pedido_id': pedido_id, 'estado': 'completado',
                'total': total, 'saldo_restante': get_saldo(user_id_api),
                'nombre_jugador': nombre_jugador, 'canjes_realizados': canjes_ok,
                'canjes_esperados': num_canjes, 'reembolso_parcial': monto_parcial,
                'mensaje': f'{canjes_ok}/{num_canjes} recargas completadas. Reembolso parcial: ${monto_parcial:.4f}'
            })
        else:
            db3.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
            db3.commit()
            db3.close()
            db4 = get_db()
            for pid in pin_ids:
                db4.execute("UPDATE pines SET estado = 'disponible', usado_por = NULL, pedido_id = NULL, fecha_usado = NULL WHERE id = ? AND estado = 'usado'", (pid,))
            db4.commit()
            db4.close()
            recargar_saldo(user_id_api, total, f"Reembolso API: Error canje pedido #{pedido_id}")
            return jsonify({
                'ok': False, 'error': error_msg, 'pedido_id': pedido_id,
                'reembolsado': True, 'saldo_restante': get_saldo(user_id_api)
            }), 400

    # Producto de categoría Gift Card — verificar si tiene pines en almacén para entregar
    if prod['categoria_tipo'] == 'giftcards':
        cant_pines = min(cantidad, 50)
        pines_disponibles = db.execute("SELECT * FROM pines WHERE producto_id = ? AND estado = 'disponible' LIMIT ?", (producto_id, cant_pines)).fetchall()
        if len(pines_disponibles) >= cant_pines:
            codigos = []
            for pin_row in pines_disponibles:
                db.execute("UPDATE pines SET estado = 'usado', usado_por = ?, pedido_id = ?, fecha_usado = datetime('now','localtime') WHERE id = ?",
                           (user_id_api, pedido_id, pin_row['id']))
                codigos.append(decrypt_pin(pin_row['pin']))
            todos_codigos = '\n'.join(codigos)
            db.execute("UPDATE pedidos SET estado = 'completado', codigo_entregado = ? WHERE id = ?", (todos_codigos, pedido_id))
            db.commit()
            db.close()
            verificar_stock_bajo(producto_id)
            if (prod['usa_pincentral'] if 'usa_pincentral' in prod.keys() else 0):
                restock_pincentral_almacen_async(producto_id)
            if (prod['usa_jadh'] if 'usa_jadh' in prod.keys() else 0):
                restock_jadh_almacen_async(producto_id)
            return jsonify({
                'ok': True, 'pedido_id': pedido_id, 'estado': 'completado',
                'total': total, 'saldo_restante': get_saldo(user_id_api),
                'codigos': codigos,
                'cantidad_entregada': len(codigos),
                'mensaje': f'{len(codigos)} código(s) entregado(s)'
            })
        else:
            if (prod['usa_pincentral'] if 'usa_pincentral' in prod.keys() else 0):
                restock_pincentral_almacen_async(producto_id)
            if (prod['usa_jadh'] if 'usa_jadh' in prod.keys() else 0):
                restock_jadh_almacen_async(producto_id)
            db.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
            db.commit()
            db.close()
            recargar_saldo(user_id_api, total, f"Reembolso API: Sin stock gift card pedido #{pedido_id}")
            disponibles = len(pines_disponibles)
            return jsonify({
                'ok': False, 'error': f'Stock insuficiente. Se necesitan {cant_pines} códigos pero solo hay {disponibles}',
                'pedido_id': pedido_id, 'reembolsado': True, 'saldo_restante': get_saldo(user_id_api)
            }), 400

    db.close()
    nuevo_saldo = get_saldo(user_id_api)
    return jsonify({
        'ok': True, 'pedido_id': pedido_id, 'estado': 'completado',
        'total': total, 'saldo_restante': nuevo_saldo,
        'merchant_ref': merchant_ref,
        'nombre_jugador': nombre_jugador,
        'mensaje': f'Recarga completada para {nombre_jugador} (ID: {id_juego})' if nombre_jugador else f'Pedido #{pedido_id} creado'
    })


@app.route('/api/v1/pedidos', methods=['GET'])
@api_key_required
def api_pedidos():
    user = request.api_user
    db = get_db()
    pedidos = db.execute("SELECT p.id, p.cantidad, p.total, p.id_juego, p.nombre_jugador, p.codigo_entregado, p.estado, p.referencia_externa, p.referencia_cliente, p.fecha_pedido, pr.nombre as producto FROM pedidos p JOIN productos pr ON p.producto_id = pr.id WHERE p.usuario_id = ? ORDER BY p.fecha_pedido DESC LIMIT 50", (user['id'],)).fetchall()
    db.close()
    return jsonify({'ok': True, 'pedidos': [dict(p) for p in pedidos]})


@app.route('/api/v1/pedido/<int:pedido_id>', methods=['GET'])
@api_key_required
def api_pedido_detalle(pedido_id):
    user = request.api_user
    db = get_db()
    pedido = db.execute("SELECT p.id, p.cantidad, p.total, p.id_juego, p.nombre_jugador, p.codigo_entregado, p.estado, p.referencia_externa, p.referencia_cliente, p.fecha_pedido, pr.nombre as producto FROM pedidos p JOIN productos pr ON p.producto_id = pr.id WHERE p.id = ? AND p.usuario_id = ?", (pedido_id, user['id'])).fetchone()
    db.close()
    if not pedido:
        return jsonify({'ok': False, 'error': 'Pedido no encontrado'}), 404
    return jsonify({'ok': True, 'pedido': dict(pedido)})


@app.route('/api/v1/recargas/status', methods=['GET'])
@api_key_required
def api_recarga_status_por_referencia():
    user = request.api_user
    merchant_ref = str(request.args.get('merchant_ref', '') or '').strip()
    pedido_id_raw = str(request.args.get('pedido_id', '') or '').strip()

    pedido_id = 0
    if pedido_id_raw:
        try:
            pedido_id = int(pedido_id_raw)
        except ValueError:
            return jsonify({'ok': False, 'error': 'pedido_id inválido'}), 400

    if not merchant_ref and pedido_id <= 0:
        return jsonify({'ok': False, 'error': 'Debes enviar merchant_ref o pedido_id'}), 400

    cache_key = f"{int(user['id'])}:{merchant_ref or ('id:' + str(pedido_id))}"
    cached = _recarga_status_cache_get(cache_key)
    if cached is not None:
        status_code, payload = cached
        return jsonify(payload), status_code

    db = get_db()
    try:
        if merchant_ref:
            pedido = db.execute(
                "SELECT p.id, p.usuario_id, p.cantidad, p.total, p.id_juego, p.nombre_jugador, p.codigo_entregado, p.estado, p.referencia_externa, p.referencia_cliente, p.fecha_pedido, pr.nombre as producto "
                "FROM pedidos p JOIN productos pr ON pr.id = p.producto_id "
                "WHERE p.usuario_id = ? AND p.referencia_cliente = ? ORDER BY p.id DESC LIMIT 1",
                (user['id'], merchant_ref),
            ).fetchone()
        else:
            pedido = db.execute(
                "SELECT p.id, p.usuario_id, p.cantidad, p.total, p.id_juego, p.nombre_jugador, p.codigo_entregado, p.estado, p.referencia_externa, p.referencia_cliente, p.fecha_pedido, pr.nombre as producto "
                "FROM pedidos p JOIN productos pr ON pr.id = p.producto_id "
                "WHERE p.usuario_id = ? AND p.id = ? LIMIT 1",
                (user['id'], pedido_id),
            ).fetchone()
    finally:
        db.close()

    if not pedido:
        payload = {'ok': False, 'error': 'Pedido no encontrado'}
        _recarga_status_cache_set(cache_key, 404, payload)
        return jsonify(payload), 404

    estado_interno = str(pedido['estado'] or '').strip().lower()
    estado_api = {
        'pendiente': 'pending',
        'procesando': 'processing',
        'completado': 'completed',
        'cancelado': 'failed',
    }.get(estado_interno, 'processing')

    payload = {
        'ok': True,
        'pedido_id': int(pedido['id']),
        'merchant_ref': str(pedido['referencia_cliente'] or ''),
        'status': estado_api,
        'estado': estado_interno,
        'producto': str(pedido['producto'] or ''),
        'id_juego': str(pedido['id_juego'] or ''),
        'nombre_jugador': str(pedido['nombre_jugador'] or ''),
        'codigo': str(pedido['codigo_entregado'] or ''),
        'referencia': str(pedido['referencia_externa'] or ''),
        'cantidad': int(pedido['cantidad'] or 0),
        'total': float(pedido['total'] or 0),
        'fecha': str(pedido['fecha_pedido'] or ''),
    }
    _recarga_status_cache_set(cache_key, 200, payload)
    return jsonify(payload)


@app.route('/api/v1/transacciones', methods=['GET'])
@api_key_required
def api_transacciones():
    user = request.api_user
    db = get_db()
    trans = db.execute("SELECT id, tipo, monto, saldo_anterior, saldo_nuevo, descripcion, fecha FROM transacciones WHERE usuario_id = ? ORDER BY fecha DESC LIMIT 50", (user['id'],)).fetchall()
    db.close()
    return jsonify({'ok': True, 'transacciones': [dict(t) for t in trans]})


@app.route('/webhook/moogold', methods=['GET', 'POST'])
def webhook_moogold():
    if request.method == 'GET':
        return jsonify({
            'ok': True,
            'service': 'moogold',
            'callback_url': 'https://tiendagiftven.tech/webhook/moogold',
        })

    token_cfg = str(config.MOOGOLD_CALLBACK_TOKEN or '').strip()
    if token_cfg:
        token_in = str(request.args.get('token', '') or request.headers.get('X-Callback-Token', '') or '').strip()
        if token_in != token_cfg:
            return jsonify({'status': 'error', 'message': 'Unauthorized callback token'}), 401

    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({'status': 'error', 'message': 'Invalid JSON payload'}), 400

    order_id = str(payload.get('order_id', '') or '').strip()
    partner_order_id = str(payload.get('partner_order_id', '') or payload.get('partnerOrderId', '') or '').strip()
    estado_moogold = str(payload.get('status', '') or '').strip()

    if not estado_moogold:
        return jsonify({'status': 'error', 'message': 'Missing status'}), 400
    if not order_id and not partner_order_id:
        return jsonify({'status': 'error', 'message': 'Missing order reference'}), 400

    db = get_db()
    try:
        pedido = None
        if order_id:
            pedido = db.execute(
                "SELECT id, usuario_id, producto_id, total, estado, nombre_jugador, referencia_externa, referencia_cliente "
                "FROM pedidos WHERE referencia_externa = ? ORDER BY id DESC LIMIT 1",
                (order_id,),
            ).fetchone()
        if not pedido and partner_order_id:
            pedido = db.execute(
                "SELECT id, usuario_id, producto_id, total, estado, nombre_jugador, referencia_externa, referencia_cliente "
                "FROM pedidos WHERE referencia_cliente = ? ORDER BY id DESC LIMIT 1",
                (partner_order_id,),
            ).fetchone()

        if not pedido:
            db.commit()
            return jsonify({'status': 'success', 'message': 'Callback recibido sin pedido local asociado'}), 200

        resultado = _procesar_callback_moogold(db, pedido, payload)
        db.commit()
        return jsonify({'status': 'success', 'message': 'Callback procesado', 'resultado': resultado}), 200
    except Exception as e:
        db.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        db.close()


@app.route('/api/v1/webhook', methods=['GET', 'POST'])
@api_key_required
def api_webhook():
    user = request.api_user
    if request.method == 'GET':
        webhook_url = (dict(user).get('webhook_url', '') if user else '') or ''
        return jsonify({'ok': True, 'webhook_url': webhook_url})
    data = request.get_json() or {}
    url = (data.get('url') or '').strip()
    if url and not url.startswith('http'):
        return jsonify({'ok': False, 'error': 'La URL debe empezar con http:// o https://'}), 400
    db = get_db()
    db.execute("UPDATE usuarios SET webhook_url = ? WHERE id = ?", (url, user['id']))
    db.commit()
    db.close()
    if url:
        return jsonify({'ok': True, 'mensaje': f'Webhook registrado: {url}'})
    return jsonify({'ok': True, 'mensaje': 'Webhook eliminado'})


def enviar_webhook(usuario_id, pedido_data):
    """Envía notificación webhook al revendedor si tiene URL configurada.
    Se ejecuta en segundo plano, guarda logs y reintenta ante fallos."""
    import json
    import threading
    import time
    import requests as req

    def _do_send():
        db = None
        try:
            db = get_db()
            db.executescript("""
                CREATE TABLE IF NOT EXISTS webhook_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    usuario_id INTEGER NOT NULL,
                    pedido_id INTEGER,
                    url TEXT NOT NULL,
                    payload TEXT DEFAULT '',
                    exitoso INTEGER DEFAULT 0,
                    intentos INTEGER DEFAULT 0,
                    status_code INTEGER DEFAULT 0,
                    respuesta TEXT DEFAULT '',
                    error TEXT DEFAULT '',
                    fecha TEXT DEFAULT (datetime('now','localtime'))
                );
                CREATE INDEX IF NOT EXISTS idx_webhook_logs_usuario ON webhook_logs(usuario_id);
                CREATE INDEX IF NOT EXISTS idx_webhook_logs_pedido ON webhook_logs(pedido_id);
                CREATE INDEX IF NOT EXISTS idx_webhook_logs_fecha ON webhook_logs(fecha);
            """)
            user = db.execute("SELECT webhook_url FROM usuarios WHERE id = ?", (usuario_id,)).fetchone()
            webhook_url = (user['webhook_url'] or '') if user else ''
            if not webhook_url:
                db.close()
                return

            payload_str = json.dumps(pedido_data)
            timeout = int(os.environ.get('WEBHOOK_TIMEOUT_SECONDS', '30'))
            max_intentos = max(1, int(os.environ.get('WEBHOOK_MAX_RETRIES', '3')))
            intentos = 0
            exitoso = 0
            status_code = 0
            respuesta = ''
            error_str = ''

            while intentos < max_intentos and not exitoso:
                intentos += 1
                try:
                    r = req.post(
                        webhook_url,
                        json=pedido_data,
                        timeout=timeout,
                        headers={'User-Agent': 'tiendagiftven-webhook/1.0'},
                    )
                    status_code = r.status_code
                    try:
                        respuesta = r.text[:2000]
                    except Exception:
                        respuesta = ''
                    if 200 <= status_code < 300:
                        exitoso = 1
                        break
                    error_str = f'HTTP {status_code}'
                    # No reintentar errores 4xx del cliente
                    if status_code < 500:
                        break
                except Exception as e:
                    error_str = str(e)
                    respuesta = ''
                if intentos < max_intentos and not exitoso:
                    time.sleep(2)

            db.execute(
                "INSERT INTO webhook_logs (usuario_id, pedido_id, url, payload, exitoso, intentos, status_code, respuesta, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    usuario_id,
                    pedido_data.get('pedido_id'),
                    webhook_url,
                    payload_str,
                    1 if exitoso else 0,
                    intentos,
                    status_code,
                    respuesta,
                    error_str,
                ),
            )
            db.commit()
        except Exception:
            pass
        finally:
            if db:
                try:
                    db.close()
                except Exception:
                    pass

    try:
        threading.Thread(target=_do_send, daemon=True).start()
    except Exception:
        pass


iniciar_worker_restock_pincentral_global()


if __name__ == '__main__':
    init_db()
    debug_mode = os.environ.get('FLASK_DEBUG', '0') == '1'
    host = os.environ.get('FLASK_HOST', '127.0.0.1')
    port = int(os.environ.get('PORT', '5000'))
    app.run(debug=debug_mode, host=host, port=port)
