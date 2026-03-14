<?php
require_once 'config.php';
require_once 'auth.php';

// Si ya está logueado, redirigir al dashboard
if (estaLogueado()) {
    header('Location: dashboard.php');
    exit;
}

$error = '';
$exito = '';

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $nombre = trim($_POST['nombre'] ?? '');
    $email = trim($_POST['email'] ?? '');
    $telefono = trim($_POST['telefono'] ?? '');
    $password = $_POST['password'] ?? '';
    $password2 = $_POST['password2'] ?? '';
    
    if (empty($nombre) || empty($email) || empty($password)) {
        $error = 'Por favor completa todos los campos obligatorios';
    } elseif (strlen($password) < 6) {
        $error = 'La contraseña debe tener al menos 6 caracteres';
    } elseif ($password !== $password2) {
        $error = 'Las contraseñas no coinciden';
    } elseif (!filter_var($email, FILTER_VALIDATE_EMAIL)) {
        $error = 'El correo electrónico no es válido';
    } else {
        $resultado = registrarUsuario($nombre, $email, $password, $telefono);
        if ($resultado['ok']) {
            // Auto-login después del registro
            loginUsuario($email, $password);
            setFlash('¡Bienvenido a ' . TIENDA_NOMBRE . '! Tu cuenta ha sido creada.');
            header('Location: dashboard.php');
            exit;
        } else {
            $error = $resultado['error'];
        }
    }
}

$pagina_titulo = 'Crear Cuenta';
?>
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title><?php echo $pagina_titulo; ?> - <?php echo TIENDA_NOMBRE; ?></title>
    <link rel="stylesheet" href="css/styles.css">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
</head>
<body class="auth-page">
    <div class="auth-container">
        <div class="auth-card">
            <div class="auth-header">
                <div class="auth-logo">
                    <i class="fas fa-gamepad"></i>
                </div>
                <h1><?php echo TIENDA_NOMBRE; ?></h1>
                <p>Crea tu cuenta para empezar a comprar</p>
            </div>

            <?php if ($error): ?>
            <div class="alert alert-error">
                <i class="fas fa-exclamation-circle"></i> <?php echo htmlspecialchars($error); ?>
            </div>
            <?php endif; ?>

            <form method="POST" class="auth-form">
                <div class="form-group">
                    <label for="nombre"><i class="fas fa-user"></i> Nombre completo *</label>
                    <input type="text" id="nombre" name="nombre" placeholder="Tu nombre" 
                           value="<?php echo htmlspecialchars($nombre ?? ''); ?>" required>
                </div>
                <div class="form-group">
                    <label for="email"><i class="fas fa-envelope"></i> Correo electrónico *</label>
                    <input type="email" id="email" name="email" placeholder="tucorreo@ejemplo.com" 
                           value="<?php echo htmlspecialchars($email ?? ''); ?>" required>
                </div>
                <div class="form-group">
                    <label for="telefono"><i class="fas fa-phone"></i> Teléfono / WhatsApp</label>
                    <input type="text" id="telefono" name="telefono" placeholder="Ej: +57 300 1234567" 
                           value="<?php echo htmlspecialchars($telefono ?? ''); ?>">
                </div>
                <div class="form-group">
                    <label for="password"><i class="fas fa-lock"></i> Contraseña * (mín. 6 caracteres)</label>
                    <input type="password" id="password" name="password" placeholder="Tu contraseña" required>
                </div>
                <div class="form-group">
                    <label for="password2"><i class="fas fa-lock"></i> Confirmar contraseña *</label>
                    <input type="password" id="password2" name="password2" placeholder="Repite tu contraseña" required>
                </div>
                <button type="submit" class="btn btn-primary btn-block">
                    <i class="fas fa-user-plus"></i> Crear Cuenta
                </button>
            </form>

            <div class="auth-footer">
                <p>¿Ya tienes cuenta? <a href="login.php">Inicia sesión aquí</a></p>
            </div>
        </div>
    </div>
</body>
</html>
