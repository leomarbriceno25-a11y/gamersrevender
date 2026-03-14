<?php
require_once 'config.php';
require_once 'auth.php';
require_once 'hype_api.php';
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
$pedido_id = $db->lastInsertId();

// === CANJE AUTOMÁTICO (solo para Free Fire con ID de juego) ===
$canje_exitoso = false;
$canje_resultados = [];

if ($producto['categoria'] === 'freefire' && !empty($id_juego)) {
    for ($i = 0; $i < $cantidad; $i++) {
        // Buscar un PIN disponible para este producto
        $stmt = $db->prepare("SELECT * FROM pines WHERE producto_id = ? AND estado = 'disponible' LIMIT 1");
        $stmt->execute([$producto_id]);
        $pin_disponible = $stmt->fetch();

        if (!$pin_disponible) {
            $canje_resultados[] = ['ok' => false, 'error' => 'Sin stock de PINs'];
            break;
        }

        // Marcar PIN como "procesando" temporalmente
        $stmt = $db->prepare("UPDATE pines SET estado = 'usado', usado_por = ?, pedido_id = ?, fecha_usado = NOW() WHERE id = ?");
        $stmt->execute([$usuario['id'], $pedido_id, $pin_disponible['id']]);

        // Intentar canjear el PIN
        $resultado = hypeCanjearPin($pin_disponible['pin'], $id_juego);
        $canje_resultados[] = $resultado;

        if ($resultado['ok']) {
            // Guardar nombre del jugador
            $stmt = $db->prepare("UPDATE pines SET nombre_juego = ? WHERE id = ?");
            $stmt->execute([$resultado['username'] ?? '', $pin_disponible['id']]);
            $canje_exitoso = true;
        } else {
            // Marcar PIN como error
            $stmt = $db->prepare("UPDATE pines SET estado = 'error' WHERE id = ?");
            $stmt->execute([$pin_disponible['id']]);
        }
    }
}

// Actualizar estado del pedido
if ($canje_exitoso) {
    $stmt = $db->prepare("UPDATE pedidos SET estado = 'completado' WHERE id = ?");
    $stmt->execute([$pedido_id]);
    setFlash('¡Recarga exitosa! Los diamantes fueron enviados a tu cuenta ' . $id_juego, 'success');
    header('Location: resultado_pedido.php?id=' . $pedido_id);
    exit;
}

// Si no hay stock o es gift card, redirigir a WhatsApp
$stmt = $db->prepare("UPDATE pedidos SET estado = 'pendiente' WHERE id = ?");
$stmt->execute([$pedido_id]);

$mensaje = "🎮 *NUEVO PEDIDO #$pedido_id - " . TIENDA_NOMBRE . "*\n\n";
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

$whatsapp_url = "https://wa.me/" . WHATSAPP_NUMERO . "?text=" . urlencode($mensaje);
header("Location: $whatsapp_url");
exit;
?>
