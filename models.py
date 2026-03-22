import sqlite3
import os
import secrets
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

DB_PATH = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'instance', 'tienda.db')


def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            telefono TEXT DEFAULT '',
            rol TEXT DEFAULT 'revendedor' CHECK(rol IN ('admin', 'revendedor')),
            activo INTEGER DEFAULT 1,
            api_key TEXT UNIQUE,
            fecha_registro TEXT DEFAULT (datetime('now','localtime')),
            ultimo_login TEXT
        );

        CREATE TABLE IF NOT EXISTS categorias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            icono TEXT DEFAULT 'fa-folder',
            imagen TEXT DEFAULT '',
            tipo TEXT DEFAULT 'juegos',
            descripcion TEXT,
            activo INTEGER DEFAULT 1,
            orden INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            descripcion TEXT,
            precio REAL NOT NULL,
            categoria_id INTEGER,
            icono TEXT DEFAULT 'fa-gem',
            activo INTEGER DEFAULT 1,
            usa_api INTEGER DEFAULT 0,
            monto_api INTEGER DEFAULT 0,
            gamepoint_product_id INTEGER DEFAULT 0,
            gamepoint_package_id INTEGER DEFAULT 0,
            gamepoint_fields TEXT DEFAULT '',
            orden INTEGER DEFAULT 0,
            fecha_creacion TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (categoria_id) REFERENCES categorias(id)
        );

        CREATE TABLE IF NOT EXISTS carteras (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL UNIQUE,
            saldo REAL DEFAULT 0.0,
            fecha_creacion TEXT DEFAULT (datetime('now','localtime')),
            ultima_actualizacion TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
        );

        CREATE TABLE IF NOT EXISTS pedidos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL,
            producto_id INTEGER NOT NULL,
            cantidad INTEGER DEFAULT 1,
            total REAL NOT NULL,
            id_juego TEXT,
            nombre_jugador TEXT,
            codigo_entregado TEXT DEFAULT '',
            estado TEXT DEFAULT 'pendiente' CHECK(estado IN ('pendiente', 'procesando', 'completado', 'cancelado')),
            fecha_pedido TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id),
            FOREIGN KEY (producto_id) REFERENCES productos(id)
        );

        CREATE TABLE IF NOT EXISTS transacciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL,
            tipo TEXT NOT NULL CHECK(tipo IN ('recarga', 'compra', 'reembolso')),
            monto REAL NOT NULL,
            saldo_anterior REAL NOT NULL,
            saldo_nuevo REAL NOT NULL,
            descripcion TEXT,
            pedido_id INTEGER,
            admin_id INTEGER,
            fecha TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id),
            FOREIGN KEY (admin_id) REFERENCES usuarios(id)
        );

        CREATE TABLE IF NOT EXISTS pines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            producto_id INTEGER NOT NULL,
            pin TEXT NOT NULL,
            estado TEXT DEFAULT 'disponible' CHECK(estado IN ('disponible', 'usado', 'error')),
            usado_por INTEGER,
            pedido_id INTEGER,
            nombre_juego TEXT,
            fecha_agregado TEXT DEFAULT (datetime('now','localtime')),
            fecha_usado TEXT,
            FOREIGN KEY (producto_id) REFERENCES productos(id),
            FOREIGN KEY (usado_por) REFERENCES usuarios(id)
        );
    """)

    # Migración: agregar columnas GamePoint si no existen
    try:
        db.execute("SELECT gamepoint_product_id FROM productos LIMIT 1")
    except Exception:
        db.execute("ALTER TABLE productos ADD COLUMN gamepoint_product_id INTEGER DEFAULT 0")
        db.execute("ALTER TABLE productos ADD COLUMN gamepoint_package_id INTEGER DEFAULT 0")
    try:
        db.execute("SELECT gamepoint_fields FROM productos LIMIT 1")
    except Exception:
        db.execute("ALTER TABLE productos ADD COLUMN gamepoint_fields TEXT DEFAULT ''")
    try:
        db.execute("SELECT codigo_entregado FROM pedidos LIMIT 1")
    except Exception:
        db.execute("ALTER TABLE pedidos ADD COLUMN codigo_entregado TEXT DEFAULT ''")
    try:
        db.execute("SELECT referencia_externa FROM pedidos LIMIT 1")
    except Exception:
        db.execute("ALTER TABLE pedidos ADD COLUMN referencia_externa TEXT DEFAULT ''")
    try:
        db.execute("SELECT recarga_manual FROM productos LIMIT 1")
    except Exception:
        db.execute("ALTER TABLE productos ADD COLUMN recarga_manual INTEGER DEFAULT 0")
    try:
        db.execute("SELECT webhook_url FROM usuarios LIMIT 1")
    except Exception:
        db.execute("ALTER TABLE usuarios ADD COLUMN webhook_url TEXT DEFAULT ''")
    # Restock automático de pines: producto origen (Gift Card), stock mínimo y objetivo
    try:
        db.execute("SELECT pin_origen_producto_id FROM productos LIMIT 1")
    except Exception:
        db.execute("ALTER TABLE productos ADD COLUMN pin_origen_producto_id INTEGER DEFAULT 0")
    try:
        db.execute("SELECT stock_minimo FROM productos LIMIT 1")
    except Exception:
        db.execute("ALTER TABLE productos ADD COLUMN stock_minimo INTEGER DEFAULT 0")
    try:
        db.execute("SELECT stock_objetivo FROM productos LIMIT 1")
    except Exception:
        db.execute("ALTER TABLE productos ADD COLUMN stock_objetivo INTEGER DEFAULT 0")
    # Multi-canje: cuántos pines consumir por compra Hype (ej: 2 para 200 diamantes = 2x100)
    try:
        db.execute("SELECT canjes_por_compra FROM productos LIMIT 1")
    except Exception:
        db.execute("ALTER TABLE productos ADD COLUMN canjes_por_compra INTEGER DEFAULT 1")
    # Bonus por monto de recarga
    try:
        db.execute("SELECT id FROM bonus_recarga LIMIT 1")
    except Exception:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS bonus_recarga (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                monto_minimo REAL NOT NULL,
                porcentaje_bonus REAL NOT NULL,
                activo INTEGER DEFAULT 1
            );
        """)

    # Configuración general (métodos de pago, etc.)
    try:
        db.execute("SELECT id FROM configuracion LIMIT 1")
    except Exception:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS configuracion (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                clave TEXT NOT NULL UNIQUE,
                valor TEXT DEFAULT ''
            );
        """)
        # Insertar métodos de pago por defecto
        metodos_default = [
            ('metodo_pago_movil_activo', '1'),
            ('metodo_pago_movil_nombre', 'Pago Móvil'),
            ('metodo_pago_movil_datos', 'Banco: ---\nTeléfono: ---\nCédula: ---'),
            ('metodo_pago_movil_nota', 'Envía el monto exacto en Bs al cambio del día'),
            ('metodo_binance_activo', '1'),
            ('metodo_binance_nombre', 'Binance Pay'),
            ('metodo_binance_datos', 'Binance ID: ---\nMoneda: USDT'),
            ('metodo_binance_nota', 'Envía el monto exacto en USDT por Binance Pay'),
            ('metodo_zinli_activo', '1'),
            ('metodo_zinli_nombre', 'Zinli'),
            ('metodo_zinli_datos', 'Usuario Zinli: ---'),
            ('metodo_zinli_nota', 'Envía el monto exacto en USD por Zinli'),
            ('metodo_zelle_activo', '1'),
            ('metodo_zelle_nombre', 'Zelle'),
            ('metodo_zelle_datos', 'Email Zelle: ---'),
            ('metodo_zelle_nota', 'Envía el monto exacto en USD por Zelle'),
            ('recarga_minima', '0.50'),
        ]
        for clave, valor in metodos_default:
            db.execute("INSERT OR IGNORE INTO configuracion (clave, valor) VALUES (?,?)", (clave, valor))
    # Asegurar que recarga_minima exista en DBs existentes
    db.execute("INSERT OR IGNORE INTO configuracion (clave, valor) VALUES ('recarga_minima', '0.50')")

    # Solicitudes de recarga de saldo
    try:
        db.execute("SELECT id FROM solicitudes_recarga LIMIT 1")
    except Exception:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS solicitudes_recarga (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id INTEGER NOT NULL,
                monto REAL NOT NULL,
                metodo_pago TEXT NOT NULL,
                referencia TEXT DEFAULT '',
                comprobante TEXT DEFAULT '',
                estado TEXT DEFAULT 'pendiente' CHECK(estado IN ('pendiente', 'aprobada', 'rechazada')),
                nota_admin TEXT DEFAULT '',
                admin_id INTEGER,
                fecha_solicitud TEXT DEFAULT (datetime('now','localtime')),
                fecha_respuesta TEXT,
                FOREIGN KEY (usuario_id) REFERENCES usuarios(id),
                FOREIGN KEY (admin_id) REFERENCES usuarios(id)
            );
        """)

    # Verificación de nombre de jugador por categoría
    try:
        db.execute("SELECT verificar_nombre FROM categorias LIMIT 1")
    except Exception:
        db.execute("ALTER TABLE categorias ADD COLUMN verificar_nombre INTEGER DEFAULT 0")
    try:
        db.execute("SELECT verificar_nombre_tipo FROM categorias LIMIT 1")
    except Exception:
        db.execute("ALTER TABLE categorias ADD COLUMN verificar_nombre_tipo TEXT DEFAULT ''")


    # Crear admin si no existe
    admin = db.execute("SELECT id FROM usuarios WHERE email = ?", ('admin@gamersrev.com',)).fetchone()
    if not admin:
        api_key = secrets.token_hex(32)
        db.execute(
            "INSERT INTO usuarios (nombre, email, password, rol, api_key) VALUES (?, ?, ?, ?, ?)",
            ('Admin', 'admin@gamersrev.com', generate_password_hash('admin123'), 'admin', api_key)
        )

    # Categorias (cada juego/plataforma es una categoría)
    cat = db.execute("SELECT id FROM categorias LIMIT 1").fetchone()
    if not cat:
        categorias_data = [
            ('Free Fire', 'freefire', 'fa-fire', 'https://i.imgur.com/8QZqZ0m.jpg', 'juegos', 'Recargas de diamantes Free Fire', 1),
            ('Call of Duty Mobile', 'codmobile', 'fa-crosshairs', 'https://i.imgur.com/JYq3EPA.jpg', 'juegos', 'Recargas de CP para COD Mobile', 2),
            ('PUBG Mobile', 'pubg', 'fa-crosshairs', 'https://i.imgur.com/kX9z7wE.jpg', 'juegos', 'Recargas de UC para PUBG Mobile', 3),
            ('Fortnite', 'fortnite', 'fa-gamepad', 'https://i.imgur.com/6YzKEJq.jpg', 'juegos', 'Recargas de V-Bucks Fortnite', 4),
            ('Roblox', 'roblox', 'fa-cube', 'https://i.imgur.com/mKGmHf8.jpg', 'juegos', 'Recargas de Robux', 5),
            ('Mobile Legends', 'mobilelegends', 'fa-shield-halved', 'https://i.imgur.com/VwXj7Qi.jpg', 'juegos', 'Recargas de diamantes ML', 6),
            ('Genshin Impact', 'genshin', 'fa-star', 'https://i.imgur.com/5fGJVBP.jpg', 'juegos', 'Recargas de Genesis Crystals', 7),
            ('Steam', 'steam', 'fa-steam', 'https://i.imgur.com/YCQ8WkB.jpg', 'giftcards', 'Gift Cards Steam Wallet', 8),
            ('PlayStation', 'playstation', 'fa-playstation', 'https://i.imgur.com/7vO5qEn.jpg', 'giftcards', 'Gift Cards PlayStation Store', 9),
            ('Xbox', 'xbox', 'fa-xbox', 'https://i.imgur.com/pT4gMXI.jpg', 'giftcards', 'Gift Cards Xbox Store', 10),
        ]
        for nombre, slug, icono, imagen, tipo, desc, orden in categorias_data:
            db.execute("INSERT INTO categorias (nombre, slug, icono, imagen, tipo, descripcion, orden) VALUES (?,?,?,?,?,?,?)",
                       (nombre, slug, icono, imagen, tipo, desc, orden))

    # Productos
    prod = db.execute("SELECT id FROM productos LIMIT 1").fetchone()
    if not prod:
        def get_cat(slug):
            r = db.execute("SELECT id FROM categorias WHERE slug = ?", (slug,)).fetchone()
            return r['id'] if r else None

        # Free Fire
        cid = get_cat('freefire')
        if cid:
            for nombre, desc, precio, monto in [
                ('100+10 Diamantes', 'Recarga de 110 diamantes Free Fire', 1.50, 1),
                ('310+31 Diamantes', 'Recarga de 341 diamantes Free Fire', 3.00, 2),
                ('520+52 Diamantes', 'Recarga de 572 diamantes Free Fire', 5.50, 3),
                ('1060+106 Diamantes', 'Recarga de 1166 diamantes Free Fire', 12.00, 4),
                ('2180+196 Diamantes', 'Recarga de 2376 diamantes Free Fire', 23.00, 5),
                ('5600+538 Diamantes', 'Recarga de 6138 diamantes Free Fire', 55.00, 6),
            ]:
                db.execute("INSERT INTO productos (nombre, descripcion, precio, categoria_id, icono, usa_api, monto_api) VALUES (?,?,?,?,?,?,?)",
                           (nombre, desc, precio, cid, 'fa-gem', 1, monto))

        # Call of Duty Mobile
        cid = get_cat('codmobile')
        if cid:
            for nombre, desc, precio in [
                ('80 CP', 'Recarga de 80 CP COD Mobile', 1.50),
                ('160 CP', 'Recarga de 160 CP COD Mobile', 3.00),
                ('320 CP', 'Recarga de 320 CP COD Mobile', 5.50),
                ('800 CP', 'Recarga de 800 CP COD Mobile', 12.00),
                ('1600 CP', 'Recarga de 1600 CP COD Mobile', 23.00),
                ('5000 CP', 'Recarga de 5000 CP COD Mobile', 55.00),
            ]:
                db.execute("INSERT INTO productos (nombre, descripcion, precio, categoria_id, icono) VALUES (?,?,?,?,?)",
                           (nombre, desc, precio, cid, 'fa-coins'))

        # PUBG Mobile
        cid = get_cat('pubg')
        if cid:
            for nombre, desc, precio in [
                ('60 UC', 'Recarga de 60 UC PUBG Mobile', 1.50),
                ('325 UC', 'Recarga de 325 UC PUBG Mobile', 5.50),
                ('660 UC', 'Recarga de 660 UC PUBG Mobile', 10.00),
                ('1800 UC', 'Recarga de 1800 UC PUBG Mobile', 25.00),
                ('3850 UC', 'Recarga de 3850 UC PUBG Mobile', 50.00),
            ]:
                db.execute("INSERT INTO productos (nombre, descripcion, precio, categoria_id, icono) VALUES (?,?,?,?,?)",
                           (nombre, desc, precio, cid, 'fa-coins'))

        # Fortnite
        cid = get_cat('fortnite')
        if cid:
            for nombre, desc, precio in [
                ('1000 V-Bucks', 'Recarga de 1000 V-Bucks', 10.00),
                ('2800 V-Bucks', 'Recarga de 2800 V-Bucks', 25.00),
                ('5000 V-Bucks', 'Recarga de 5000 V-Bucks', 40.00),
                ('13500 V-Bucks', 'Recarga de 13500 V-Bucks', 90.00),
            ]:
                db.execute("INSERT INTO productos (nombre, descripcion, precio, categoria_id, icono) VALUES (?,?,?,?,?)",
                           (nombre, desc, precio, cid, 'fa-coins'))

        # Roblox
        cid = get_cat('roblox')
        if cid:
            for nombre, desc, precio in [
                ('400 Robux', 'Recarga de 400 Robux', 5.50),
                ('800 Robux', 'Recarga de 800 Robux', 10.00),
                ('1700 Robux', 'Recarga de 1700 Robux', 20.00),
                ('4500 Robux', 'Recarga de 4500 Robux', 50.00),
            ]:
                db.execute("INSERT INTO productos (nombre, descripcion, precio, categoria_id, icono) VALUES (?,?,?,?,?)",
                           (nombre, desc, precio, cid, 'fa-coins'))

        # Mobile Legends
        cid = get_cat('mobilelegends')
        if cid:
            for nombre, desc, precio in [
                ('86 Diamantes ML', 'Recarga de 86 diamantes Mobile Legends', 2.00),
                ('172 Diamantes ML', 'Recarga de 172 diamantes Mobile Legends', 4.00),
                ('344 Diamantes ML', 'Recarga de 344 diamantes Mobile Legends', 7.50),
                ('706 Diamantes ML', 'Recarga de 706 diamantes Mobile Legends', 15.00),
                ('2010 Diamantes ML', 'Recarga de 2010 diamantes Mobile Legends', 40.00),
            ]:
                db.execute("INSERT INTO productos (nombre, descripcion, precio, categoria_id, icono) VALUES (?,?,?,?,?)",
                           (nombre, desc, precio, cid, 'fa-gem'))

        # Genshin Impact
        cid = get_cat('genshin')
        if cid:
            for nombre, desc, precio in [
                ('60 Genesis Crystals', 'Recarga de 60 Genesis Crystals', 1.50),
                ('300 Genesis Crystals', 'Recarga de 300 Genesis Crystals', 5.50),
                ('980 Genesis Crystals', 'Recarga de 980 Genesis Crystals', 16.00),
                ('1980 Genesis Crystals', 'Recarga de 1980 Genesis Crystals', 30.00),
                ('3280 Genesis Crystals', 'Recarga de 3280 Genesis Crystals', 50.00),
            ]:
                db.execute("INSERT INTO productos (nombre, descripcion, precio, categoria_id, icono) VALUES (?,?,?,?,?)",
                           (nombre, desc, precio, cid, 'fa-gem'))

        # Steam
        cid = get_cat('steam')
        if cid:
            for nombre, desc, precio in [
                ('Steam $5', 'Gift Card Steam Wallet $5 USD', 6.00),
                ('Steam $10', 'Gift Card Steam Wallet $10 USD', 11.50),
                ('Steam $20', 'Gift Card Steam Wallet $20 USD', 22.00),
                ('Steam $50', 'Gift Card Steam Wallet $50 USD', 54.00),
            ]:
                db.execute("INSERT INTO productos (nombre, descripcion, precio, categoria_id, icono) VALUES (?,?,?,?,?)",
                           (nombre, desc, precio, cid, 'fa-gift'))

        # PlayStation
        cid = get_cat('playstation')
        if cid:
            for nombre, desc, precio in [
                ('PSN $10', 'Gift Card PlayStation Store $10 USD', 11.00),
                ('PSN $25', 'Gift Card PlayStation Store $25 USD', 27.00),
                ('PSN $50', 'Gift Card PlayStation Store $50 USD', 53.00),
                ('PSN $100', 'Gift Card PlayStation Store $100 USD', 105.00),
            ]:
                db.execute("INSERT INTO productos (nombre, descripcion, precio, categoria_id, icono) VALUES (?,?,?,?,?)",
                           (nombre, desc, precio, cid, 'fa-gift'))

        # Xbox
        cid = get_cat('xbox')
        if cid:
            for nombre, desc, precio in [
                ('Xbox $10', 'Gift Card Xbox Store $10 USD', 11.00),
                ('Xbox $25', 'Gift Card Xbox Store $25 USD', 27.00),
                ('Xbox $50', 'Gift Card Xbox Store $50 USD', 53.00),
                ('Xbox $100', 'Gift Card Xbox Store $100 USD', 105.00),
            ]:
                db.execute("INSERT INTO productos (nombre, descripcion, precio, categoria_id, icono) VALUES (?,?,?,?,?)",
                           (nombre, desc, precio, cid, 'fa-gift'))

    db.commit()
    db.close()


def get_user_by_id(user_id):
    db = get_db()
    user = db.execute("SELECT * FROM usuarios WHERE id = ?", (user_id,)).fetchone()
    db.close()
    return user


def get_user_by_email(email):
    db = get_db()
    user = db.execute("SELECT * FROM usuarios WHERE email = ?", (email,)).fetchone()
    db.close()
    return user


def get_user_by_api_key(api_key):
    db = get_db()
    user = db.execute("SELECT * FROM usuarios WHERE api_key = ? AND activo = 1", (api_key,)).fetchone()
    db.close()
    return user


def create_user(nombre, email, password, telefono=''):
    db = get_db()
    api_key = secrets.token_hex(32)
    try:
        db.execute(
            "INSERT INTO usuarios (nombre, email, password, telefono, api_key, activo) VALUES (?, ?, ?, ?, ?, 0)",
            (nombre, email, generate_password_hash(password), telefono, api_key)
        )
        db.commit()
        user = db.execute("SELECT * FROM usuarios WHERE email = ?", (email,)).fetchone()
        # Crear cartera
        db.execute("INSERT INTO carteras (usuario_id, saldo) VALUES (?, 0.0)", (user['id'],))
        db.commit()
        db.close()
        return user
    except sqlite3.IntegrityError:
        db.close()
        return None


def get_saldo(usuario_id):
    db = get_db()
    c = db.execute("SELECT saldo FROM carteras WHERE usuario_id = ?", (usuario_id,)).fetchone()
    db.close()
    if c:
        return c['saldo']
    # Crear cartera si no existe
    db = get_db()
    db.execute("INSERT OR IGNORE INTO carteras (usuario_id, saldo) VALUES (?, 0.0)", (usuario_id,))
    db.commit()
    db.close()
    return 0.0


def recargar_saldo(usuario_id, monto, descripcion='Recarga de saldo', admin_id=None):
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    try:
        c = db.execute("SELECT saldo FROM carteras WHERE usuario_id = ?", (usuario_id,)).fetchone()
        saldo_actual = c['saldo'] if c else 0.0
        saldo_nuevo = saldo_actual + monto
        db.execute("UPDATE carteras SET saldo = ?, ultima_actualizacion = datetime('now','localtime') WHERE usuario_id = ?",
                   (saldo_nuevo, usuario_id))
        db.execute("INSERT INTO transacciones (usuario_id, tipo, monto, saldo_anterior, saldo_nuevo, descripcion, admin_id) VALUES (?,?,?,?,?,?,?)",
                   (usuario_id, 'recarga', monto, saldo_actual, saldo_nuevo, descripcion, admin_id))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return saldo_nuevo


def descontar_saldo(usuario_id, monto, descripcion='Compra', pedido_id=None):
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    try:
        c = db.execute("SELECT saldo FROM carteras WHERE usuario_id = ?", (usuario_id,)).fetchone()
        saldo_actual = c['saldo'] if c else 0.0
        if saldo_actual < monto:
            db.rollback()
            db.close()
            return None
        saldo_nuevo = saldo_actual - monto
        db.execute("UPDATE carteras SET saldo = ?, ultima_actualizacion = datetime('now','localtime') WHERE usuario_id = ?",
                   (saldo_nuevo, usuario_id))
        db.execute("INSERT INTO transacciones (usuario_id, tipo, monto, saldo_anterior, saldo_nuevo, descripcion, pedido_id) VALUES (?,?,?,?,?,?,?)",
                   (usuario_id, 'compra', monto, saldo_actual, saldo_nuevo, descripcion, pedido_id))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return saldo_nuevo
