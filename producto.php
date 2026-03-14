<?php
require_once 'config.php';
require_once 'auth.php';
requiereLogin();

$db = conectarDB();
$id = intval($_GET['id'] ?? 0);

$stmt = $db->prepare("SELECT * FROM productos WHERE id = ? AND activo = 1");
$stmt->execute([$id]);
$producto = $stmt->fetch();

if (!$producto) {
    setFlash('Producto no encontrado', 'error');
    header('Location: dashboard.php');
    exit;
}

$pagina_titulo = $producto['nombre'];
require_once 'includes/header.php';
?>

<section class="page-header">
    <div class="container">
        <a href="<?php echo $producto['categoria'] === 'freefire' ? 'freefire.php' : 'giftcards.php'; ?>" class="btn-back">
            <i class="fas fa-arrow-left"></i> Volver al catálogo
        </a>
    </div>
</section>

<section class="section">
    <div class="container">
        <div class="product-detail">
            <div class="product-detail-info">
                <div class="product-detail-icon">
                    <i class="fas <?php echo htmlspecialchars($producto['icono']); ?>"></i>
                </div>
                <h1><?php echo htmlspecialchars($producto['nombre']); ?></h1>
                <p class="product-detail-desc"><?php echo htmlspecialchars($producto['descripcion']); ?></p>
                <div class="product-detail-price">$<?php echo number_format($producto['precio'], 2); ?></div>
                <div class="product-badges">
                    <span class="badge badge-success"><i class="fas fa-bolt"></i> Entrega instantánea</span>
                    <span class="badge badge-info"><i class="fas fa-shield-alt"></i> 100% Seguro</span>
                </div>
            </div>

            <div class="product-detail-form">
                <h2><i class="fas fa-shopping-cart"></i> Realizar Pedido</h2>
                <form method="POST" action="procesar_pedido.php">
                    <input type="hidden" name="producto_id" value="<?php echo $producto['id']; ?>">

                    <?php if ($producto['categoria'] === 'freefire'): ?>
                    <div class="form-group">
                        <label for="id_juego"><i class="fas fa-gamepad"></i> ID de Free Fire *</label>
                        <input type="text" id="id_juego" name="id_juego" placeholder="Ingresa tu ID de Free Fire" required>
                    </div>
                    <?php endif; ?>

                    <div class="form-group">
                        <label for="cantidad"><i class="fas fa-sort-numeric-up"></i> Cantidad</label>
                        <select id="cantidad" name="cantidad" class="form-select">
                            <?php for ($i = 1; $i <= 10; $i++): ?>
                            <option value="<?php echo $i; ?>">x<?php echo $i; ?> - $<?php echo number_format($producto['precio'] * $i, 2); ?></option>
                            <?php endfor; ?>
                        </select>
                    </div>

                    <div class="form-group">
                        <label for="metodo_pago"><i class="fas fa-credit-card"></i> Método de pago</label>
                        <select id="metodo_pago" name="metodo_pago" class="form-select" required>
                            <option value="">Selecciona...</option>
                            <option value="transferencia">Transferencia bancaria</option>
                            <option value="pago_movil">Pago Móvil</option>
                            <option value="binance">Binance Pay / USDT</option>
                            <option value="paypal">PayPal</option>
                            <option value="zinli">Zinli</option>
                        </select>
                    </div>

                    <div class="order-summary">
                        <h3>Resumen</h3>
                        <div class="summary-row">
                            <span><?php echo htmlspecialchars($producto['nombre']); ?></span>
                            <span id="summary-price">$<?php echo number_format($producto['precio'], 2); ?></span>
                        </div>
                        <div class="summary-total">
                            <span>Total:</span>
                            <span id="summary-total">$<?php echo number_format($producto['precio'], 2); ?></span>
                        </div>
                    </div>

                    <button type="submit" class="btn btn-primary btn-block btn-lg">
                        <i class="fab fa-whatsapp"></i> Pedir por WhatsApp
                    </button>
                </form>
            </div>
        </div>
    </div>
</section>

<script>
    const precio = <?php echo $producto['precio']; ?>;
    const cantidadSelect = document.getElementById('cantidad');
    const summaryPrice = document.getElementById('summary-price');
    const summaryTotal = document.getElementById('summary-total');
    
    cantidadSelect.addEventListener('change', function() {
        const total = (precio * parseInt(this.value)).toFixed(2);
        summaryPrice.textContent = '$' + total;
        summaryTotal.textContent = '$' + total;
    });
</script>

<?php require_once 'includes/footer.php'; ?>
