<?php
require_once 'config.php';
require_once 'auth.php';
requiereLogin();

$db = conectarDB();
$stmt = $db->query("SELECT * FROM productos WHERE categoria = 'freefire' AND activo = 1 ORDER BY precio ASC");
$productos = $stmt->fetchAll();

$pagina_titulo = 'Diamantes Free Fire';
require_once 'includes/header.php';
?>

<section class="page-header">
    <div class="container">
        <h1><i class="fas fa-fire"></i> Diamantes <span class="text-gradient">Free Fire</span></h1>
        <p>Recargas instantáneas al mejor precio</p>
    </div>
</section>

<section class="section">
    <div class="container">
        <div class="products-grid">
            <?php foreach ($productos as $prod): ?>
            <div class="product-card">
                <div class="product-icon product-icon-fire">
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

<?php require_once 'includes/footer.php'; ?>
