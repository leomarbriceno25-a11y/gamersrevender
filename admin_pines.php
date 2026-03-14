<?php
require_once 'config.php';
require_once 'auth.php';
requiereLogin();

// Solo admin puede acceder
if (!esAdmin()) {
    setFlash('No tienes permisos para acceder aquí', 'error');
    header('Location: dashboard.php');
    exit;
}

$db = conectarDB();
$error = '';
$exito = '';

// Procesar formulario de agregar PINs
if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['accion'])) {
    
    if ($_POST['accion'] === 'agregar_pines') {
        $producto_id = intval($_POST['producto_id'] ?? 0);
        $pines_texto = trim($_POST['pines'] ?? '');
        
        if (empty($producto_id) || empty($pines_texto)) {
            $error = 'Selecciona un producto e ingresa al menos un PIN';
        } else {
            // Separar PINs por líneas
            $pines = array_filter(array_map('trim', explode("\n", $pines_texto)));
            $agregados = 0;
            $duplicados = 0;
            
            foreach ($pines as $pin) {
                if (empty($pin)) continue;
                
                // Verificar si ya existe
                $stmt = $db->prepare("SELECT id FROM pines WHERE pin = ?");
                $stmt->execute([$pin]);
                if ($stmt->fetch()) {
                    $duplicados++;
                    continue;
                }
                
                $stmt = $db->prepare("INSERT INTO pines (producto_id, pin) VALUES (?, ?)");
                $stmt->execute([$producto_id, $pin]);
                $agregados++;
            }
            
            $exito = "$agregados PINs agregados exitosamente";
            if ($duplicados > 0) {
                $exito .= " ($duplicados duplicados omitidos)";
            }
        }
    }
    
    if ($_POST['accion'] === 'eliminar_pin') {
        $pin_id = intval($_POST['pin_id'] ?? 0);
        $stmt = $db->prepare("DELETE FROM pines WHERE id = ? AND estado = 'disponible'");
        $stmt->execute([$pin_id]);
        $exito = 'PIN eliminado';
    }
}

// Obtener productos para el dropdown
$productos = $db->query("SELECT id, nombre, categoria FROM productos WHERE activo = 1 ORDER BY categoria, nombre")->fetchAll();

// Obtener stock de PINs por producto
$stock = $db->query("SELECT p.nombre, p.id as producto_id, 
    COUNT(CASE WHEN pi.estado = 'disponible' THEN 1 END) as disponibles,
    COUNT(CASE WHEN pi.estado = 'usado' THEN 1 END) as usados,
    COUNT(CASE WHEN pi.estado = 'error' THEN 1 END) as errores,
    COUNT(pi.id) as total
    FROM productos p LEFT JOIN pines pi ON p.id = pi.producto_id
    WHERE p.activo = 1
    GROUP BY p.id ORDER BY p.categoria, p.nombre")->fetchAll();

// Obtener PINs recientes
$pines_recientes = $db->query("SELECT pi.*, p.nombre as producto_nombre, u.nombre as usuario_nombre 
    FROM pines pi 
    JOIN productos p ON pi.producto_id = p.id 
    LEFT JOIN usuarios u ON pi.usado_por = u.id 
    ORDER BY pi.id DESC LIMIT 30")->fetchAll();

$pagina_titulo = 'Admin - Stock de PINs';
require_once 'includes/header.php';
?>

<section class="page-header">
    <div class="container">
        <h1><i class="fas fa-key"></i> Stock de <span class="text-gradient">PINs</span></h1>
        <p>Administra tu inventario de PINs para canje automático</p>
    </div>
</section>

<section class="section">
    <div class="container">
        <?php if ($error): ?>
        <div class="alert alert-error"><i class="fas fa-exclamation-circle"></i> <?php echo htmlspecialchars($error); ?></div>
        <?php endif; ?>
        <?php if ($exito): ?>
        <div class="alert alert-success"><i class="fas fa-check-circle"></i> <?php echo htmlspecialchars($exito); ?></div>
        <?php endif; ?>

        <!-- Stock Resumen -->
        <h2 class="section-title"><i class="fas fa-chart-bar"></i> Resumen de Stock</h2>
        <div class="table-responsive" style="margin-bottom: 2rem;">
            <table class="table">
                <thead>
                    <tr>
                        <th>Producto</th>
                        <th>Disponibles</th>
                        <th>Usados</th>
                        <th>Errores</th>
                        <th>Total</th>
                    </tr>
                </thead>
                <tbody>
                    <?php foreach ($stock as $s): ?>
                    <tr>
                        <td><?php echo htmlspecialchars($s['nombre']); ?></td>
                        <td><span class="badge badge-completado"><?php echo $s['disponibles']; ?></span></td>
                        <td><span class="badge badge-procesando"><?php echo $s['usados']; ?></span></td>
                        <td><span class="badge badge-cancelado"><?php echo $s['errores']; ?></span></td>
                        <td><?php echo $s['total']; ?></td>
                    </tr>
                    <?php endforeach; ?>
                </tbody>
            </table>
        </div>

        <!-- Formulario Agregar PINs -->
        <div class="admin-form-card">
            <h2 class="section-title"><i class="fas fa-plus-circle"></i> Agregar PINs</h2>
            <form method="POST">
                <input type="hidden" name="accion" value="agregar_pines">
                <div class="form-group">
                    <label for="producto_id"><i class="fas fa-box"></i> Producto</label>
                    <select id="producto_id" name="producto_id" class="form-select" required>
                        <option value="">Selecciona el producto...</option>
                        <?php foreach ($productos as $p): ?>
                        <option value="<?php echo $p['id']; ?>">
                            [<?php echo strtoupper($p['categoria']); ?>] <?php echo htmlspecialchars($p['nombre']); ?>
                        </option>
                        <?php endforeach; ?>
                    </select>
                </div>
                <div class="form-group">
                    <label for="pines"><i class="fas fa-key"></i> PINs (uno por línea)</label>
                    <textarea id="pines" name="pines" class="form-textarea" rows="6" 
                              placeholder="Pega los PINs aquí, uno por línea:&#10;XXXX-XXXX-XXXX-XXXX&#10;YYYY-YYYY-YYYY-YYYY&#10;ZZZZ-ZZZZ-ZZZZ-ZZZZ" required></textarea>
                </div>
                <button type="submit" class="btn btn-primary">
                    <i class="fas fa-upload"></i> Agregar PINs al Stock
                </button>
            </form>
        </div>

        <!-- Lista de PINs recientes -->
        <h2 class="section-title" style="margin-top: 2rem;"><i class="fas fa-list"></i> PINs Recientes</h2>
        <div class="table-responsive">
            <table class="table">
                <thead>
                    <tr>
                        <th>#</th>
                        <th>Producto</th>
                        <th>PIN</th>
                        <th>Estado</th>
                        <th>Usado por</th>
                        <th>Fecha</th>
                        <th>Acciones</th>
                    </tr>
                </thead>
                <tbody>
                    <?php foreach ($pines_recientes as $pin): ?>
                    <tr>
                        <td><?php echo $pin['id']; ?></td>
                        <td><?php echo htmlspecialchars($pin['producto_nombre']); ?></td>
                        <td><code><?php echo htmlspecialchars(substr($pin['pin'], 0, 12)) . '...'; ?></code></td>
                        <td>
                            <span class="badge badge-<?php echo $pin['estado'] === 'disponible' ? 'completado' : ($pin['estado'] === 'usado' ? 'procesando' : 'cancelado'); ?>">
                                <?php echo ucfirst($pin['estado']); ?>
                            </span>
                        </td>
                        <td><?php echo htmlspecialchars($pin['usuario_nombre'] ?? '-'); ?></td>
                        <td><?php echo date('d/m/Y H:i', strtotime($pin['fecha_agregado'])); ?></td>
                        <td>
                            <?php if ($pin['estado'] === 'disponible'): ?>
                            <form method="POST" style="display: inline;" onsubmit="return confirm('¿Eliminar este PIN?')">
                                <input type="hidden" name="accion" value="eliminar_pin">
                                <input type="hidden" name="pin_id" value="<?php echo $pin['id']; ?>">
                                <button type="submit" class="btn btn-sm btn-outline" style="color: var(--error); border-color: var(--error);">
                                    <i class="fas fa-trash"></i>
                                </button>
                            </form>
                            <?php else: ?>
                            -
                            <?php endif; ?>
                        </td>
                    </tr>
                    <?php endforeach; ?>
                </tbody>
            </table>
        </div>
    </div>
</section>

<?php require_once 'includes/footer.php'; ?>
