-- ===== BASE DE DATOS GAMERSREV =====
-- Ejecutar este script en phpMyAdmin de cPanel
-- Seleccionar la BD gamersre_tienda antes de ejecutar

-- Tabla de usuarios
CREATE TABLE IF NOT EXISTS usuarios (
    id INT AUTO_INCREMENT PRIMARY KEY,
    nombre VARCHAR(100) NOT NULL,
    email VARCHAR(150) NOT NULL UNIQUE,
    password VARCHAR(255) NOT NULL,
    telefono VARCHAR(20) DEFAULT NULL,
    rol ENUM('usuario', 'admin') DEFAULT 'usuario',
    activo TINYINT(1) DEFAULT 1,
    fecha_registro DATETIME DEFAULT CURRENT_TIMESTAMP,
    ultimo_login DATETIME DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Tabla de categorias
CREATE TABLE IF NOT EXISTS categorias (
    id INT AUTO_INCREMENT PRIMARY KEY,
    nombre VARCHAR(100) NOT NULL,
    slug VARCHAR(100) NOT NULL UNIQUE,
    icono VARCHAR(50) DEFAULT 'fa-folder',
    descripcion TEXT,
    activo TINYINT(1) DEFAULT 1,
    orden INT DEFAULT 0,
    fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Tabla de productos
CREATE TABLE IF NOT EXISTS productos (
    id INT AUTO_INCREMENT PRIMARY KEY,
    nombre VARCHAR(150) NOT NULL,
    descripcion TEXT,
    precio DECIMAL(10,2) NOT NULL,
    categoria ENUM('freefire', 'giftcard') NOT NULL,
    icono VARCHAR(50) DEFAULT 'fa-gem',
    activo TINYINT(1) DEFAULT 1,
    fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Tabla de servicios
CREATE TABLE IF NOT EXISTS servicios (
    id INT AUTO_INCREMENT PRIMARY KEY,
    producto_id INT NOT NULL,
    nombre VARCHAR(200) NOT NULL,
    descripcion TEXT,
    precio DECIMAL(10,2) NOT NULL,
    activo TINYINT(1) DEFAULT 1,
    orden INT DEFAULT 0,
    fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (producto_id) REFERENCES productos(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Tabla de pedidos
CREATE TABLE IF NOT EXISTS pedidos (
    id INT AUTO_INCREMENT PRIMARY KEY,
    usuario_id INT NOT NULL,
    producto_id INT NOT NULL,
    cantidad INT DEFAULT 1,
    total DECIMAL(10,2) NOT NULL,
    id_juego VARCHAR(50) DEFAULT NULL,
    metodo_pago VARCHAR(50) DEFAULT NULL,
    estado ENUM('pendiente', 'procesando', 'completado', 'cancelado') DEFAULT 'pendiente',
    fecha_pedido DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (usuario_id) REFERENCES usuarios(id),
    FOREIGN KEY (producto_id) REFERENCES productos(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Tabla de PINs
CREATE TABLE IF NOT EXISTS pines (
    id INT AUTO_INCREMENT PRIMARY KEY,
    producto_id INT NOT NULL,
    pin VARCHAR(255) NOT NULL,
    estado ENUM('disponible', 'usado', 'error') DEFAULT 'disponible',
    usado_por INT DEFAULT NULL,
    pedido_id INT DEFAULT NULL,
    nombre_juego VARCHAR(100) DEFAULT NULL,
    fecha_agregado DATETIME DEFAULT CURRENT_TIMESTAMP,
    fecha_usado DATETIME DEFAULT NULL,
    FOREIGN KEY (producto_id) REFERENCES productos(id),
    FOREIGN KEY (usado_por) REFERENCES usuarios(id),
    FOREIGN KEY (pedido_id) REFERENCES pedidos(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ===== DATOS INICIALES =====

-- Categorias
INSERT INTO categorias (nombre, slug, icono, descripcion, orden) VALUES
('Free Fire', 'freefire', 'fa-fire', 'Recargas de diamantes Free Fire', 1),
('Gift Cards', 'giftcard', 'fa-gift', 'Tarjetas de regalo para todas las plataformas', 2);

-- Free Fire
INSERT INTO productos (nombre, descripcion, precio, categoria, icono) VALUES
('100 Diamantes Free Fire', 'Recarga instantánea de 100 diamantes', 1.50, 'freefire', 'fa-gem'),
('310 Diamantes Free Fire', 'Recarga instantánea de 310 diamantes', 4.00, 'freefire', 'fa-gem'),
('520 Diamantes Free Fire', 'Recarga instantánea de 520 diamantes', 6.50, 'freefire', 'fa-gem'),
('1060 Diamantes Free Fire', 'Recarga instantánea de 1060 diamantes', 12.00, 'freefire', 'fa-gem'),
('2180 Diamantes Free Fire', 'Recarga instantánea de 2180 diamantes', 23.00, 'freefire', 'fa-gem'),
('5600 Diamantes Free Fire', 'Recarga instantánea de 5600 diamantes', 55.00, 'freefire', 'fa-gem');

-- Gift Cards
INSERT INTO productos (nombre, descripcion, precio, categoria, icono) VALUES
('Gift Card PlayStation $10', 'Tarjeta PlayStation Store $10 USD', 11.00, 'giftcard', 'fa-gift'),
('Gift Card PlayStation $25', 'Tarjeta PlayStation Store $25 USD', 27.00, 'giftcard', 'fa-gift'),
('Gift Card Xbox $10', 'Tarjeta Xbox Store $10 USD', 11.00, 'giftcard', 'fa-gift'),
('Gift Card Xbox $25', 'Tarjeta Xbox Store $25 USD', 27.00, 'giftcard', 'fa-gift'),
('Gift Card Steam $10', 'Tarjeta Steam Wallet $10 USD', 11.50, 'giftcard', 'fa-gift'),
('Gift Card Steam $20', 'Tarjeta Steam Wallet $20 USD', 22.00, 'giftcard', 'fa-gift'),
('Gift Card Google Play $10', 'Tarjeta Google Play $10 USD', 11.00, 'giftcard', 'fa-gift'),
('Gift Card iTunes $10', 'Tarjeta iTunes/App Store $10 USD', 11.50, 'giftcard', 'fa-gift');

-- Usuario admin (password: admin123)
INSERT INTO usuarios (nombre, email, password, telefono, rol) VALUES
('Admin', 'admin@gamersrev.com', '$2y$10$YjdSFnhmT1JV1de.5NJ8meeguBGSvO5y5r0eGlfdm/6c.6EhKfO.y', '0000000000', 'admin');
