-- ===== BASE DE DATOS GAMERSREV =====
-- Ejecutar este script en phpMyAdmin de cPanel

CREATE DATABASE IF NOT EXISTS gamersrev_tienda CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE gamersrev_tienda;

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

-- ===== PRODUCTOS INICIALES =====

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
