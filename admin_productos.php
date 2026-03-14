<?php
require_once 'config.php';
require_once 'auth.php';
requiereLogin();

if (!esAdmin()) {
    setFlash('No tienes permisos para acceder aquí', 'error');
    header('Location: dashboard.php');
    exit;
}

$db = conectarDB();
$error = '';
$exito = '';
$modo = $_GET['modo'] ?? 'lista';
$id = intval($_GET['id'] ?? 0);
$cat_filtro = $_GET['cat'] ?? '';

// ===== PROCESAR ACCIONES POST =====
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $accion = $_POST['accion'] ?? '';

    // --- AGREGAR PRODUCTO ---
    if ($accion === 'agregar_producto') {
        $nombre = trim($_POST['nombre'] ?? '');
        $descripcion = trim($_POST['descripcion'] ?? '');
        $precio = floatval($_POST['precio'] ?? 0);
        $categoria = trim($_POST['categoria'] ?? '');
        $icono = trim($_POST['icono'] ?? 'fa-gem');
        
        if (empty($nombre) || $precio <= 0 || empty($categoria)) {
            $error = 'Nombre, precio y categoría son obligatorios';
        } else {
            $stmt = $db->prepare("INSERT INTO productos (nombre, descripcion, precio, categoria, icono) VALUES (?, ?, ?, ?, ?)");
            $stmt->execute([$nombre, $descripcion, $precio, $categoria, $icono]);
            $exito = "Producto '$nombre' creado exitosamente";
        }
    }

    // --- EDITAR PRODUCTO ---
    if ($accion === 'editar_producto') {
        $prod_id = intval($_POST['producto_id'] ?? 0);
        $nombre = trim($_POST['nombre'] ?? '');
        $descripcion = trim($_POST['descripcion'] ?? '');
        $precio = floatval($_POST['precio'] ?? 0);
        $categoria = trim($_POST['categoria'] ?? '');
        $icono = trim($_POST['icono'] ?? 'fa-gem');
        $activo = isset($_POST['activo']) ? 1 : 0;
        
        if (empty($nombre) || $precio <= 0) {
            $error = 'Nombre y precio son obligatorios';
        } else {
            $stmt = $db->prepare("UPDATE productos SET nombre = ?, descripcion = ?, precio = ?, categoria = ?, icono = ?, activo = ? WHERE id = ?");
            $stmt->execute([$nombre, $descripcion, $precio, $categoria, $icono, $activo, $prod_id]);
            $exito = "Producto actualizado exitosamente";
        }
    }

    // --- ELIMINAR PRODUCTO ---
    if ($accion === 'eliminar_producto') {
        $prod_id = intval($_POST['producto_id'] ?? 0);
        // Verificar si tiene pedidos
        $stmt = $db->prepare("SELECT COUNT(*) as c FROM pedidos WHERE producto_id = ?");
        $stmt->execute([$prod_id]);
        $tiene_pedidos = $stmt->fetch()['c'] > 0;
        
        if ($tiene_pedidos) {
            // Solo desactivar si tiene pedidos
            $stmt = $db->prepare("UPDATE productos SET activo = 0 WHERE id = ?");
            $stmt->execute([$prod_id]);
            $exito = "Producto desactivado (tiene pedidos asociados)";
        } else {
            // Eliminar servicios y PINs primero
            $db->prepare("DELETE FROM servicios WHERE producto_id = ?")->execute([$prod_id]);
            $db->prepare("DELETE FROM pines WHERE producto_id = ?")->execute([$prod_id]);
            $db->prepare("DELETE FROM productos WHERE id = ?")->execute([$prod_id]);
            $exito = "Producto eliminado exitosamente";
        }
    }

    // --- AGREGAR CATEGORÍA ---
    if ($accion === 'agregar_categoria') {
        $cat_nombre = trim($_POST['cat_nombre'] ?? '');
        $cat_slug = trim($_POST['cat_slug'] ?? '');
        $cat_icono = trim($_POST['cat_icono'] ?? 'fa-folder');
        $cat_desc = trim($_POST['cat_descripcion'] ?? '');
        
        if (empty($cat_nombre) || empty($cat_slug)) {
            $error = 'Nombre y slug de la categoría son obligatorios';
        } else {
            $stmt = $db->prepare("INSERT INTO categorias (nombre, slug, icono, descripcion) VALUES (?, ?, ?, ?)");
            $stmt->execute([$cat_nombre, $cat_slug, $cat_icono, $cat_desc]);
            
            // Agregar al ENUM de productos
            $slugs = $db->query("SELECT slug FROM categorias")->fetchAll(PDO::FETCH_COLUMN);
            $enum = implode("','", $slugs);
            $db->exec("ALTER TABLE productos MODIFY COLUMN categoria ENUM('$enum') NOT NULL");
            
            $exito = "Categoría '$cat_nombre' creada exitosamente";
        }
    }

    // --- EDITAR CATEGORÍA ---
    if ($accion === 'editar_categoria') {
        $cat_id = intval($_POST['cat_id'] ?? 0);
        $cat_nombre = trim($_POST['cat_nombre'] ?? '');
        $cat_icono = trim($_POST['cat_icono'] ?? 'fa-folder');
        $cat_desc = trim($_POST['cat_descripcion'] ?? '');
        $cat_activo = isset($_POST['cat_activo']) ? 1 : 0;
        
        if (empty($cat_nombre)) {
            $error = 'El nombre de la categoría es obligatorio';
        } else {
            $stmt = $db->prepare("UPDATE categorias SET nombre = ?, icono = ?, descripcion = ?, activo = ? WHERE id = ?");
            $stmt->execute([$cat_nombre, $cat_icono, $cat_desc, $cat_activo, $cat_id]);
            $exito = "Categoría actualizada";
        }
    }

    // --- ELIMINAR CATEGORÍA ---
    if ($accion === 'eliminar_categoria') {
        $cat_id = intval($_POST['cat_id'] ?? 0);
        $stmt = $db->prepare("SELECT slug FROM categorias WHERE id = ?");
        $stmt->execute([$cat_id]);
        $cat = $stmt->fetch();
        
        if ($cat) {
            $stmt = $db->prepare("SELECT COUNT(*) as c FROM productos WHERE categoria = ?");
            $stmt->execute([$cat['slug']]);
            if ($stmt->fetch()['c'] > 0) {
                $error = "No puedes eliminar esta categoría, tiene productos asociados";
            } else {
                $db->prepare("DELETE FROM categorias WHERE id = ?")->execute([$cat_id]);
                $exito = "Categoría eliminada";
            }
        }
    }

    // --- AGREGAR SERVICIO ---
    if ($accion === 'agregar_servicio') {
        $serv_producto_id = intval($_POST['producto_id'] ?? 0);
        $serv_nombre = trim($_POST['serv_nombre'] ?? '');
        $serv_descripcion = trim($_POST['serv_descripcion'] ?? '');
        $serv_precio = floatval($_POST['serv_precio'] ?? 0);
        $serv_orden = intval($_POST['serv_orden'] ?? 0);
        
        if (empty($serv_nombre) || $serv_precio <= 0 || $serv_producto_id <= 0) {
            $error = 'Nombre, precio y producto son obligatorios';
        } else {
            $stmt = $db->prepare("INSERT INTO servicios (producto_id, nombre, descripcion, precio, orden) VALUES (?, ?, ?, ?, ?)");
            $stmt->execute([$serv_producto_id, $serv_nombre, $serv_descripcion, $serv_precio, $serv_orden]);
            $exito = "Servicio '$serv_nombre' agregado";
            $modo = 'servicios';
            $id = $serv_producto_id;
        }
    }

    // --- EDITAR SERVICIO ---
    if ($accion === 'editar_servicio') {
        $serv_id = intval($_POST['servicio_id'] ?? 0);
        $serv_producto_id = intval($_POST['producto_id'] ?? 0);
        $serv_nombre = trim($_POST['serv_nombre'] ?? '');
        $serv_descripcion = trim($_POST['serv_descripcion'] ?? '');
        $serv_precio = floatval($_POST['serv_precio'] ?? 0);
        $serv_orden = intval($_POST['serv_orden'] ?? 0);
        $serv_activo = isset($_POST['serv_activo']) ? 1 : 0;
        
        if (empty($serv_nombre) || $serv_precio <= 0) {
            $error = 'Nombre y precio son obligatorios';
        } else {
            $stmt = $db->prepare("UPDATE servicios SET nombre = ?, descripcion = ?, precio = ?, orden = ?, activo = ? WHERE id = ?");
            $stmt->execute([$serv_nombre, $serv_descripcion, $serv_precio, $serv_orden, $serv_activo, $serv_id]);
            $exito = "Servicio actualizado";
            $modo = 'servicios';
            $id = $serv_producto_id;
        }
    }

    // --- ELIMINAR SERVICIO ---
    if ($accion === 'eliminar_servicio') {
        $serv_id = intval($_POST['servicio_id'] ?? 0);
        $serv_producto_id = intval($_POST['producto_id'] ?? 0);
        $db->prepare("DELETE FROM servicios WHERE id = ?")->execute([$serv_id]);
        $exito = "Servicio eliminado";
        $modo = 'servicios';
        $id = $serv_producto_id;
    }
}

// ===== OBTENER DATOS =====
$categorias = $db->query("SELECT * FROM categorias ORDER BY orden, nombre")->fetchAll();

// Filtrar productos por categoría si se seleccionó
$sql = "SELECT p.*, c.nombre as categoria_nombre, (SELECT COUNT(*) FROM servicios s WHERE s.producto_id = p.id) as total_servicios FROM productos p LEFT JOIN categorias c ON p.categoria = c.slug";
if (!empty($cat_filtro)) {
    $stmt = $db->prepare($sql . " WHERE p.categoria = ? ORDER BY p.categoria, p.nombre");
    $stmt->execute([$cat_filtro]);
} else {
    $stmt = $db->query($sql . " ORDER BY p.categoria, p.nombre");
}
$productos = $stmt->fetchAll();

// Si modo editar producto, obtener datos
$producto_editar = null;
if ($modo === 'editar' && $id > 0) {
    $stmt = $db->prepare("SELECT * FROM productos WHERE id = ?");
    $stmt->execute([$id]);
    $producto_editar = $stmt->fetch();
}

// Si modo servicios, obtener producto y sus servicios
$producto_servicios = null;
$servicios = [];
if ($modo === 'servicios' && $id > 0) {
    $stmt = $db->prepare("SELECT * FROM productos WHERE id = ?");
    $stmt->execute([$id]);
    $producto_servicios = $stmt->fetch();
    
    $stmt = $db->prepare("SELECT * FROM servicios WHERE producto_id = ? ORDER BY orden, nombre");
    $stmt->execute([$id]);
    $servicios = $stmt->fetchAll();
}

// Si modo editar servicio
$servicio_editar = null;
$serv_edit_id = intval($_GET['serv_id'] ?? 0);
if ($modo === 'editar_servicio' && $serv_edit_id > 0) {
    $stmt = $db->prepare("SELECT s.*, p.nombre as producto_nombre FROM servicios s JOIN productos p ON s.producto_id = p.id WHERE s.id = ?");
    $stmt->execute([$serv_edit_id]);
    $servicio_editar = $stmt->fetch();
    if ($servicio_editar) {
        $id = $servicio_editar['producto_id'];
    }
}

// Si modo editar categoría
$categoria_editar = null;
if ($modo === 'editar_cat' && $id > 0) {
    $stmt = $db->prepare("SELECT * FROM categorias WHERE id = ?");
    $stmt->execute([$id]);
    $categoria_editar = $stmt->fetch();
}

$pagina_titulo = 'Admin - Productos';
require_once 'includes/header.php';
?>

<section class="page-header">
    <div class="container">
        <h1><i class="fas fa-boxes-stacked"></i> Administrar <span class="text-gradient">Productos</span></h1>
        <p>Gestiona productos, categorías y servicios</p>
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

        <!-- TABS DE NAVEGACIÓN -->
        <div class="admin-tabs">
            <a href="admin_productos.php" class="admin-tab <?php echo ($modo === 'lista' && empty($cat_filtro)) ? 'active' : ''; ?>">
                <i class="fas fa-list"></i> Todos los Productos
            </a>
            <a href="admin_productos.php?modo=nuevo" class="admin-tab <?php echo $modo === 'nuevo' ? 'active' : ''; ?>">
                <i class="fas fa-plus"></i> Nuevo Producto
            </a>
            <a href="admin_productos.php?modo=categorias" class="admin-tab <?php echo ($modo === 'categorias' || $modo === 'editar_cat' || $modo === 'nueva_cat') ? 'active' : ''; ?>">
                <i class="fas fa-tags"></i> Categorías
            </a>
        </div>

        <!-- ===== FILTROS POR CATEGORÍA ===== -->
        <?php if ($modo === 'lista'): ?>
        <div class="admin-filters">
            <a href="admin_productos.php" class="filter-btn <?php echo empty($cat_filtro) ? 'active' : ''; ?>">Todos</a>
            <?php foreach ($categorias as $cat): ?>
            <a href="admin_productos.php?cat=<?php echo $cat['slug']; ?>" class="filter-btn <?php echo $cat_filtro === $cat['slug'] ? 'active' : ''; ?>">
                <i class="fas <?php echo $cat['icono']; ?>"></i> <?php echo htmlspecialchars($cat['nombre']); ?>
            </a>
            <?php endforeach; ?>
        </div>

        <!-- LISTA DE PRODUCTOS -->
        <div class="table-responsive">
            <table class="table">
                <thead>
                    <tr>
                        <th>ID</th>
                        <th>Producto</th>
                        <th>Categoría</th>
                        <th>Precio</th>
                        <th>Servicios</th>
                        <th>Estado</th>
                        <th>Acciones</th>
                    </tr>
                </thead>
                <tbody>
                    <?php if (empty($productos)): ?>
                    <tr><td colspan="7" style="text-align:center; padding:2rem; color:var(--text-secondary);">No hay productos</td></tr>
                    <?php endif; ?>
                    <?php foreach ($productos as $p): ?>
                    <tr>
                        <td><?php echo $p['id']; ?></td>
                        <td>
                            <strong><i class="fas <?php echo $p['icono']; ?>" style="color:var(--accent-primary);margin-right:0.3rem;"></i><?php echo htmlspecialchars($p['nombre']); ?></strong>
                            <br><small style="color:var(--text-secondary);"><?php echo htmlspecialchars(mb_substr($p['descripcion'] ?? '', 0, 50)); ?></small>
                        </td>
                        <td><?php echo htmlspecialchars($p['categoria_nombre'] ?? $p['categoria']); ?></td>
                        <td><strong>$<?php echo number_format($p['precio'], 2); ?></strong></td>
                        <td>
                            <a href="admin_productos.php?modo=servicios&id=<?php echo $p['id']; ?>" class="badge badge-info">
                                <?php echo $p['total_servicios']; ?> servicios
                            </a>
                        </td>
                        <td>
                            <span class="badge <?php echo $p['activo'] ? 'badge-completado' : 'badge-cancelado'; ?>">
                                <?php echo $p['activo'] ? 'Activo' : 'Inactivo'; ?>
                            </span>
                        </td>
                        <td>
                            <div class="action-btns">
                                <a href="admin_productos.php?modo=editar&id=<?php echo $p['id']; ?>" class="btn btn-sm btn-secondary" title="Editar">
                                    <i class="fas fa-edit"></i>
                                </a>
                                <a href="admin_productos.php?modo=servicios&id=<?php echo $p['id']; ?>" class="btn btn-sm btn-outline" title="Servicios">
                                    <i class="fas fa-cogs"></i>
                                </a>
                                <form method="POST" style="display:inline;" onsubmit="return confirm('¿Eliminar este producto?')">
                                    <input type="hidden" name="accion" value="eliminar_producto">
                                    <input type="hidden" name="producto_id" value="<?php echo $p['id']; ?>">
                                    <button type="submit" class="btn btn-sm btn-outline" style="color:var(--error);border-color:var(--error);" title="Eliminar">
                                        <i class="fas fa-trash"></i>
                                    </button>
                                </form>
                            </div>
                        </td>
                    </tr>
                    <?php endforeach; ?>
                </tbody>
            </table>
        </div>
        <?php endif; ?>

        <!-- ===== FORMULARIO NUEVO PRODUCTO ===== -->
        <?php if ($modo === 'nuevo'): ?>
        <div class="admin-form-card">
            <h2 class="section-title"><i class="fas fa-plus-circle"></i> Nuevo Producto</h2>
            <form method="POST">
                <input type="hidden" name="accion" value="agregar_producto">
                <div class="form-grid-2">
                    <div class="form-group">
                        <label><i class="fas fa-box"></i> Nombre del producto *</label>
                        <input type="text" name="nombre" placeholder="Ej: 100 Diamantes Free Fire" required>
                    </div>
                    <div class="form-group">
                        <label><i class="fas fa-tag"></i> Categoría *</label>
                        <select name="categoria" class="form-select" required>
                            <option value="">Selecciona...</option>
                            <?php foreach ($categorias as $cat): ?>
                            <option value="<?php echo $cat['slug']; ?>"><?php echo htmlspecialchars($cat['nombre']); ?></option>
                            <?php endforeach; ?>
                        </select>
                    </div>
                </div>
                <div class="form-group">
                    <label><i class="fas fa-align-left"></i> Descripción</label>
                    <textarea name="descripcion" class="form-textarea" rows="3" placeholder="Descripción del producto..."></textarea>
                </div>
                <div class="form-grid-2">
                    <div class="form-group">
                        <label><i class="fas fa-dollar-sign"></i> Precio (USD) *</label>
                        <input type="number" name="precio" step="0.01" min="0.01" placeholder="0.00" required>
                    </div>
                    <div class="form-group">
                        <label><i class="fas fa-icons"></i> Icono (FontAwesome)</label>
                        <select name="icono" class="form-select">
                            <option value="fa-gem">fa-gem (Diamante)</option>
                            <option value="fa-fire">fa-fire (Fuego)</option>
                            <option value="fa-gift">fa-gift (Regalo)</option>
                            <option value="fa-gamepad">fa-gamepad (Control)</option>
                            <option value="fa-star">fa-star (Estrella)</option>
                            <option value="fa-crown">fa-crown (Corona)</option>
                            <option value="fa-bolt">fa-bolt (Rayo)</option>
                            <option value="fa-shield">fa-shield (Escudo)</option>
                            <option value="fa-coins">fa-coins (Monedas)</option>
                            <option value="fa-credit-card">fa-credit-card (Tarjeta)</option>
                        </select>
                    </div>
                </div>
                <div class="form-actions">
                    <button type="submit" class="btn btn-primary"><i class="fas fa-save"></i> Crear Producto</button>
                    <a href="admin_productos.php" class="btn btn-secondary">Cancelar</a>
                </div>
            </form>
        </div>
        <?php endif; ?>

        <!-- ===== FORMULARIO EDITAR PRODUCTO ===== -->
        <?php if ($modo === 'editar' && $producto_editar): ?>
        <div class="admin-form-card">
            <h2 class="section-title"><i class="fas fa-edit"></i> Editar Producto #<?php echo $producto_editar['id']; ?></h2>
            <form method="POST">
                <input type="hidden" name="accion" value="editar_producto">
                <input type="hidden" name="producto_id" value="<?php echo $producto_editar['id']; ?>">
                <div class="form-grid-2">
                    <div class="form-group">
                        <label><i class="fas fa-box"></i> Nombre *</label>
                        <input type="text" name="nombre" value="<?php echo htmlspecialchars($producto_editar['nombre']); ?>" required>
                    </div>
                    <div class="form-group">
                        <label><i class="fas fa-tag"></i> Categoría *</label>
                        <select name="categoria" class="form-select" required>
                            <?php foreach ($categorias as $cat): ?>
                            <option value="<?php echo $cat['slug']; ?>" <?php echo $producto_editar['categoria'] === $cat['slug'] ? 'selected' : ''; ?>>
                                <?php echo htmlspecialchars($cat['nombre']); ?>
                            </option>
                            <?php endforeach; ?>
                        </select>
                    </div>
                </div>
                <div class="form-group">
                    <label><i class="fas fa-align-left"></i> Descripción</label>
                    <textarea name="descripcion" class="form-textarea" rows="3"><?php echo htmlspecialchars($producto_editar['descripcion'] ?? ''); ?></textarea>
                </div>
                <div class="form-grid-2">
                    <div class="form-group">
                        <label><i class="fas fa-dollar-sign"></i> Precio (USD) *</label>
                        <input type="number" name="precio" step="0.01" min="0.01" value="<?php echo $producto_editar['precio']; ?>" required>
                    </div>
                    <div class="form-group">
                        <label><i class="fas fa-icons"></i> Icono</label>
                        <select name="icono" class="form-select">
                            <?php
                            $iconos = ['fa-gem'=>'Diamante','fa-fire'=>'Fuego','fa-gift'=>'Regalo','fa-gamepad'=>'Control','fa-star'=>'Estrella','fa-crown'=>'Corona','fa-bolt'=>'Rayo','fa-shield'=>'Escudo','fa-coins'=>'Monedas','fa-credit-card'=>'Tarjeta'];
                            foreach ($iconos as $val => $label):
                            ?>
                            <option value="<?php echo $val; ?>" <?php echo $producto_editar['icono'] === $val ? 'selected' : ''; ?>>
                                <?php echo "$val ($label)"; ?>
                            </option>
                            <?php endforeach; ?>
                        </select>
                    </div>
                </div>
                <div class="form-group">
                    <label class="checkbox-label">
                        <input type="checkbox" name="activo" <?php echo $producto_editar['activo'] ? 'checked' : ''; ?>>
                        Producto activo (visible para clientes)
                    </label>
                </div>
                <div class="form-actions">
                    <button type="submit" class="btn btn-primary"><i class="fas fa-save"></i> Guardar Cambios</button>
                    <a href="admin_productos.php" class="btn btn-secondary">Cancelar</a>
                    <a href="admin_productos.php?modo=servicios&id=<?php echo $producto_editar['id']; ?>" class="btn btn-outline">
                        <i class="fas fa-cogs"></i> Ver Servicios
                    </a>
                </div>
            </form>
        </div>
        <?php endif; ?>

        <!-- ===== GESTIÓN DE CATEGORÍAS ===== -->
        <?php if ($modo === 'categorias' || $modo === 'nueva_cat'): ?>
        <div class="admin-form-card" style="margin-bottom: 2rem;">
            <h2 class="section-title"><i class="fas fa-plus-circle"></i> Nueva Categoría</h2>
            <form method="POST">
                <input type="hidden" name="accion" value="agregar_categoria">
                <div class="form-grid-2">
                    <div class="form-group">
                        <label>Nombre *</label>
                        <input type="text" name="cat_nombre" placeholder="Ej: Mobile Legends" required>
                    </div>
                    <div class="form-group">
                        <label>Slug (URL) *</label>
                        <input type="text" name="cat_slug" placeholder="Ej: mobile-legends" required>
                    </div>
                </div>
                <div class="form-grid-2">
                    <div class="form-group">
                        <label>Icono (FontAwesome)</label>
                        <input type="text" name="cat_icono" placeholder="fa-gamepad" value="fa-folder">
                    </div>
                    <div class="form-group">
                        <label>Descripción</label>
                        <input type="text" name="cat_descripcion" placeholder="Descripción breve...">
                    </div>
                </div>
                <button type="submit" class="btn btn-primary"><i class="fas fa-save"></i> Crear Categoría</button>
            </form>
        </div>

        <h2 class="section-title"><i class="fas fa-tags"></i> Categorías Existentes</h2>
        <div class="table-responsive">
            <table class="table">
                <thead>
                    <tr>
                        <th>ID</th>
                        <th>Nombre</th>
                        <th>Slug</th>
                        <th>Icono</th>
                        <th>Estado</th>
                        <th>Acciones</th>
                    </tr>
                </thead>
                <tbody>
                    <?php foreach ($categorias as $cat): ?>
                    <tr>
                        <td><?php echo $cat['id']; ?></td>
                        <td><strong><i class="fas <?php echo $cat['icono']; ?>" style="color:var(--accent-primary);margin-right:0.3rem;"></i><?php echo htmlspecialchars($cat['nombre']); ?></strong></td>
                        <td><code><?php echo htmlspecialchars($cat['slug']); ?></code></td>
                        <td><code><?php echo htmlspecialchars($cat['icono']); ?></code></td>
                        <td>
                            <span class="badge <?php echo $cat['activo'] ? 'badge-completado' : 'badge-cancelado'; ?>">
                                <?php echo $cat['activo'] ? 'Activo' : 'Inactivo'; ?>
                            </span>
                        </td>
                        <td>
                            <div class="action-btns">
                                <a href="admin_productos.php?modo=editar_cat&id=<?php echo $cat['id']; ?>" class="btn btn-sm btn-secondary"><i class="fas fa-edit"></i></a>
                                <form method="POST" style="display:inline;" onsubmit="return confirm('¿Eliminar esta categoría?')">
                                    <input type="hidden" name="accion" value="eliminar_categoria">
                                    <input type="hidden" name="cat_id" value="<?php echo $cat['id']; ?>">
                                    <button type="submit" class="btn btn-sm btn-outline" style="color:var(--error);border-color:var(--error);"><i class="fas fa-trash"></i></button>
                                </form>
                            </div>
                        </td>
                    </tr>
                    <?php endforeach; ?>
                </tbody>
            </table>
        </div>
        <?php endif; ?>

        <!-- ===== EDITAR CATEGORÍA ===== -->
        <?php if ($modo === 'editar_cat' && $categoria_editar): ?>
        <div class="admin-form-card">
            <h2 class="section-title"><i class="fas fa-edit"></i> Editar Categoría: <?php echo htmlspecialchars($categoria_editar['nombre']); ?></h2>
            <form method="POST">
                <input type="hidden" name="accion" value="editar_categoria">
                <input type="hidden" name="cat_id" value="<?php echo $categoria_editar['id']; ?>">
                <div class="form-grid-2">
                    <div class="form-group">
                        <label>Nombre *</label>
                        <input type="text" name="cat_nombre" value="<?php echo htmlspecialchars($categoria_editar['nombre']); ?>" required>
                    </div>
                    <div class="form-group">
                        <label>Icono (FontAwesome)</label>
                        <input type="text" name="cat_icono" value="<?php echo htmlspecialchars($categoria_editar['icono']); ?>">
                    </div>
                </div>
                <div class="form-group">
                    <label>Descripción</label>
                    <textarea name="cat_descripcion" class="form-textarea" rows="2"><?php echo htmlspecialchars($categoria_editar['descripcion'] ?? ''); ?></textarea>
                </div>
                <div class="form-group">
                    <label class="checkbox-label">
                        <input type="checkbox" name="cat_activo" <?php echo $categoria_editar['activo'] ? 'checked' : ''; ?>>
                        Categoría activa
                    </label>
                </div>
                <div class="form-actions">
                    <button type="submit" class="btn btn-primary"><i class="fas fa-save"></i> Guardar</button>
                    <a href="admin_productos.php?modo=categorias" class="btn btn-secondary">Cancelar</a>
                </div>
            </form>
        </div>
        <?php endif; ?>

        <!-- ===== GESTIÓN DE SERVICIOS ===== -->
        <?php if ($modo === 'servicios' && $producto_servicios): ?>
        <div class="admin-breadcrumb">
            <a href="admin_productos.php"><i class="fas fa-arrow-left"></i> Volver a Productos</a>
            &raquo; Servicios de: <strong><?php echo htmlspecialchars($producto_servicios['nombre']); ?></strong>
        </div>

        <!-- Formulario agregar servicio -->
        <div class="admin-form-card" style="margin-bottom: 2rem;">
            <h2 class="section-title"><i class="fas fa-plus-circle"></i> Agregar Servicio</h2>
            <form method="POST">
                <input type="hidden" name="accion" value="agregar_servicio">
                <input type="hidden" name="producto_id" value="<?php echo $producto_servicios['id']; ?>">
                <div class="form-grid-2">
                    <div class="form-group">
                        <label>Nombre del servicio *</label>
                        <input type="text" name="serv_nombre" placeholder="Ej: Entrega en 1 hora" required>
                    </div>
                    <div class="form-group">
                        <label>Precio (USD) *</label>
                        <input type="number" name="serv_precio" step="0.01" min="0.01" placeholder="0.00" required>
                    </div>
                </div>
                <div class="form-grid-2">
                    <div class="form-group">
                        <label>Descripción</label>
                        <input type="text" name="serv_descripcion" placeholder="Descripción breve del servicio...">
                    </div>
                    <div class="form-group">
                        <label>Orden</label>
                        <input type="number" name="serv_orden" value="0" min="0">
                    </div>
                </div>
                <button type="submit" class="btn btn-primary"><i class="fas fa-plus"></i> Agregar Servicio</button>
            </form>
        </div>

        <!-- Lista de servicios -->
        <h2 class="section-title"><i class="fas fa-cogs"></i> Servicios de <?php echo htmlspecialchars($producto_servicios['nombre']); ?></h2>
        <?php if (empty($servicios)): ?>
        <div class="empty-state" style="padding: 2rem;">
            <i class="fas fa-cogs"></i>
            <h2>Sin servicios</h2>
            <p>Este producto no tiene servicios configurados aún</p>
        </div>
        <?php else: ?>
        <div class="table-responsive">
            <table class="table">
                <thead>
                    <tr>
                        <th>ID</th>
                        <th>Servicio</th>
                        <th>Descripción</th>
                        <th>Precio</th>
                        <th>Orden</th>
                        <th>Estado</th>
                        <th>Acciones</th>
                    </tr>
                </thead>
                <tbody>
                    <?php foreach ($servicios as $serv): ?>
                    <tr>
                        <td><?php echo $serv['id']; ?></td>
                        <td><strong><?php echo htmlspecialchars($serv['nombre']); ?></strong></td>
                        <td style="color:var(--text-secondary);font-size:0.85rem;"><?php echo htmlspecialchars($serv['descripcion'] ?? '-'); ?></td>
                        <td><strong>$<?php echo number_format($serv['precio'], 2); ?></strong></td>
                        <td><?php echo $serv['orden']; ?></td>
                        <td>
                            <span class="badge <?php echo $serv['activo'] ? 'badge-completado' : 'badge-cancelado'; ?>">
                                <?php echo $serv['activo'] ? 'Activo' : 'Inactivo'; ?>
                            </span>
                        </td>
                        <td>
                            <div class="action-btns">
                                <a href="admin_productos.php?modo=editar_servicio&serv_id=<?php echo $serv['id']; ?>" class="btn btn-sm btn-secondary"><i class="fas fa-edit"></i></a>
                                <form method="POST" style="display:inline;" onsubmit="return confirm('¿Eliminar este servicio?')">
                                    <input type="hidden" name="accion" value="eliminar_servicio">
                                    <input type="hidden" name="servicio_id" value="<?php echo $serv['id']; ?>">
                                    <input type="hidden" name="producto_id" value="<?php echo $producto_servicios['id']; ?>">
                                    <button type="submit" class="btn btn-sm btn-outline" style="color:var(--error);border-color:var(--error);"><i class="fas fa-trash"></i></button>
                                </form>
                            </div>
                        </td>
                    </tr>
                    <?php endforeach; ?>
                </tbody>
            </table>
        </div>
        <?php endif; ?>
        <?php endif; ?>

        <!-- ===== EDITAR SERVICIO ===== -->
        <?php if ($modo === 'editar_servicio' && $servicio_editar): ?>
        <div class="admin-breadcrumb">
            <a href="admin_productos.php?modo=servicios&id=<?php echo $servicio_editar['producto_id']; ?>">
                <i class="fas fa-arrow-left"></i> Volver a Servicios de <?php echo htmlspecialchars($servicio_editar['producto_nombre']); ?>
            </a>
        </div>
        <div class="admin-form-card">
            <h2 class="section-title"><i class="fas fa-edit"></i> Editar Servicio</h2>
            <form method="POST">
                <input type="hidden" name="accion" value="editar_servicio">
                <input type="hidden" name="servicio_id" value="<?php echo $servicio_editar['id']; ?>">
                <input type="hidden" name="producto_id" value="<?php echo $servicio_editar['producto_id']; ?>">
                <div class="form-grid-2">
                    <div class="form-group">
                        <label>Nombre *</label>
                        <input type="text" name="serv_nombre" value="<?php echo htmlspecialchars($servicio_editar['nombre']); ?>" required>
                    </div>
                    <div class="form-group">
                        <label>Precio (USD) *</label>
                        <input type="number" name="serv_precio" step="0.01" min="0.01" value="<?php echo $servicio_editar['precio']; ?>" required>
                    </div>
                </div>
                <div class="form-grid-2">
                    <div class="form-group">
                        <label>Descripción</label>
                        <input type="text" name="serv_descripcion" value="<?php echo htmlspecialchars($servicio_editar['descripcion'] ?? ''); ?>">
                    </div>
                    <div class="form-group">
                        <label>Orden</label>
                        <input type="number" name="serv_orden" value="<?php echo $servicio_editar['orden']; ?>" min="0">
                    </div>
                </div>
                <div class="form-group">
                    <label class="checkbox-label">
                        <input type="checkbox" name="serv_activo" <?php echo $servicio_editar['activo'] ? 'checked' : ''; ?>>
                        Servicio activo
                    </label>
                </div>
                <div class="form-actions">
                    <button type="submit" class="btn btn-primary"><i class="fas fa-save"></i> Guardar</button>
                    <a href="admin_productos.php?modo=servicios&id=<?php echo $servicio_editar['producto_id']; ?>" class="btn btn-secondary">Cancelar</a>
                </div>
            </form>
        </div>
        <?php endif; ?>
    </div>
</section>

<?php require_once 'includes/footer.php'; ?>
