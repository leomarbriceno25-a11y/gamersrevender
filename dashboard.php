<?php
require_once 'config.php';
require_once 'auth.php';
requiereLogin();

$db = conectarDB();
$usuario = obtenerUsuarioActual();

// Obtener estadísticas del usuario
$stmt = $db->prepare("SELECT COUNT(*) as total_pedidos, COALESCE(SUM(total), 0) as total_gastado FROM pedidos WHERE usuario_id = ?");
$stmt->execute([$usuario['id']]);
$stats = $stmt->fetch();

// Obtener saldo de cartera
$stmt = $db->prepare("SELECT COALESCE(saldo, 0) as saldo FROM carteras WHERE usuario_id = ?");
$stmt->execute([$usuario['id']]);
$saldo = $stmt->fetchColumn() ?: 0;

// Obtener últimos pedidos
$stmt = $db->prepare("SELECT p.*, pr.nombre as producto_nombre FROM pedidos p JOIN productos pr ON p.producto_id = pr.id WHERE p.usuario_id = ? ORDER BY p.fecha_pedido DESC LIMIT 5");
$stmt->execute([$usuario['id']]);
$ultimos_pedidos = $stmt->fetchAll();

// Obtener productos destacados
$stmt = $db->query("SELECT * FROM productos WHERE activo = 1 ORDER BY RAND() LIMIT 6");
$productos_destacados = $stmt->fetchAll();

$pagina_titulo = 'Panel Principal';
require_once 'includes/header.php';
?>

<!-- Hero Dashboard -->
<section class="dashboard-hero">
    <div class="container">
        <h1>Bienvenido, <span class="text-gradient"><?php echo htmlspecialchars($usuario['nombre']); ?></span></h1>
        <p>Panel de revendedor - Aquí puedes gestionar tus pedidos y compras</p>
    </div>
</section>

<!-- Stats -->
<section class="section">
    <div class="container">
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-icon"><i class="fas fa-shopping-cart"></i></div>
                <div class="stat-info">
                    <h3><?php echo $stats['total_pedidos']; ?></h3>
                    <p>Pedidos realizados</p>
                </div>
            </div>
            <div class="stat-card stat-card-wallet">
                <div class="stat-icon"><i class="fas fa-wallet"></i></div>
                <div class="stat-info">
                    <h3><a href="cartera.php" class="stat-link">$<?php echo number_format($saldo, 2); ?></a></h3>
                    <p>Saldo en cartera</p>
                </div>
            </div>
            <div class="stat-card">
                <div class="stat-icon"><i class="fas fa-fire"></i></div>
                <div class="stat-info">
                    <h3><a href="freefire.php" class="stat-link">Ver catálogo</a></h3>
                    <p>Diamantes Free Fire</p>
                </div>
            </div>
            <div class="stat-card">
                <div class="stat-icon"><i class="fas fa-gift"></i></div>
                <div class="stat-info">
                    <h3><a href="giftcards.php" class="stat-link">Ver catálogo</a></h3>
                    <p>Gift Cards</p>
                </div>
            </div>
        </div>
    </div>
</section>

<!-- Productos Destacados -->
<section class="section">
    <div class="container">
        <h2 class="section-title"><i class="fas fa-star"></i> Productos Destacados</h2>
        <div class="products-grid">
            <?php foreach ($productos_destacados as $prod): ?>
            <div class="product-card">
                <div class="product-icon">
                    <i class="fas <?php echo htmlspecialchars($prod['icono']); ?>"></i>
                </div>
                <h3><?php echo htmlspecialchars($prod['nombre']); ?></h3>
                <p class="product-desc"><?php echo htmlspecialchars($prod['descripcion']); ?></p>
                <div class="product-price">$<?php echo number_format($prod['precio'], 2); ?></div>
                <a href="producto.php?id=<?php echo $prod['id']; ?>" class="btn btn-primary btn-sm">
                    <i class="fas fa-shopping-cart"></i> Comprar
                </a>
            </div>
            <?php endforeach; ?>
        </div>
    </div>
</section>

<!-- Últimos Pedidos -->
<?php if (!empty($ultimos_pedidos)): ?>
<section class="section">
    <div class="container">
        <h2 class="section-title"><i class="fas fa-history"></i> Últimos Pedidos</h2>
        <div class="table-responsive">
            <table class="table">
                <thead>
                    <tr>
                        <th>#</th>
                        <th>Producto</th>
                        <th>Total</th>
                        <th>Estado</th>
                        <th>Fecha</th>
                    </tr>
                </thead>
                <tbody>
                    <?php foreach ($ultimos_pedidos as $pedido): ?>
                    <tr>
                        <td><?php echo $pedido['id']; ?></td>
                        <td><?php echo htmlspecialchars($pedido['producto_nombre']); ?></td>
                        <td>$<?php echo number_format($pedido['total'], 2); ?></td>
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
        <div class="text-center" style="margin-top: 1rem;">
            <a href="mis_pedidos.php" class="btn btn-secondary">Ver todos los pedidos</a>
        </div>
    </div>
</section>
<?php endif; ?>

<?php require_once 'includes/footer.php'; ?>
