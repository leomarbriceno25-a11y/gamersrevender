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

// === VERIFICAR SALDO EN CARTERA ===
$db->beginTransaction();
try {
    // Crear cartera si no existe
    $stmt = $db->prepare("INSERT IGNORE INTO carteras (usuario_id, saldo) VALUES (?, 0.00)");
    $stmt->execute([$usuario['id']]);

    // Obtener saldo con bloqueo
    $stmt = $db->prepare("SELECT saldo FROM carteras WHERE usuario_id = ? FOR UPDATE");
    $stmt->execute([$usuario['id']]);
    $saldo_actual = $stmt->fetchColumn();

    if ($saldo_actual < $total) {
        $db->rollBack();
        setFlash('Saldo insuficiente. Tu saldo es $' . number_format($saldo_actual, 2) . ' y el total es $' . number_format($total, 2) . '. Recarga tu cartera para continuar.', 'error');
        header('Location: producto.php?id=' . $producto_id);
        exit;
    }

    $saldo_nuevo = $saldo_actual - $total;

    // Descontar saldo
    $stmt = $db->prepare("UPDATE carteras SET saldo = ? WHERE usuario_id = ?");
    $stmt->execute([$saldo_nuevo, $usuario['id']]);

    // Guardar pedido
    $stmt = $db->prepare("INSERT INTO pedidos (usuario_id, producto_id, cantidad, total, id_juego, metodo_pago) VALUES (?, ?, ?, ?, ?, 'cartera')");
    $stmt->execute([$usuario['id'], $producto_id, $cantidad, $total, $id_juego]);
    $pedido_id = $db->lastInsertId();

    // Registrar transacción
    $stmt = $db->prepare("INSERT INTO transacciones (usuario_id, tipo, monto, saldo_anterior, saldo_nuevo, descripcion, pedido_id) VALUES (?, 'compra', ?, ?, ?, ?, ?)");
    $stmt->execute([$usuario['id'], $total, $saldo_actual, $saldo_nuevo, 'Compra: ' . $producto['nombre'] . ' x' . $cantidad, $pedido_id]);

    $db->commit();
} catch (Exception $e) {
    $db->rollBack();
    setFlash('Error al procesar el pago: ' . $e->getMessage(), 'error');
    header('Location: producto.php?id=' . $producto_id);
    exit;
}

// === CANJE AUTOMÁTICO (solo para Free Fire con ID de juego) ===
$canje_exitoso = false;
$canje_resultados = [];

if ($producto['categoria'] === 'freefire' && !empty($id_juego)) {
    for ($i = 0; $i < $cantidad; $i++) {
        $stmt = $db->prepare("SELECT * FROM pines WHERE producto_id = ? AND estado = 'disponible' LIMIT 1");
        $stmt->execute([$producto_id]);
        $pin_disponible = $stmt->fetch();

        if (!$pin_disponible) {
            $canje_resultados[] = ['ok' => false, 'error' => 'Sin stock de PINs'];
            break;
        }

        $stmt = $db->prepare("UPDATE pines SET estado = 'usado', usado_por = ?, pedido_id = ?, fecha_usado = NOW() WHERE id = ?");
        $stmt->execute([$usuario['id'], $pedido_id, $pin_disponible['id']]);

        $resultado = hypeCanjearPin($pin_disponible['pin'], $id_juego);
        $canje_resultados[] = $resultado;

        if ($resultado['ok']) {
            $stmt = $db->prepare("UPDATE pines SET nombre_juego = ? WHERE id = ?");
            $stmt->execute([$resultado['username'] ?? '', $pin_disponible['id']]);
            $canje_exitoso = true;
        } else {
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

// Si no hay stock de PINs o es gift card, pedido queda pendiente para procesamiento manual
$stmt = $db->prepare("UPDATE pedidos SET estado = 'pendiente' WHERE id = ?");
$stmt->execute([$pedido_id]);
setFlash('¡Pedido #' . $pedido_id . ' registrado! Se descontaron $' . number_format($total, 2) . ' de tu cartera. Tu pedido será procesado pronto.', 'success');
header('Location: resultado_pedido.php?id=' . $pedido_id);
exit;
?>
