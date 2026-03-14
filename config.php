<?php
// ===== CONFIGURACIÓN DE LA TIENDA =====
session_start();

// Configuración de la base de datos
define('DB_HOST', 'localhost');
define('DB_NAME', 'gamersre_tienda');
define('DB_USER', 'gamersre_admin');
define('DB_PASS', 'Leomar.27');

// Configuración de la tienda
define('TIENDA_NOMBRE', 'GamersRev');
define('TIENDA_EMAIL', 'ventas@gamersrevender.com');
define('TIENDA_INSTAGRAM', '@gamersrev');
define('TIENDA_HORARIO', 'Lun - Dom: 9am - 11pm');
define('WHATSAPP_NUMERO', '521234567890'); // <-- CAMBIA POR TU NÚMERO REAL

// Conexión a la base de datos
function conectarDB() {
    try {
        $pdo = new PDO(
            "mysql:host=" . DB_HOST . ";dbname=" . DB_NAME . ";charset=utf8mb4",
            DB_USER,
            DB_PASS,
            [
                PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
                PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
                PDO::ATTR_EMULATE_PREPARES => false,
            ]
        );
        return $pdo;
    } catch (PDOException $e) {
        die("Error de conexión: " . $e->getMessage());
    }
}

// Funciones helper de sesión
function estaLogueado() {
    return isset($_SESSION['usuario_id']);
}

function requiereLogin() {
    if (!estaLogueado()) {
        header('Location: login.php');
        exit;
    }
}

function esAdmin() {
    return isset($_SESSION['usuario_rol']) && $_SESSION['usuario_rol'] === 'admin';
}

function obtenerUsuarioActual() {
    if (!estaLogueado()) return null;
    return [
        'id' => $_SESSION['usuario_id'],
        'nombre' => $_SESSION['usuario_nombre'],
        'email' => $_SESSION['usuario_email'],
        'rol' => $_SESSION['usuario_rol'],
    ];
}

// Función para mensajes flash
function setFlash($mensaje, $tipo = 'success') {
    $_SESSION['flash_msg'] = $mensaje;
    $_SESSION['flash_tipo'] = $tipo;
}

function getFlash() {
    if (isset($_SESSION['flash_msg'])) {
        $msg = $_SESSION['flash_msg'];
        $tipo = $_SESSION['flash_tipo'];
        unset($_SESSION['flash_msg'], $_SESSION['flash_tipo']);
        return ['mensaje' => $msg, 'tipo' => $tipo];
    }
    return null;
}
?>
