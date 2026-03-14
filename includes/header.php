<?php if (!isset($pagina_titulo)) $pagina_titulo = TIENDA_NOMBRE; ?>
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title><?php echo htmlspecialchars($pagina_titulo); ?> - <?php echo TIENDA_NOMBRE; ?></title>
    <link rel="stylesheet" href="css/styles.css">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
</head>
<body>
    <?php $flash = getFlash(); if ($flash): ?>
    <div class="flash-message flash-<?php echo $flash['tipo']; ?>">
        <div class="container">
            <i class="fas <?php echo $flash['tipo'] === 'success' ? 'fa-check-circle' : 'fa-exclamation-circle'; ?>"></i>
            <?php echo htmlspecialchars($flash['mensaje']); ?>
        </div>
    </div>
    <?php endif; ?>

    <nav class="navbar">
        <div class="container nav-container">
            <a href="<?php echo estaLogueado() ? 'dashboard.php' : 'login.php'; ?>" class="nav-logo">
                <i class="fas fa-gamepad"></i> <?php echo TIENDA_NOMBRE; ?>
            </a>
            <?php if (estaLogueado()): ?>
            <div class="nav-links">
                <a href="dashboard.php"><i class="fas fa-home"></i> Inicio</a>
                <a href="freefire.php"><i class="fas fa-fire"></i> Free Fire</a>
                <a href="giftcards.php"><i class="fas fa-gift"></i> Gift Cards</a>
                <a href="mis_pedidos.php"><i class="fas fa-shopping-bag"></i> Mis Pedidos</a>
            </div>
            <div class="nav-user">
                <span class="nav-username"><i class="fas fa-user-circle"></i> <?php echo htmlspecialchars($_SESSION['usuario_nombre']); ?></span>
                <a href="logout.php" class="btn btn-sm btn-outline"><i class="fas fa-sign-out-alt"></i> Salir</a>
            </div>
            <?php else: ?>
            <div class="nav-links">
                <a href="login.php"><i class="fas fa-sign-in-alt"></i> Iniciar Sesión</a>
                <a href="registro.php"><i class="fas fa-user-plus"></i> Registrarse</a>
            </div>
            <?php endif; ?>
            <button class="nav-toggle" id="navToggle">
                <i class="fas fa-bars"></i>
            </button>
        </div>
    </nav>
    <main class="main-content">
