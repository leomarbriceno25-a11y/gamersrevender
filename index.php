<?php
require_once 'config.php';

// Redirigir según estado de sesión
if (estaLogueado()) {
    header('Location: dashboard.php');
} else {
    header('Location: login.php');
}
exit;
?>
