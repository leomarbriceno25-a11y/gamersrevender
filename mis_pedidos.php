<?php
require_once 'config.php';
require_once 'auth.php';
requiereLogin();

$db = conectarDB();
$usuario = obtenerUsuarioActual();

$stmt = $db->prepare("SELECT p.*, pr.nombre as producto_nombre FROM pedidos p JOIN productos pr ON p.producto_id = pr.id WHERE p.usuario_id = ? ORDER BY p.fecha_pedido DESC");
$stmt->execute([$usuario['id']]);
$pedidos = $stmt->fetchAll();

$pagina_titulo = 'Mis Pedidos';
require_once 'includes/header.php';
?>

<section class="page-header">
    <div class="container">
        <h1><i class="fas fa-shopping-bag"></i> Mis <span class="text-gradient">Pedidos</span></h1>
        <p>Historial de todos tus pedidos</p>
    </div>
</section>

<section class="section">
    <div class="container">
        <?php if (empty($pedidos)): ?>
        <div class="empty-state">
            <i class="fas fa-shopping-cart"></i>
            <h2>No tienes pedidos aún</h2>
            <p>Explora nuestro catálogo y realiza tu primer pedido</p>
            <div class="empty-buttons">
                <a href="freefire.php" class="btn btn-primary"><i class="fas fa-fire"></i> Free Fire</a>
                <a href="giftcards.php" class="btn btn-secondary"><i class="fas fa-gift"></i> Gift Cards</a>
            </div>
        </div>
        <?php else: ?>
        <div class="table-responsive">
            <table class="table">
                <thead>
                    <tr>
                        <th>#</th>
                        <th>Producto</th>
                        <th>Cantidad</th>
                        <th>Total</th>
                        <th>Método de pago</th>
                        <th>Estado</th>
                        <th>Fecha</th>
                    </tr>
                </thead>
                <tbody>
                    <?php foreach ($pedidos as $pedido): ?>
                    <tr>
                        <td><?php echo $pedido['id']; ?></td>
                        <td><?php echo htmlspecialchars($pedido['producto_nombre']); ?></td>
                        <td>x<?php echo $pedido['cantidad']; ?></td>
                        <td>$<?php echo number_format($pedido['total'], 2); ?></td>
                        <td><?php echo htmlspecialchars(ucfirst($pedido['metodo_pago'] ?? '-')); ?></td>
                        <td>
                            <span class="badge badge-<?php echo $pedido['estado']; ?>">
                                <?php echo ucfirst($pedido['estado']); ?>
                            </span>
                        </td>
                        <td><?php echo date('d/m/Y H:i', strtotime($pedido['fecha_pedido'])); ?></td>
                    </tr>
                    <?php endforeach; ?>
                </tbody>
            </table>
        </div>
        <?php endif; ?>
    </div>
</section>

<?php require_once 'includes/footer.php'; ?>
