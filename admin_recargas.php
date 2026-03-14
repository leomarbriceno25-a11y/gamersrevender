<?php
require_once 'config.php';
require_once 'auth.php';
requiereLogin();

if (!esAdmin()) {
    setFlash('Acceso denegado', 'error');
    header('Location: dashboard.php');
    exit;
}

$db = conectarDB();
$usuario = obtenerUsuarioActual();
$mensaje = '';
$tipo_msg = '';

// Procesar recarga de saldo
if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['accion'])) {
    if ($_POST['accion'] === 'recargar') {
        $usuario_id = intval($_POST['usuario_id'] ?? 0);
        $monto = floatval($_POST['monto'] ?? 0);
        $descripcion = trim($_POST['descripcion'] ?? 'Recarga de saldo');

        if ($usuario_id <= 0 || $monto <= 0) {
            $mensaje = 'Datos inválidos';
            $tipo_msg = 'error';
        } else {
            // Verificar que el usuario existe
            $stmt = $db->prepare("SELECT id, nombre, email FROM usuarios WHERE id = ?");
            $stmt->execute([$usuario_id]);
            $usr = $stmt->fetch();

            if (!$usr) {
                $mensaje = 'Usuario no encontrado';
                $tipo_msg = 'error';
            } else {
                $db->beginTransaction();
                try {
                    // Crear cartera si no existe
                    $stmt = $db->prepare("INSERT IGNORE INTO carteras (usuario_id, saldo) VALUES (?, 0.00)");
                    $stmt->execute([$usuario_id]);

                    // Obtener saldo actual
                    $stmt = $db->prepare("SELECT saldo FROM carteras WHERE usuario_id = ? FOR UPDATE");
                    $stmt->execute([$usuario_id]);
                    $saldo_actual = $stmt->fetchColumn();

                    $saldo_nuevo = $saldo_actual + $monto;

                    // Actualizar saldo
                    $stmt = $db->prepare("UPDATE carteras SET saldo = ? WHERE usuario_id = ?");
                    $stmt->execute([$saldo_nuevo, $usuario_id]);

                    // Registrar transacción
                    $stmt = $db->prepare("INSERT INTO transacciones (usuario_id, tipo, monto, saldo_anterior, saldo_nuevo, descripcion, admin_id) VALUES (?, 'recarga', ?, ?, ?, ?, ?)");
                    $stmt->execute([$usuario_id, $monto, $saldo_actual, $saldo_nuevo, $descripcion, $usuario['id']]);

                    $db->commit();
                    $mensaje = "Recarga de $$monto aplicada a {$usr['nombre']} ({$usr['email']}). Nuevo saldo: $$saldo_nuevo";
                    $tipo_msg = 'success';
                } catch (Exception $e) {
                    $db->rollBack();
                    $mensaje = 'Error al procesar la recarga: ' . $e->getMessage();
                    $tipo_msg = 'error';
                }
            }
        }
    }

    if ($_POST['accion'] === 'reembolsar') {
        $usuario_id = intval($_POST['usuario_id'] ?? 0);
        $monto = floatval($_POST['monto'] ?? 0);
        $descripcion = trim($_POST['descripcion'] ?? 'Reembolso');

        if ($usuario_id <= 0 || $monto <= 0) {
            $mensaje = 'Datos inválidos';
            $tipo_msg = 'error';
        } else {
            $db->beginTransaction();
            try {
                $stmt = $db->prepare("SELECT saldo FROM carteras WHERE usuario_id = ? FOR UPDATE");
                $stmt->execute([$usuario_id]);
                $saldo_actual = $stmt->fetchColumn();

                if ($saldo_actual === false) {
                    throw new Exception('Usuario no tiene cartera');
                }

                $saldo_nuevo = $saldo_actual + $monto;

                $stmt = $db->prepare("UPDATE carteras SET saldo = ? WHERE usuario_id = ?");
                $stmt->execute([$saldo_nuevo, $usuario_id]);

                $stmt = $db->prepare("INSERT INTO transacciones (usuario_id, tipo, monto, saldo_anterior, saldo_nuevo, descripcion, admin_id) VALUES (?, 'reembolso', ?, ?, ?, ?, ?)");
                $stmt->execute([$usuario_id, $monto, $saldo_actual, $saldo_nuevo, $descripcion, $usuario['id']]);

                $db->commit();
                $mensaje = "Reembolso de $$monto aplicado. Nuevo saldo: $$saldo_nuevo";
                $tipo_msg = 'success';
            } catch (Exception $e) {
                $db->rollBack();
                $mensaje = 'Error: ' . $e->getMessage();
                $tipo_msg = 'error';
            }
        }
    }
}

// Obtener lista de usuarios con saldo
$usuarios = $db->query("SELECT u.id, u.nombre, u.email, u.rol, COALESCE(c.saldo, 0) as saldo FROM usuarios u LEFT JOIN carteras c ON u.id = c.usuario_id ORDER BY u.nombre")->fetchAll();

// Últimas transacciones globales
$ultimas_trans = $db->query("SELECT t.*, u.nombre as usuario_nombre, u.email as usuario_email, a.nombre as admin_nombre FROM transacciones t JOIN usuarios u ON t.usuario_id = u.id LEFT JOIN usuarios a ON t.admin_id = a.id ORDER BY t.fecha DESC LIMIT 20")->fetchAll();

$pagina_titulo = 'Admin - Recargas';
require_once 'includes/header.php';
?>

<section class="page-header">
    <div class="container">
        <h1><i class="fas fa-wallet"></i> Gestión de Carteras</h1>
        <p>Recarga saldo a los usuarios de la plataforma</p>
    </div>
</section>

<section class="section">
    <div class="container">
        <?php if ($mensaje): ?>
        <div class="flash-message flash-<?php echo $tipo_msg; ?>" style="margin-bottom: 1.5rem;">
            <i class="fas <?php echo $tipo_msg === 'success' ? 'fa-check-circle' : 'fa-exclamation-circle'; ?>"></i>
            <?php echo htmlspecialchars($mensaje); ?>
        </div>
        <?php endif; ?>

        <!-- Formulario de recarga -->
        <div class="admin-card">
            <h2><i class="fas fa-plus-circle"></i> Recargar Saldo</h2>
            <form method="POST" class="admin-form">
                <input type="hidden" name="accion" value="recargar">
                <div class="form-grid-2">
                    <div class="form-group">
                        <label for="usuario_id"><i class="fas fa-user"></i> Usuario</label>
                        <select name="usuario_id" id="usuario_id" class="form-select" required>
                            <option value="">Seleccionar usuario...</option>
                            <?php foreach ($usuarios as $u): ?>
                            <option value="<?php echo $u['id']; ?>">
                                <?php echo htmlspecialchars($u['nombre']); ?> (<?php echo htmlspecialchars($u['email']); ?>) - Saldo: $<?php echo number_format($u['saldo'], 2); ?>
                            </option>
                            <?php endforeach; ?>
                        </select>
                    </div>
                    <div class="form-group">
                        <label for="monto"><i class="fas fa-dollar-sign"></i> Monto</label>
                        <input type="number" name="monto" id="monto" step="0.01" min="0.01" placeholder="0.00" required>
                    </div>
                </div>
                <div class="form-group">
                    <label for="descripcion"><i class="fas fa-comment"></i> Descripción</label>
                    <input type="text" name="descripcion" id="descripcion" placeholder="Ej: Recarga por transferencia bancaria" value="Recarga de saldo">
                </div>
                <div class="form-actions">
                    <button type="submit" class="btn btn-primary"><i class="fas fa-plus"></i> Aplicar Recarga</button>
                </div>
            </form>
        </div>
    </div>
</section>

<!-- Saldos de usuarios -->
<section class="section">
    <div class="container">
        <h2 class="section-title"><i class="fas fa-users"></i> Saldos de Usuarios</h2>
        <div class="table-responsive">
            <table class="table">
                <thead>
                    <tr>
                        <th>ID</th>
                        <th>Nombre</th>
                        <th>Email</th>
                        <th>Saldo</th>
                        <th>Acciones</th>
                    </tr>
                </thead>
                <tbody>
                    <?php foreach ($usuarios as $u): ?>
                    <tr>
                        <td><?php echo $u['id']; ?></td>
                        <td><?php echo htmlspecialchars($u['nombre']); ?></td>
                        <td><?php echo htmlspecialchars($u['email']); ?></td>
                        <td class="<?php echo $u['saldo'] > 0 ? 'text-green' : ''; ?>">
                            $<?php echo number_format($u['saldo'], 2); ?>
                        </td>
                        <td>
                            <div class="action-btns">
                                <button class="btn btn-sm btn-primary" onclick="document.getElementById('usuario_id').value='<?php echo $u['id']; ?>'; document.getElementById('monto').focus(); window.scrollTo(0,0);">
                                    <i class="fas fa-plus"></i> Recargar
                                </button>
                            </div>
                        </td>
                    </tr>
                    <?php endforeach; ?>
                </tbody>
            </table>
        </div>
    </div>
</section>

<!-- Últimas transacciones -->
<section class="section">
    <div class="container">
        <h2 class="section-title"><i class="fas fa-history"></i> Últimas Transacciones</h2>
        <?php if (empty($ultimas_trans)): ?>
        <div class="empty-state">
            <i class="fas fa-receipt"></i>
            <h2>Sin transacciones</h2>
            <p>Aún no hay movimientos registrados</p>
        </div>
        <?php else: ?>
        <div class="table-responsive">
            <table class="table">
                <thead>
                    <tr>
                        <th>Fecha</th>
                        <th>Usuario</th>
                        <th>Tipo</th>
                        <th>Monto</th>
                        <th>Saldo</th>
                        <th>Descripción</th>
                        <th>Admin</th>
                    </tr>
                </thead>
                <tbody>
                    <?php foreach ($ultimas_trans as $t): ?>
                    <tr>
                        <td><?php echo date('d/m/Y H:i', strtotime($t['fecha'])); ?></td>
                        <td><?php echo htmlspecialchars($t['usuario_nombre']); ?></td>
                        <td>
                            <?php if ($t['tipo'] === 'recarga'): ?>
                                <span class="badge badge-completado"><i class="fas fa-arrow-up"></i> Recarga</span>
                            <?php elseif ($t['tipo'] === 'compra'): ?>
                                <span class="badge badge-procesando"><i class="fas fa-arrow-down"></i> Compra</span>
                            <?php else: ?>
                                <span class="badge badge-pendiente"><i class="fas fa-undo"></i> Reembolso</span>
                            <?php endif; ?>
                        </td>
                        <td class="<?php echo $t['tipo'] === 'compra' ? 'text-red' : 'text-green'; ?>">
                            <?php echo $t['tipo'] === 'compra' ? '-' : '+'; ?>$<?php echo number_format($t['monto'], 2); ?>
                        </td>
                        <td>$<?php echo number_format($t['saldo_nuevo'], 2); ?></td>
                        <td><?php echo htmlspecialchars($t['descripcion'] ?? '-'); ?></td>
                        <td><?php echo htmlspecialchars($t['admin_nombre'] ?? '-'); ?></td>
                    </tr>
                    <?php endforeach; ?>
                </tbody>
            </table>
        </div>
        <?php endif; ?>
    </div>
</section>

<?php require_once 'includes/footer.php'; ?>
