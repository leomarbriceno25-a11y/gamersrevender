<?php
require_once 'config.php';
require_once 'auth.php';
requiereLogin();

$db = conectarDB();
$usuario = obtenerUsuarioActual();

// Crear cartera si no existe
$stmt = $db->prepare("SELECT * FROM carteras WHERE usuario_id = ?");
$stmt->execute([$usuario['id']]);
$cartera = $stmt->fetch();

if (!$cartera) {
    $stmt = $db->prepare("INSERT INTO carteras (usuario_id, saldo) VALUES (?, 0.00)");
    $stmt->execute([$usuario['id']]);
    $cartera = ['saldo' => 0.00];
}

// Obtener transacciones
$stmt = $db->prepare("SELECT t.*, u.nombre as admin_nombre FROM transacciones t LEFT JOIN usuarios u ON t.admin_id = u.id WHERE t.usuario_id = ? ORDER BY t.fecha DESC LIMIT 50");
$stmt->execute([$usuario['id']]);
$transacciones = $stmt->fetchAll();

$pagina_titulo = 'Mi Cartera';
require_once 'includes/header.php';
?>

<section class="page-header">
    <div class="container">
        <h1><i class="fas fa-wallet"></i> Mi Cartera</h1>
        <p>Gestiona tu saldo y revisa tus movimientos</p>
    </div>
</section>

<section class="section">
    <div class="container">
        <div class="wallet-overview">
            <div class="wallet-balance-card">
                <div class="wallet-balance-icon">
                    <i class="fas fa-wallet"></i>
                </div>
                <div class="wallet-balance-info">
                    <span class="wallet-balance-label">Saldo disponible</span>
                    <span class="wallet-balance-amount">$<?php echo number_format($cartera['saldo'], 2); ?></span>
                </div>
            </div>
            <div class="wallet-actions">
                <a href="freefire.php" class="btn btn-primary"><i class="fas fa-fire"></i> Comprar Diamantes</a>
                <a href="giftcards.php" class="btn btn-secondary"><i class="fas fa-gift"></i> Gift Cards</a>
            </div>
        </div>

        <div class="wallet-info-box">
            <i class="fas fa-info-circle"></i>
            <p>Para recargar tu cartera, contacta al administrador por WhatsApp. Una vez confirmado el pago, tu saldo se actualizará automáticamente.</p>
            <a href="https://wa.me/<?php echo WHATSAPP_NUMERO; ?>?text=Hola!%20Quiero%20recargar%20mi%20cartera.%20Mi%20email%20es:%20<?php echo urlencode($usuario['email']); ?>" class="btn btn-sm btn-success" target="_blank">
                <i class="fab fa-whatsapp"></i> Recargar Saldo
            </a>
        </div>
    </div>
</section>

<section class="section">
    <div class="container">
        <h2 class="section-title"><i class="fas fa-exchange-alt"></i> Historial de Movimientos</h2>
        <?php if (empty($transacciones)): ?>
        <div class="empty-state">
            <i class="fas fa-receipt"></i>
            <h2>Sin movimientos</h2>
            <p>Aún no tienes transacciones en tu cartera</p>
        </div>
        <?php else: ?>
        <div class="table-responsive">
            <table class="table">
                <thead>
                    <tr>
                        <th>Fecha</th>
                        <th>Tipo</th>
                        <th>Descripción</th>
                        <th>Monto</th>
                        <th>Saldo</th>
                    </tr>
                </thead>
                <tbody>
                    <?php foreach ($transacciones as $t): ?>
                    <tr>
                        <td><?php echo date('d/m/Y H:i', strtotime($t['fecha'])); ?></td>
                        <td>
                            <?php if ($t['tipo'] === 'recarga'): ?>
                                <span class="badge badge-completado"><i class="fas fa-arrow-up"></i> Recarga</span>
                            <?php elseif ($t['tipo'] === 'compra'): ?>
                                <span class="badge badge-procesando"><i class="fas fa-arrow-down"></i> Compra</span>
                            <?php else: ?>
                                <span class="badge badge-pendiente"><i class="fas fa-undo"></i> Reembolso</span>
                            <?php endif; ?>
                        </td>
                        <td><?php echo htmlspecialchars($t['descripcion'] ?? '-'); ?></td>
                        <td class="<?php echo $t['tipo'] === 'compra' ? 'text-red' : 'text-green'; ?>">
                            <?php echo $t['tipo'] === 'compra' ? '-' : '+'; ?>$<?php echo number_format($t['monto'], 2); ?>
                        </td>
                        <td>$<?php echo number_format($t['saldo_nuevo'], 2); ?></td>
                    </tr>
                    <?php endforeach; ?>
                </tbody>
            </table>
        </div>
        <?php endif; ?>
    </div>
</section>

<?php require_once 'includes/footer.php'; ?>
