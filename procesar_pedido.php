<?php
require_once 'config.php';
require_once 'auth.php';
requiereLogin();

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    header('Location: dashboard.php');
    exit;
}

$db = conectarDB();
$usuario = obtenerUsuarioActual();

$producto_id = intval($_POST['producto_id'] ?? 0);
$cantidad = intval($_POST['cantidad'] ?? 1);
$id_juego = trim($_POST['id_juego'] ?? '');
$metodo_pago = trim($_POST['metodo_pago'] ?? '');

// Obtener producto
$stmt = $db->prepare("SELECT * FROM productos WHERE id = ? AND activo = 1");
$stmt->execute([$producto_id]);
$producto = $stmt->fetch();

if (!$producto) {
    setFlash('Producto no encontrado', 'error');
    header('Location: dashboard.php');
    exit;
}

$total = $producto['precio'] * $cantidad;

// Guardar pedido en la base de datos
$stmt = $db->prepare("INSERT INTO pedidos (usuario_id, producto_id, cantidad, total, id_juego, metodo_pago) VALUES (?, ?, ?, ?, ?, ?)");
$stmt->execute([$usuario['id'], $producto_id, $cantidad, $total, $id_juego, $metodo_pago]);

// Construir mensaje de WhatsApp
$mensaje = "🎮 *NUEVO PEDIDO - " . TIENDA_NOMBRE . "*\n\n";
$mensaje .= "👤 *Cliente:* " . $usuario['nombre'] . "\n";
$mensaje .= "📧 *Email:* " . $usuario['email'] . "\n";
$mensaje .= "🛒 *Producto:* " . $producto['nombre'] . "\n";
$mensaje .= "📦 *Cantidad:* x" . $cantidad . "\n";
$mensaje .= "💰 *Total:* $" . number_format($total, 2) . "\n";
$mensaje .= "💳 *Método de pago:* " . $metodo_pago . "\n";

if (!empty($id_juego)) {
    $mensaje .= "🎯 *ID Free Fire:* " . $id_juego . "\n";
}

$mensaje .= "\n¡Hola! Quiero realizar este pedido. ¿Cómo procedo con el pago?";

// Redirigir a WhatsApp
$whatsapp_url = "https://wa.me/" . WHATSAPP_NUMERO . "?text=" . urlencode($mensaje);
header("Location: $whatsapp_url");
exit;
?>
