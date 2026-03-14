    </main>
    <footer class="footer">
        <div class="container">
            <div class="footer-grid">
                <div class="footer-col">
                    <h3><i class="fas fa-gamepad"></i> <?php echo TIENDA_NOMBRE; ?></h3>
                    <p>Tu tienda de confianza para recargas de juegos y gift cards. Entrega instantánea y precios imbatibles.</p>
                </div>
                <div class="footer-col">
                    <h3>Enlaces</h3>
                    <ul>
                        <li><a href="dashboard.php">Inicio</a></li>
                        <li><a href="freefire.php">Free Fire</a></li>
                        <li><a href="giftcards.php">Gift Cards</a></li>
                    </ul>
                </div>
                <div class="footer-col">
                    <h3>Contacto</h3>
                    <ul>
                        <li><i class="fab fa-whatsapp"></i> WhatsApp: <?php echo WHATSAPP_NUMERO; ?></li>
                        <li><i class="fas fa-envelope"></i> <?php echo TIENDA_EMAIL; ?></li>
                        <li><i class="fab fa-instagram"></i> <?php echo TIENDA_INSTAGRAM; ?></li>
                        <li><i class="fas fa-clock"></i> <?php echo TIENDA_HORARIO; ?></li>
                    </ul>
                </div>
            </div>
            <div class="footer-bottom">
                <p>&copy; <?php echo date('Y'); ?> <?php echo TIENDA_NOMBRE; ?>. Todos los derechos reservados.</p>
            </div>
        </div>
    </footer>

    <script>
        // Toggle mobile nav
        const navToggle = document.getElementById('navToggle');
        const navLinks = document.querySelector('.nav-links');
        const navUser = document.querySelector('.nav-user');
        if (navToggle) {
            navToggle.addEventListener('click', () => {
                navLinks && navLinks.classList.toggle('active');
                navUser && navUser.classList.toggle('active');
            });
        }

        // Auto-hide flash messages
        const flash = document.querySelector('.flash-message');
        if (flash) {
            setTimeout(() => { flash.style.opacity = '0'; setTimeout(() => flash.remove(), 500); }, 4000);
        }
    </script>
</body>
</html>
