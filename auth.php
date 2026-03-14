<?php
require_once 'config.php';

// Registrar nuevo usuario
function registrarUsuario($nombre, $email, $password, $telefono = null) {
    $db = conectarDB();
    
    // Verificar si el email ya existe
    $stmt = $db->prepare("SELECT id FROM usuarios WHERE email = ?");
    $stmt->execute([$email]);
    if ($stmt->fetch()) {
        return ['ok' => false, 'error' => 'Este correo ya está registrado'];
    }
    
    // Encriptar password
    $hash = password_hash($password, PASSWORD_BCRYPT);
    
    // Insertar usuario
    $stmt = $db->prepare("INSERT INTO usuarios (nombre, email, password, telefono) VALUES (?, ?, ?, ?)");
    $stmt->execute([$nombre, $email, $hash, $telefono]);
    
    return ['ok' => true, 'id' => $db->lastInsertId()];
}

// Iniciar sesión
function loginUsuario($email, $password) {
    $db = conectarDB();
    
    $stmt = $db->prepare("SELECT * FROM usuarios WHERE email = ? AND activo = 1");
    $stmt->execute([$email]);
    $usuario = $stmt->fetch();
    
    if (!$usuario || !password_verify($password, $usuario['password'])) {
        return ['ok' => false, 'error' => 'Correo o contraseña incorrectos'];
    }
    
    // Crear sesión
    $_SESSION['usuario_id'] = $usuario['id'];
    $_SESSION['usuario_nombre'] = $usuario['nombre'];
    $_SESSION['usuario_email'] = $usuario['email'];
    $_SESSION['usuario_rol'] = $usuario['rol'];
    
    // Actualizar último login
    $stmt = $db->prepare("UPDATE usuarios SET ultimo_login = NOW() WHERE id = ?");
    $stmt->execute([$usuario['id']]);
    
    return ['ok' => true];
}

// Cerrar sesión
function logoutUsuario() {
    session_destroy();
    header('Location: login.php');
    exit;
}
?>
