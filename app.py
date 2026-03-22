from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from functools import wraps
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from models import (
    init_db, get_db, get_user_by_id, get_user_by_email, get_user_by_api_key,
    create_user, get_saldo, recargar_saldo, descontar_saldo
)
import config
import secrets
import os
import uuid
import sqlite3

app = Flask(__name__)
app.secret_key = config.SECRET_KEY

# Seguridad de cookies de sesión
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
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


init_db()


def verificar_nombre_jugador(tipo, player_id, zone_id=''):
    """Consulta APIs externas para obtener el nombre del jugador según el tipo de juego."""
    import requests as ext_requests
    try:
        if tipo == 'freefire':
            r = ext_requests.get(
                f"https://tiendagiftven.net/conexion_api/api.php?action=ValidarParametros&id={player_id}",
                timeout=10
            )
            data = r.json()
            if data.get('alerta') == 'green' and data.get('nickname'):
                return {'ok': True, 'nombre': data['nickname']}
            return {'ok': False, 'error': 'ID no encontrado'}

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
            data = r.json()
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


# ===== DECORADORES =====
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Inicia sesión para continuar', 'error')
            return redirect(url_for('login'))
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
        api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
        if not api_key:
            return jsonify({'error': 'API key requerida'}), 401
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
            if not user['activo']:
                flash('Tu cuenta está pendiente de aprobación. El administrador debe activarla antes de que puedas acceder.', 'error')
                return render_template('login.html')
            session['user_id'] = user['id']
            session['user_nombre'] = user['nombre']
            session['user_rol'] = user['rol']
            db = get_db()
            db.execute("UPDATE usuarios SET ultimo_login = datetime('now','localtime') WHERE id = ?", (user['id'],))
            db.commit()
            db.close()
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
        if not nombre or not email or not password:
            flash('Todos los campos son obligatorios', 'error')
            return render_template('registro.html')
        if len(password) < 6:
            flash('La contraseña debe tener al menos 6 caracteres', 'error')
            return render_template('registro.html')
        user = create_user(nombre, email, password, telefono)
        if not user:
            flash('El email ya está registrado', 'error')
            return render_template('registro.html')
        flash('Registro exitoso. Tu cuenta debe ser aprobada por el administrador antes de poder acceder.', 'success')
        return redirect(url_for('login'))
    return render_template('registro.html')


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


# ===== DASHBOARD =====
@app.route('/dashboard')
@login_required
def dashboard():
    user = get_user_by_id(session['user_id'])
    db = get_db()
    stats = db.execute("SELECT COUNT(*) as total_pedidos, COALESCE(SUM(total), 0) as total_gastado FROM pedidos WHERE usuario_id = ?", (user['id'],)).fetchone()
    ultimos = db.execute("SELECT p.*, pr.nombre as producto_nombre FROM pedidos p JOIN productos pr ON p.producto_id = pr.id WHERE p.usuario_id = ? ORDER BY p.fecha_pedido DESC LIMIT 5", (user['id'],)).fetchall()
    categorias = db.execute("SELECT c.*, (SELECT COUNT(*) FROM productos p WHERE p.categoria_id = c.id AND p.activo = 1) as total_productos FROM categorias c WHERE c.activo = 1 ORDER BY c.orden").fetchall()
    saldo = get_saldo(user['id'])
    db.close()
    return render_template('dashboard.html', user=user, stats=stats, ultimos=ultimos, categorias=categorias, saldo=saldo)


# ===== CATALOGO =====
@app.route('/catalogo')
@login_required
def catalogo():
    db = get_db()
    categorias = db.execute("SELECT c.*, (SELECT COUNT(*) FROM productos p WHERE p.categoria_id = c.id AND p.activo = 1) as total_productos FROM categorias c WHERE c.activo = 1 ORDER BY c.orden").fetchall()
    db.close()
    return render_template('catalogo.html', categorias=categorias)


@app.route('/catalogo/<slug>')
@login_required
def catalogo_juego(slug):
    db = get_db()
    cat = db.execute("SELECT * FROM categorias WHERE slug = ? AND activo = 1", (slug,)).fetchone()
    if not cat:
        flash('Juego no encontrado', 'error')
        return redirect(url_for('catalogo'))
    productos = db.execute("SELECT * FROM productos WHERE categoria_id = ? AND activo = 1 ORDER BY orden ASC, precio ASC", (cat['id'],)).fetchall()
    db.close()
    return render_template('catalogo_juego.html', categoria=cat, productos=productos)


@app.route('/producto/<int:id>')
@login_required
def producto(id):
    import json as _json
    db = get_db()
    prod = db.execute("SELECT p.*, c.nombre as categoria_nombre, c.slug as categoria_slug, c.tipo as categoria_tipo, c.verificar_nombre, c.verificar_nombre_tipo FROM productos p JOIN categorias c ON p.categoria_id = c.id WHERE p.id = ? AND p.activo = 1", (id,)).fetchone()
    db.close()
    if not prod:
        flash('Producto no encontrado', 'error')
        return redirect(url_for('catalogo'))
    # Convertir a dict y parsear gamepoint_fields JSON
    prod_dict = dict(prod)
    if prod_dict.get('gamepoint_fields'):
        try:
            prod_dict['gamepoint_fields'] = _json.loads(prod_dict['gamepoint_fields'])
        except Exception:
            prod_dict['gamepoint_fields'] = []
    else:
        prod_dict['gamepoint_fields'] = []
    saldo = get_saldo(session['user_id'])
    return render_template('producto.html', producto=prod_dict, saldo=saldo)


# ===== COMPRAR =====
@app.route('/comprar', methods=['POST'])
@login_required
def comprar():
    producto_id = int(request.form.get('producto_id', 0))
    cantidad = int(request.form.get('cantidad', 1))
    id_juego = request.form.get('id_juego', '').strip()

    db = get_db()
    prod = db.execute("SELECT p.*, c.tipo as categoria_tipo FROM productos p JOIN categorias c ON p.categoria_id = c.id WHERE p.id = ? AND p.activo = 1", (producto_id,)).fetchone()
    if not prod:
        flash('Producto no encontrado', 'error')
        db.close()
        return redirect(url_for('catalogo'))

    total = prod['precio'] * cantidad
    user_id = session['user_id']

    resultado = descontar_saldo(user_id, total, f"Compra: {prod['nombre']} x{cantidad}")
    if resultado is None:
        saldo = get_saldo(user_id)
        flash(f'Saldo insuficiente. Tu saldo es ${saldo:.4f} y el total es ${total:.4f}', 'error')
        db.close()
        return redirect(url_for('producto', id=producto_id))

    db.execute("INSERT INTO pedidos (usuario_id, producto_id, cantidad, total, id_juego, estado) VALUES (?,?,?,?,?,?)",
               (user_id, producto_id, cantidad, total, id_juego, 'procesando'))
    pedido_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Actualizar transacción con pedido_id
    db.execute("UPDATE transacciones SET pedido_id = ? WHERE id = (SELECT id FROM transacciones WHERE usuario_id = ? AND pedido_id IS NULL ORDER BY id DESC LIMIT 1)",
               (pedido_id, user_id))
    db.commit()

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
                merchant_code=merchant_code
            )
            # Reabrir DB para guardar resultado
            db2 = get_db()
            if resultado_api.get('ok'):
                nombre_jugador = resultado_api.get('ingamename', '')
                ref = resultado_api.get('referenceno', '')
                codigo = resultado_api.get('item', '')
                estado_final = 'procesando' if es_manual else 'completado'
                db2.execute("UPDATE pedidos SET estado = ?, nombre_jugador = ?, codigo_entregado = ?, referencia_externa = ? WHERE id = ?", (estado_final, nombre_jugador or ref, codigo, ref, pedido_id))
                db2.commit()
                db2.close()
                if es_manual:
                    flash(f'Pedido #{pedido_id} enviado al proveedor (Ref: {ref}). Se confirmará automáticamente cuando el proveedor lo procese.', 'success')
                elif codigo:
                    flash(f'Pedido #{pedido_id} completado. Código: {codigo}', 'success')
                else:
                    flash(f'Pedido #{pedido_id} completado. Recarga aplicada a {nombre_jugador or id_juego} (Ref: {ref}).', 'success')
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
            db2.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
            db2.commit()
            db2.close()
            recargar_saldo(user_id, total, f"Reembolso: Excepción GamePoint pedido #{pedido_id}")
            flash(f'Error inesperado en la recarga. Se reembolsó ${total:.4f} a tu cartera.', 'error')
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
            pin_codes.append(pr['pin'])
            db.execute("UPDATE pines SET estado = 'usado', usado_por = ?, pedido_id = ?, fecha_usado = datetime('now','localtime') WHERE id = ?",
                       (user_id, pedido_id, pr['id']))
        db.commit()
        db.close()

        # Ejecutar canjes secuencialmente
        canjes_ok = 0
        nombre_jugador = ''
        error_msg = ''
        for i, pin_code in enumerate(pin_codes):
            try:
                resultado_api = canjear_pin_completo(pin_code, id_juego, monto_api)
                if resultado_api.get('ok'):
                    canjes_ok += 1
                    nombre_jugador = resultado_api.get('username', '') or nombre_jugador
                else:
                    paso_error = resultado_api.get('paso', 0)
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
                db4.execute("UPDATE pines SET estado = 'disponible', usado_por = NULL, pedido_id = NULL, fecha_usado = NULL WHERE id = ?", (pin_ids[j],))
            db4.commit()
            db4.close()
            recargar_saldo(user_id, monto_parcial, f"Reembolso parcial: {canjes_ok}/{num_canjes} canjes OK pedido #{pedido_id}")
            flash(f'Pedido #{pedido_id}: {canjes_ok}/{num_canjes} recargas completadas. Se reembolsó ${monto_parcial:.4f} por las fallidas.', 'warning')
            return redirect(url_for('pedido_detalle', id=pedido_id))
        else:
            db3.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
            db3.commit()
            db3.close()
            # Devolver todos los pines
            db4 = get_db()
            for pid in pin_ids:
                db4.execute("UPDATE pines SET estado = 'disponible', usado_por = NULL, pedido_id = NULL, fecha_usado = NULL WHERE id = ?", (pid,))
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
                codigos.append(pin_row['pin'])
            todos_codigos = '\n'.join(codigos)
            db.execute("UPDATE pedidos SET estado = 'completado', codigo_entregado = ? WHERE id = ?", (todos_codigos, pedido_id))
            db.commit()
            db.close()
            flash(f'Pedido #{pedido_id} completado. {len(codigos)} código(s) entregado(s).', 'success')
            return redirect(url_for('pedido_detalle', id=pedido_id))
        else:
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
    db.close()
    if not pedido:
        flash('Pedido no encontrado', 'error')
        return redirect(url_for('mis_pedidos'))
    return render_template('pedido.html', pedido=pedido)


@app.route('/mis-pines')
@login_required
def mis_pines():
    db = get_db()
    pines = db.execute(
        "SELECT p.id as pedido_id, p.codigo_entregado, p.cantidad, p.total, p.estado, p.fecha_pedido, pr.nombre as producto_nombre "
        "FROM pedidos p JOIN productos pr ON p.producto_id = pr.id "
        "JOIN categorias c ON pr.categoria_id = c.id "
        "WHERE p.usuario_id = ? AND p.codigo_entregado IS NOT NULL AND p.codigo_entregado != '' "
        "AND c.tipo = 'giftcards' "
        "ORDER BY p.fecha_pedido DESC", (session['user_id'],)
    ).fetchall()
    db.close()
    return render_template('mis_pines.html', pines=pines)


@app.route('/mis-pedidos')
@login_required
def mis_pedidos():
    db = get_db()
    pedidos = db.execute(
        "SELECT p.*, pr.nombre as producto_nombre FROM pedidos p "
        "JOIN productos pr ON p.producto_id = pr.id "
        "JOIN categorias c ON pr.categoria_id = c.id "
        "WHERE p.usuario_id = ? AND c.tipo != 'giftcards' "
        "ORDER BY p.fecha_pedido DESC", (session['user_id'],)
    ).fetchall()
    db.close()
    return render_template('mis_pedidos.html', pedidos=pedidos)


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
        db.close()
        return redirect(url_for('perfil'))
    saldo = get_saldo(session['user_id'])
    db.close()
    return render_template('perfil.html', user=user, saldo=saldo)


# ===== CARTERA =====
@app.route('/cartera')
@login_required
def cartera():
    db = get_db()
    saldo = get_saldo(session['user_id'])
    transacciones = db.execute("SELECT t.*, u.nombre as admin_nombre FROM transacciones t LEFT JOIN usuarios u ON t.admin_id = u.id WHERE t.usuario_id = ? ORDER BY t.fecha DESC LIMIT 50", (session['user_id'],)).fetchall()
    db.close()
    return render_template('cartera.html', saldo=saldo, transacciones=transacciones)


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
        if monto < 0.50:
            flash('El monto mínimo es $0.50', 'error')
            return redirect(url_for('solicitar_recarga'))
        if not metodo_pago:
            flash('Selecciona un método de pago', 'error')
            return redirect(url_for('solicitar_recarga'))
        db = get_db()
        # Verificar que no tenga otra solicitud pendiente
        pendiente = db.execute("SELECT id FROM solicitudes_recarga WHERE usuario_id = ? AND estado = 'pendiente'", (session['user_id'],)).fetchone()
        if pendiente:
            db.close()
            flash('Ya tienes una solicitud de recarga pendiente. Espera a que sea procesada.', 'error')
            return redirect(url_for('solicitar_recarga'))
        db.execute("INSERT INTO solicitudes_recarga (usuario_id, monto, metodo_pago, referencia) VALUES (?,?,?,?)",
                   (session['user_id'], monto, metodo_pago, referencia))
        db.commit()
        db.close()
        flash(f'Solicitud de recarga por ${monto:.2f} enviada. El admin la revisará pronto.', 'success')
        return redirect(url_for('solicitar_recarga'))
    db = get_db()
    solicitudes = db.execute("SELECT * FROM solicitudes_recarga WHERE usuario_id = ? ORDER BY fecha_solicitud DESC LIMIT 20", (session['user_id'],)).fetchall()
    saldo = get_saldo(session['user_id'])
    # Cargar config de métodos de pago
    config_rows = db.execute("SELECT clave, valor FROM configuracion WHERE clave LIKE 'metodo_%'").fetchall()
    config = {r['clave']: r['valor'] for r in config_rows}
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
    return render_template('solicitar_recarga.html', solicitudes=solicitudes, saldo=saldo, metodos=metodos)


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
    db.close()
    return render_template('admin/panel.html', total_users=total_users, total_pedidos=total_pedidos, total_ventas=total_ventas, total_pendientes=total_pendientes, ultimos_pedidos=ultimos_pedidos, solicitudes_pendientes=solicitudes_pendientes)


@app.route('/admin/solicitudes')
@admin_required
def admin_solicitudes():
    db = get_db()
    solicitudes = db.execute(
        "SELECT s.*, u.nombre as usuario_nombre, u.email as usuario_email "
        "FROM solicitudes_recarga s JOIN usuarios u ON s.usuario_id = u.id "
        "ORDER BY CASE s.estado WHEN 'pendiente' THEN 0 ELSE 1 END, s.fecha_solicitud DESC"
    ).fetchall()
    db.close()
    return render_template('admin/solicitudes.html', solicitudes=solicitudes)


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
    # Aplicar la recarga al saldo del usuario
    recargar_saldo(sol['usuario_id'], sol['monto'],
                   f"Recarga aprobada (solicitud #{id}) - {sol['metodo_pago']}",
                   admin_id=session['user_id'])
    db.execute("UPDATE solicitudes_recarga SET estado = 'aprobada', admin_id = ?, nota_admin = ?, fecha_respuesta = datetime('now','localtime') WHERE id = ?",
               (session['user_id'], nota, id))
    db.commit()
    db.close()
    flash(f'Solicitud #{id} aprobada. Se recargaron ${sol["monto"]:.4f} al usuario.', 'success')
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
        db.commit()
        db.close()
        flash('Métodos de pago actualizados correctamente', 'success')
        return redirect(url_for('admin_metodos_pago'))
    config_rows = db.execute("SELECT clave, valor FROM configuracion WHERE clave LIKE 'metodo_%'").fetchall()
    config = {r['clave']: r['valor'] for r in config_rows}
    db.close()
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
    return render_template('admin/metodos_pago.html', metodos=metodos)


@app.route('/admin/usuarios')
@admin_required
def admin_usuarios():
    db = get_db()
    usuarios = db.execute("SELECT u.*, COALESCE(c.saldo, 0) as saldo FROM usuarios u LEFT JOIN carteras c ON u.id = c.usuario_id ORDER BY u.fecha_registro DESC").fetchall()
    db.close()
    return render_template('admin/usuarios.html', usuarios=usuarios)


@app.route('/admin/usuario/<int:id>/toggle', methods=['POST'])
@admin_required
def admin_toggle_usuario(id):
    db = get_db()
    user = db.execute("SELECT id, activo, rol FROM usuarios WHERE id = ?", (id,)).fetchone()
    if not user or user['rol'] == 'admin':
        db.close()
        flash('No se puede modificar este usuario.', 'error')
        return redirect(url_for('admin_usuarios'))
    nuevo_estado = 0 if user['activo'] else 1
    db.execute("UPDATE usuarios SET activo = ? WHERE id = ?", (nuevo_estado, id))
    db.commit()
    db.close()
    if nuevo_estado:
        flash('Usuario aprobado y activado.', 'success')
    else:
        flash('Usuario desactivado.', 'success')
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
        accion = request.form.get('accion')
        if accion == 'crear':
            nombre = request.form.get('nombre', '').strip()
            descripcion = request.form.get('descripcion', '').strip()
            precio = float(request.form.get('precio', 0))
            categoria_id = int(request.form.get('categoria_id', 0))
            icono = request.form.get('icono', 'fa-gem').strip()
            usa_api = 1 if request.form.get('usa_api') else 0
            monto_api = int(request.form.get('monto_api', 0))
            gamepoint_product_id = int(request.form.get('gamepoint_product_id', 0))
            gamepoint_package_id = int(request.form.get('gamepoint_package_id', 0))
            gamepoint_fields = request.form.get('gamepoint_fields', '').strip()
            recarga_manual = 1 if request.form.get('recarga_manual') else 0
            orden = int(request.form.get('orden', 0))
            pin_origen_producto_id = int(request.form.get('pin_origen_producto_id', 0))
            stock_minimo = int(request.form.get('stock_minimo', 0))
            stock_objetivo = int(request.form.get('stock_objetivo', 0))
            canjes_por_compra = int(request.form.get('canjes_por_compra', 1)) or 1
            if nombre and precio > 0 and categoria_id > 0:
                db.execute("INSERT INTO productos (nombre, descripcion, precio, categoria_id, icono, usa_api, monto_api, gamepoint_product_id, gamepoint_package_id, gamepoint_fields, recarga_manual, orden, pin_origen_producto_id, stock_minimo, stock_objetivo, canjes_por_compra) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (nombre, descripcion, precio, categoria_id, icono, usa_api, monto_api, gamepoint_product_id, gamepoint_package_id, gamepoint_fields, recarga_manual, orden, pin_origen_producto_id, stock_minimo, stock_objetivo, canjes_por_compra))
                db.commit()
                flash(f'Producto "{nombre}" creado', 'success')
        elif accion == 'editar':
            prod_id = int(request.form.get('producto_id', 0))
            nombre = request.form.get('nombre', '').strip()
            descripcion = request.form.get('descripcion', '').strip()
            precio = float(request.form.get('precio', 0))
            categoria_id = int(request.form.get('categoria_id', 0))
            activo = 1 if request.form.get('activo') else 0
            usa_api = 1 if request.form.get('usa_api') else 0
            monto_api = int(request.form.get('monto_api', 0))
            gamepoint_product_id = int(request.form.get('gamepoint_product_id', 0))
            gamepoint_package_id = int(request.form.get('gamepoint_package_id', 0))
            gamepoint_fields = request.form.get('gamepoint_fields', '').strip()
            recarga_manual = 1 if request.form.get('recarga_manual') else 0
            orden = int(request.form.get('orden', 0))
            pin_origen_producto_id = int(request.form.get('pin_origen_producto_id', 0))
            stock_minimo = int(request.form.get('stock_minimo', 0))
            stock_objetivo = int(request.form.get('stock_objetivo', 0))
            canjes_por_compra = int(request.form.get('canjes_por_compra', 1)) or 1
            if prod_id > 0 and nombre and precio > 0:
                db.execute("UPDATE productos SET nombre=?, descripcion=?, precio=?, categoria_id=?, activo=?, usa_api=?, monto_api=?, gamepoint_product_id=?, gamepoint_package_id=?, gamepoint_fields=?, recarga_manual=?, orden=?, pin_origen_producto_id=?, stock_minimo=?, stock_objetivo=?, canjes_por_compra=? WHERE id=?",
                           (nombre, descripcion, precio, categoria_id, activo, usa_api, monto_api, gamepoint_product_id, gamepoint_package_id, gamepoint_fields, recarga_manual, orden, pin_origen_producto_id, stock_minimo, stock_objetivo, canjes_por_compra, prod_id))
                db.commit()
                flash(f'Producto actualizado', 'success')
        elif accion == 'eliminar':
            prod_id = int(request.form.get('producto_id', 0))
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

    productos = db.execute("SELECT p.*, c.nombre as categoria_nombre FROM productos p LEFT JOIN categorias c ON p.categoria_id = c.id ORDER BY c.orden, p.orden, p.nombre").fetchall()
    categorias = db.execute("SELECT * FROM categorias ORDER BY orden").fetchall()
    # Productos giftcard para selector de restock
    productos_giftcard_raw = db.execute(
        "SELECT p.id, p.nombre FROM productos p JOIN categorias c ON p.categoria_id = c.id WHERE c.tipo = 'giftcards' AND p.activo = 1 ORDER BY p.nombre"
    ).fetchall()
    productos_giftcard = []
    for gc in productos_giftcard_raw:
        stock = db.execute("SELECT COUNT(*) as c FROM pines WHERE producto_id = ? AND estado = 'disponible'", (gc['id'],)).fetchone()['c']
        productos_giftcard.append({'id': gc['id'], 'nombre': gc['nombre'], 'stock': stock})
    db.close()
    return render_template('admin/productos.html', productos=productos, categorias=categorias, productos_giftcard=productos_giftcard)


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
                "UPDATE productos SET nombre=?, precio=?, activo=?, recarga_manual=?, gamepoint_product_id=?, gamepoint_package_id=? WHERE id=?",
                (p['nombre'], float(p['precio']), int(p['activo']), int(p.get('recarga_manual', 0)),
                 int(p.get('gamepoint_product_id', 0)), int(p.get('gamepoint_package_id', 0)), int(p['id']))
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


@app.route('/admin/gamepoint')
@admin_required
def admin_gamepoint_catalogo():
    return render_template('admin/gamepoint.html')


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
    """Verificar pedidos GamePoint: procesando->completado/cancelado, completado->cancelado si FAIL"""
    from gamepoint_api import consultar_orden
    db = get_db()
    pedidos = db.execute(
        "SELECT p.id, p.usuario_id, p.total, p.estado, p.referencia_externa, p.nombre_jugador "
        "FROM pedidos p JOIN productos pr ON p.producto_id = pr.id "
        "WHERE p.estado IN ('completado', 'procesando') AND p.referencia_externa != '' AND p.referencia_externa IS NOT NULL "
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
                    'razon': inquiry.get('reason', 'Proveedor rechazó la recarga'),
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
        "WHERE p.estado IN ('completado', 'procesando') AND p.referencia_externa != '' AND p.referencia_externa IS NOT NULL "
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
                    'razon': inquiry.get('reason', 'Proveedor rechazó la recarga'),
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
            orden = int(request.form.get('orden', 0))
            verificar_nombre = 1 if request.form.get('verificar_nombre') else 0
            verificar_nombre_tipo = request.form.get('verificar_nombre_tipo', '').strip()
            if nombre and slug:
                try:
                    db.execute("INSERT INTO categorias (nombre, slug, icono, imagen, tipo, descripcion, orden, verificar_nombre, verificar_nombre_tipo) VALUES (?,?,?,?,?,?,?,?,?)",
                               (nombre, slug, icono, imagen, tipo, descripcion, orden, verificar_nombre, verificar_nombre_tipo))
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
            orden = int(request.form.get('orden', 0))
            activo = 1 if request.form.get('activo') else 0
            verificar_nombre = 1 if request.form.get('verificar_nombre') else 0
            verificar_nombre_tipo = request.form.get('verificar_nombre_tipo', '').strip()
            if cat_id > 0 and nombre and slug:
                db.execute("UPDATE categorias SET nombre=?, slug=?, icono=?, imagen=?, tipo=?, descripcion=?, orden=?, activo=?, verificar_nombre=?, verificar_nombre_tipo=? WHERE id=?",
                           (nombre, slug, icono, imagen, tipo, descripcion, orden, activo, verificar_nombre, verificar_nombre_tipo, cat_id))
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
                count = 0
                for pin in pines_list:
                    db.execute("INSERT INTO pines (producto_id, pin) VALUES (?, ?)", (producto_id, pin))
                    count += 1
                db.commit()
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
        return redirect(url_for('admin_almacen'))

    # Productos de categoría Gift Card + productos usa_api (Free Fire)
    productos_api = db.execute("SELECT p.* FROM productos p JOIN categorias c ON p.categoria_id = c.id WHERE p.activo = 1 AND (c.tipo = 'giftcards' OR p.usa_api = 1) ORDER BY p.nombre").fetchall()
    # Stock por producto
    stock = {}
    for p in productos_api:
        count = db.execute("SELECT COUNT(*) as c FROM pines WHERE producto_id = ? AND estado = 'disponible'", (p['id'],)).fetchone()
        stock[p['id']] = count['c']
    # Todos los pines agrupados
    pines = db.execute("SELECT pi.*, pr.nombre as producto_nombre FROM pines pi JOIN productos pr ON pi.producto_id = pr.id ORDER BY pi.estado ASC, pi.fecha_agregado DESC").fetchall()
    # Filtro por producto
    filtro = request.args.get('producto_id', '')
    if filtro:
        pines = db.execute("SELECT pi.*, pr.nombre as producto_nombre FROM pines pi JOIN productos pr ON pi.producto_id = pr.id WHERE pi.producto_id = ? ORDER BY pi.estado ASC, pi.fecha_agregado DESC", (int(filtro),)).fetchall()
    db.close()
    return render_template('admin/almacen.html', productos_api=productos_api, stock=stock, pines=pines, filtro=filtro)


@app.route('/admin/pedidos')
@admin_required
def admin_pedidos():
    db = get_db()
    pedidos = db.execute("SELECT p.*, u.nombre as usuario_nombre, u.email as usuario_email, pr.nombre as producto_nombre FROM pedidos p JOIN usuarios u ON p.usuario_id = u.id JOIN productos pr ON p.producto_id = pr.id ORDER BY p.fecha_pedido DESC").fetchall()
    db.close()
    return render_template('admin/pedidos.html', pedidos=pedidos)


@app.route('/admin/pedido/<int:id>/estado', methods=['POST'])
@admin_required
def admin_cambiar_estado(id):
    estado = request.form.get('estado', 'pendiente')
    db = get_db()
    db.execute("UPDATE pedidos SET estado = ? WHERE id = ?", (estado, id))
    db.commit()
    db.close()
    flash(f'Pedido #{id} actualizado a {estado}', 'success')
    return redirect(url_for('admin_pedidos'))


# ===== API KEY MANAGEMENT =====
@app.route('/mi-api', methods=['GET', 'POST'])
@login_required
def mi_api():
    db = get_db()
    user = db.execute("SELECT * FROM usuarios WHERE id = ?", (session['user_id'],)).fetchone()
    if request.method == 'POST':
        nueva_key = secrets.token_hex(32)
        db.execute("UPDATE usuarios SET api_key = ? WHERE id = ?", (nueva_key, session['user_id']))
        db.commit()
        user = db.execute("SELECT * FROM usuarios WHERE id = ?", (session['user_id'],)).fetchone()
        flash('API Key generada exitosamente', 'success')
    db.close()
    return render_template('mi_api.html', user=user)


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
    db = get_db()
    productos = db.execute("SELECT p.id, p.nombre, p.descripcion, p.precio, p.usa_api, p.gamepoint_product_id, p.gamepoint_fields, p.recarga_manual, c.nombre as categoria FROM productos p JOIN categorias c ON p.categoria_id = c.id WHERE p.activo = 1 ORDER BY c.orden, p.nombre").fetchall()
    db.close()
    result = []
    for p in productos:
        d = dict(p)
        usa_api_hype = d.pop('usa_api', 0)
        # Parsear campos requeridos para que el revendedor sepa qué enviar
        fields_raw = d.pop('gamepoint_fields', '') or ''
        campos = []
        if fields_raw:
            try:
                campos = _json.loads(fields_raw)
            except Exception:
                campos = []
        if campos:
            d['campos_requeridos'] = [{'nombre': f['name'], 'descripcion': f['desc'], 'tipo': f['type'], 'opciones': f.get('options', [])} for f in campos]
        elif usa_api_hype:
            d['campos_requeridos'] = [{'nombre': 'id_juego', 'descripcion': 'ID del jugador en Free Fire', 'tipo': 'string', 'opciones': []}]
        else:
            d['campos_requeridos'] = []
        d['usa_gamepoint'] = bool(d.pop('gamepoint_product_id', 0))
        d['procesamiento_manual'] = bool(d.pop('recarga_manual', 0))
        result.append(d)
    return jsonify({'ok': True, 'productos': result})


@app.route('/api/v1/comprar', methods=['POST'])
@api_key_required
def api_comprar():
    user = request.api_user
    data = request.get_json() or {}
    producto_id = data.get('producto_id', 0)
    cantidad = data.get('cantidad', 1)
    id_juego = data.get('id_juego', '')
    input2 = data.get('input2', '')

    db = get_db()
    prod = db.execute("SELECT p.*, c.tipo as categoria_tipo FROM productos p JOIN categorias c ON p.categoria_id = c.id WHERE p.id = ? AND p.activo = 1", (producto_id,)).fetchone()
    if not prod:
        db.close()
        return jsonify({'ok': False, 'error': 'Producto no encontrado'}), 404

    # Validar que se envíe id_juego si el producto lo requiere (no aplica a gift cards sin campos)
    gp_fields_raw = ''
    try:
        gp_fields_raw = prod['gamepoint_fields'] or ''
    except Exception:
        pass
    requiere_id = prod['usa_api'] or (prod['gamepoint_product_id'] and gp_fields_raw)
    if requiere_id and not id_juego:
        db.close()
        return jsonify({'ok': False, 'error': 'Se requiere id_juego (Player ID)'}), 400

    total = prod['precio'] * cantidad
    resultado = descontar_saldo(user['id'], total, f"API: {prod['nombre']} x{cantidad}")
    if resultado is None:
        saldo = get_saldo(user['id'])
        db.close()
        return jsonify({'ok': False, 'error': 'Saldo insuficiente', 'saldo': saldo, 'total': total}), 400

    db.execute("INSERT INTO pedidos (usuario_id, producto_id, cantidad, total, id_juego, estado) VALUES (?,?,?,?,?,?)",
               (user['id'], producto_id, cantidad, total, id_juego, 'procesando'))
    pedido_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    db.execute("UPDATE transacciones SET pedido_id = ? WHERE id = (SELECT id FROM transacciones WHERE usuario_id = ? AND pedido_id IS NULL ORDER BY id DESC LIMIT 1)",
               (pedido_id, user['id']))
    db.commit()

    nombre_jugador = ''
    user_id_api = user['id']

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
                merchant_code=merchant_code
            )
            db2 = get_db()
            if resultado_api.get('ok'):
                nombre_jugador = resultado_api.get('ingamename', '')
                ref = resultado_api.get('referenceno', '')
                codigo = resultado_api.get('item', '')
                estado_final = 'procesando' if es_manual else 'completado'
                db2.execute("UPDATE pedidos SET estado = ?, nombre_jugador = ?, codigo_entregado = ?, referencia_externa = ? WHERE id = ?", (estado_final, nombre_jugador or ref, codigo, ref, pedido_id))
                db2.commit()
                db2.close()
                resp = {
                    'ok': True, 'pedido_id': pedido_id, 'estado': estado_final,
                    'total': total, 'saldo_restante': get_saldo(user_id_api),
                    'referencia': ref,
                }
                if es_manual:
                    resp['mensaje'] = f'Pedido enviado al proveedor (Ref: {ref}). Se confirmará automáticamente.'
                elif codigo:
                    resp['codigo'] = codigo
                    resp['mensaje'] = f'Código entregado: {codigo}'
                else:
                    resp['nombre_jugador'] = nombre_jugador
                    resp['mensaje'] = f'Recarga completada para {nombre_jugador or id_juego} (Ref: {ref})'
                return jsonify(resp)
            else:
                db2.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
                db2.commit()
                db2.close()
                recargar_saldo(user_id_api, total, f"Reembolso API: Error GamePoint pedido #{pedido_id}")
                return jsonify({
                    'ok': False, 'error': resultado_api.get('error', resultado_api.get('message', 'Error desconocido')),
                    'pedido_id': pedido_id, 'reembolsado': True, 'saldo_restante': get_saldo(user_id_api)
                }), 400
        except Exception as e:
            db2 = get_db()
            db2.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
            db2.commit()
            db2.close()
            recargar_saldo(user_id_api, total, f"Reembolso API: Excepción GamePoint pedido #{pedido_id}")
            return jsonify({
                'ok': False, 'error': str(e), 'pedido_id': pedido_id,
                'reembolsado': True, 'saldo_restante': get_saldo(user_id_api)
            }), 500

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
            pin_codes.append(pr['pin'])
            db.execute("UPDATE pines SET estado = 'usado', usado_por = ?, pedido_id = ?, fecha_usado = datetime('now','localtime') WHERE id = ?",
                       (user_id_api, pedido_id, pr['id']))
        db.commit()
        db.close()

        # Ejecutar canjes secuencialmente
        canjes_ok = 0
        nombre_jugador = ''
        error_msg = ''
        for i, pin_code in enumerate(pin_codes):
            try:
                resultado_api = canjear_pin_completo(pin_code, id_juego, monto_api)
                if resultado_api.get('ok'):
                    canjes_ok += 1
                    nombre_jugador = resultado_api.get('username', '') or nombre_jugador
                else:
                    paso_error = resultado_api.get('paso', 0)
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
                db4.execute("UPDATE pines SET estado = 'disponible', usado_por = NULL, pedido_id = NULL, fecha_usado = NULL WHERE id = ?", (pin_ids[j],))
            db4.commit()
            db4.close()
            recargar_saldo(user_id_api, monto_parcial, f"Reembolso parcial API: {canjes_ok}/{num_canjes} canjes OK pedido #{pedido_id}")
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
                db4.execute("UPDATE pines SET estado = 'disponible', usado_por = NULL, pedido_id = NULL, fecha_usado = NULL WHERE id = ?", (pid,))
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
                codigos.append(pin_row['pin'])
            todos_codigos = '\n'.join(codigos)
            db.execute("UPDATE pedidos SET estado = 'completado', codigo_entregado = ? WHERE id = ?", (todos_codigos, pedido_id))
            db.commit()
            db.close()
            return jsonify({
                'ok': True, 'pedido_id': pedido_id, 'estado': 'completado',
                'total': total, 'saldo_restante': get_saldo(user_id_api),
                'codigos': codigos,
                'cantidad_entregada': len(codigos),
                'mensaje': f'{len(codigos)} código(s) entregado(s)'
            })
        else:
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
        'nombre_jugador': nombre_jugador,
        'mensaje': f'Recarga completada para {nombre_jugador} (ID: {id_juego})' if nombre_jugador else f'Pedido #{pedido_id} creado'
    })


@app.route('/api/v1/pedidos', methods=['GET'])
@api_key_required
def api_pedidos():
    user = request.api_user
    db = get_db()
    pedidos = db.execute("SELECT p.id, p.cantidad, p.total, p.id_juego, p.nombre_jugador, p.codigo_entregado, p.estado, p.fecha_pedido, pr.nombre as producto FROM pedidos p JOIN productos pr ON p.producto_id = pr.id WHERE p.usuario_id = ? ORDER BY p.fecha_pedido DESC LIMIT 50", (user['id'],)).fetchall()
    db.close()
    return jsonify({'ok': True, 'pedidos': [dict(p) for p in pedidos]})


@app.route('/api/v1/pedido/<int:pedido_id>', methods=['GET'])
@api_key_required
def api_pedido_detalle(pedido_id):
    user = request.api_user
    db = get_db()
    pedido = db.execute("SELECT p.id, p.cantidad, p.total, p.id_juego, p.nombre_jugador, p.codigo_entregado, p.estado, p.fecha_pedido, pr.nombre as producto FROM pedidos p JOIN productos pr ON p.producto_id = pr.id WHERE p.id = ? AND p.usuario_id = ?", (pedido_id, user['id'])).fetchone()
    db.close()
    if not pedido:
        return jsonify({'ok': False, 'error': 'Pedido no encontrado'}), 404
    return jsonify({'ok': True, 'pedido': dict(pedido)})


@app.route('/api/v1/transacciones', methods=['GET'])
@api_key_required
def api_transacciones():
    user = request.api_user
    db = get_db()
    trans = db.execute("SELECT id, tipo, monto, saldo_anterior, saldo_nuevo, descripcion, fecha FROM transacciones WHERE usuario_id = ? ORDER BY fecha DESC LIMIT 50", (user['id'],)).fetchall()
    db.close()
    return jsonify({'ok': True, 'transacciones': [dict(t) for t in trans]})


@app.route('/api/v1/webhook', methods=['GET', 'POST'])
@api_key_required
def api_webhook():
    user = request.api_user
    if request.method == 'GET':
        return jsonify({'ok': True, 'webhook_url': user.get('webhook_url', '') or ''})
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
    """Envía notificación webhook al revendedor si tiene URL configurada."""
    try:
        db = get_db()
        user = db.execute("SELECT webhook_url FROM usuarios WHERE id = ?", (usuario_id,)).fetchone()
        db.close()
        webhook_url = (user['webhook_url'] or '') if user else ''
        if not webhook_url:
            return
        import requests as req
        req.post(webhook_url, json=pedido_data, timeout=10)
    except Exception:
        pass


if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)
