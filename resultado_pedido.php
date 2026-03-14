<?php
require_once 'config.php';
require_once 'auth.php';
requiereLogin();

$db = conectarDB();
$usuario = obtenerUsuarioActual();
$pedido_id = intval($_GET['id'] ?? 0);

$stmt = $db->prepare("SELECT p.*, pr.nombre as producto_nombre, pr.icono, pr.categoria FROM pedidos p JOIN productos pr ON p.producto_id = pr.id WHERE p.id = ? AND p.usuario_id = ?");
$stmt->execute([$pedido_id, $usuario['id']]);
$pedido = $stmt->fetch();

if (!$pedido) {
    setFlash('Pedido no encontrado', 'error');
    header('Location: dashboard.php');
    exit;
}

// Obtener info del PIN usado
$stmt = $db->prepare("SELECT nombre_juego FROM pines WHERE pedido_id = ? AND estado = 'usado' LIMIT 1");
$stmt->execute([$pedido_id]);
$pin_info = $stmt->fetch();

$pagina_titulo = 'Pedido #' . $pedido_id;
require_once 'includes/header.php';
?>

<section class="section">
    <div class="container">
        <div class="resultado-card">
            <?php if ($pedido['estado'] === 'completado'): ?>
            <div class="resultado-icon resultado-success">
                <i class="fas fa-check-circle"></i>
            </div>
            <h1>¡Recarga Exitosa!</h1>
            <p class="resultado-desc">Los diamantes fueron enviados a tu cuenta de Free Fire</p>
            
            <div class="resultado-detalles">
                <div class="detalle-row">
                    <span>Pedido</span>
                    <strong>#<?php echo $pedido_id; ?></strong>
                </div>
                <div class="detalle-row">
                    <span>Producto</span>
                    <strong><?php echo htmlspecialchars($pedido['producto_nombre']); ?></strong>
                </div>
                <div class="detalle-row">
                    <span>Cantidad</span>
                    <strong>x<?php echo $pedido['cantidad']; ?></strong>
                </div>
                <div class="detalle-row">
                    <span>ID Free Fire</span>
                    <strong><?php echo htmlspecialchars($pedido['id_juego']); ?></strong>
                </div>
                <?php if ($pin_info && $pin_info['nombre_juego']): ?>
                <div class="detalle-row">
                    <span>Jugador</span>
                    <strong><?php echo htmlspecialchars($pin_info['nombre_juego']); ?></strong>
                </div>
                <?php endif; ?>
                <div class="detalle-row detalle-total">
                    <span>Total</span>
                    <strong>$<?php echo number_format($pedido['total'], 2); ?></strong>
                </div>
            </div>

            <?php else: ?>
            <div class="resultado-icon resultado-pending">
                <i class="fas fa-clock"></i>
            </div>
            <h1>Pedido Registrado</h1>
            <p class="resultado-desc">Tu pedido #<?php echo $pedido_id; ?> está pendiente de procesamiento</p>
            
            <div class="resultado-detalles">
                <div class="detalle-row">
                    <span>Producto</span>
                    <strong><?php echo htmlspecialchars($pedido['producto_nombre']); ?></strong>
                </div>
                <div class="detalle-row">
                    <span>Estado</span>
                    <span class="badge badge-<?php echo $pedido['estado']; ?>"><?php echo ucfirst($pedido['estado']); ?></span>
                </div>
                <div class="detalle-row detalle-total">
                    <span>Total</span>
                    <strong>$<?php echo number_format($pedido['total'], 2); ?></strong>
                </div>
            </div>
            <?php endif; ?>

            <div class="resultado-actions">
                <a href="dashboard.php" class="btn btn-primary"><i class="fas fa-home"></i> Ir al Dashboard</a>
                <a href="mis_pedidos.php" class="btn btn-secondary"><i class="fas fa-list"></i> Ver Pedidos</a>
            </div>
        </div>
    </div>
</section>

<?php require_once 'includes/footer.php'; ?>
