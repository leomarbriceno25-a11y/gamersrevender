"""
Módulo de scraping para PagoStore Garena (Free Fire)
Usa Playwright para automatizar recargas de diamantes via Razer Gold

Flujo:
1. Abre PagoStore Garena (región Colombia)
2. Ingresa el ID del jugador y hace clic en "Iniciar Sesión"
3. Selecciona el monto de diamantes (100, 310, 520, etc.)
4. Selecciona Razer Gold como método de pago
5. Redirige a Razer Gold → login → confirmar pago

Requiere: playwright, playwright install chromium
"""
import asyncio
import os
import random
import requests
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

# Credenciales desde variables de entorno o config
RAZER_EMAIL = os.environ.get('RAZER_EMAIL', '')
RAZER_PASSWORD = os.environ.get('RAZER_PASSWORD', '')
TWOCAPTCHA_API_KEY = os.environ.get('TWOCAPTCHA_API_KEY', '')

# URL base de PagoStore Free Fire (región Colombia)
PAGOSTORE_URL = "https://pagostore.garena.com/app/100067/idlogin"

# Montos disponibles en PagoStore (texto que aparece en los botones)
MONTOS_DISPONIBLES = [100, 310, 520, 1050, 1060, 2160, 2180, 5580, 5600]


async def esperar_captcha_manual(page, timeout_seg=60):
    """
    Espera a que el usuario resuelva manualmente el captcha DataDome.
    Detecta si hay captcha, muestra mensaje, y espera hasta que desaparezca.
    Retorna True si se resolvió, False si timeout.
    """
    # Detectar si hay captcha
    has_captcha = False
    for frame in page.frames:
        if 'captcha' in frame.url.lower() or 'datadome' in frame.url.lower() or 'geo.captcha' in frame.url.lower():
            has_captcha = True
            break

    if not has_captcha:
        try:
            visible = await page.locator('text="Desliza hacia la derecha"').is_visible(timeout=2000)
            if not visible:
                print("[CAPTCHA] No se detectó captcha - página limpia")
                return True
        except:
            print("[CAPTCHA] No se detectó captcha")
            return True

    print("\n" + "=" * 60)
    print("⚠️  CAPTCHA DETECTADO - Resuélvelo manualmente en el navegador")
    print("   Desliza la flecha hacia la derecha")
    print(f"   Esperando máximo {timeout_seg} segundos...")
    print("=" * 60 + "\n")

    # Esperar hasta que el captcha desaparezca
    for i in range(timeout_seg):
        await page.wait_for_timeout(1000)
        has_captcha = False
        for frame in page.frames:
            if 'captcha' in frame.url.lower() or 'datadome' in frame.url.lower() or 'geo.captcha' in frame.url.lower():
                has_captcha = True
                break
        if not has_captcha:
            # Verificar también el texto
            try:
                visible = await page.locator('text="Desliza hacia la derecha"').is_visible(timeout=500)
                if not visible:
                    print(f"[CAPTCHA] ¡Captcha resuelto! ({i+1}s)")
                    return True
            except:
                print(f"[CAPTCHA] ¡Captcha resuelto! ({i+1}s)")
                return True

        if (i + 1) % 10 == 0:
            print(f"[CAPTCHA] Esperando... {timeout_seg - i - 1}s restantes")

    print("[CAPTCHA] Timeout - captcha no resuelto")
    return False


async def recargar_garena(player_id, monto_diamantes, headless=True):
    """
    Ejecuta una recarga de Free Fire via PagoStore Garena + Razer Gold.
    
    Args:
        player_id: ID del jugador Free Fire (ej: "3648494384")
        monto_diamantes: Monto de diamantes (100, 310, 520, 1050, 1060, 2160, 2180, 5580, 5600)
        headless: True para navegador invisible, False para ver el proceso
    
    Returns:
        dict con ok, mensaje, nombre_jugador, etc.
    """
    if not RAZER_EMAIL or not RAZER_PASSWORD:
        return {"ok": False, "error": "Credenciales de Razer Gold no configuradas"}

    resultado = {"ok": False, "error": "No completado", "nombre_jugador": ""}

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-blink-features=AutomationControlled',
            ]
        )
        context = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            locale="es-CO",
            timezone_id="America/Bogota",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        )
        stealth = Stealth()
        page = await context.new_page()
        # Aplicar stealth para evadir DataDome
        await stealth.apply_stealth_async(page)

        try:
            # === PASO 1: Abrir PagoStore Free Fire ===
            print(f"[GARENA] Abriendo PagoStore...")
            await page.goto(PAGOSTORE_URL, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(3000)

            # === PASO 2: Ingresar ID del jugador ===
            print(f"[GARENA] Ingresando ID: {player_id}")
            id_input = page.locator('input[placeholder="Introduce el ID del jugador aquí."]')
            await id_input.fill(str(player_id))
            await page.wait_for_timeout(1000)

            # Clic en "Iniciar Sesión"
            await page.locator('button:has-text("Iniciar Sesión")').click()
            await page.wait_for_timeout(3000)

            # === PASO 3: Esperar resolución manual del CAPTCHA ===
            captcha_resolved = await esperar_captcha_manual(page)
            if captcha_resolved:
                print("[GARENA] CAPTCHA resuelto, esperando...")
                await page.wait_for_timeout(4000)
            else:
                print("[GARENA] No se detectó CAPTCHA o ya pasó")
                await page.wait_for_timeout(2000)

            # === PASO 4: Verificar nombre del jugador ===
            try:
                await page.screenshot(path="garena_after_login.png")
                body_text = await page.locator('body').text_content()
                print(f"[GARENA] Página después de login cargada")
            except Exception as e:
                print(f"[GARENA] Info: {e}")

            # === PASO 4: Seleccionar tab "Compra" ===
            print(f"[GARENA] Seleccionando tab Compra...")
            try:
                compra_tab = page.locator('button:has-text("Compra")').first
                await compra_tab.click()
                await page.wait_for_timeout(2000)
            except:
                print("[GARENA] Tab Compra ya seleccionado o no encontrado")

            # === PASO 5: Seleccionar monto de diamantes ===
            print(f"[GARENA] Seleccionando {monto_diamantes} diamantes...")
            # Los montos aparecen como texto en botones/divs clickeables
            monto_text = str(monto_diamantes)
            # Buscar el elemento que contiene el número exacto
            monto_btn = page.locator(f'text="{monto_text}"').first
            try:
                await monto_btn.click(timeout=5000)
            except:
                # Intentar con formato con punto (ej: 1.050, 2.160, 5.600)
                if monto_diamantes >= 1000:
                    monto_formatted = f"{monto_diamantes:,.0f}".replace(",", ".")
                    monto_btn = page.locator(f'text="{monto_formatted}"').first
                    await monto_btn.click(timeout=5000)
            await page.wait_for_timeout(2000)

            # === PASO 6: Seleccionar Razer Gold como método de pago ===
            print("[GARENA] Seleccionando Razer Gold...")
            # Razer Gold aparece como una opción con imagen, buscar por alt o src
            razer = page.locator('[alt*="razer" i], [alt*="Razer"], img[src*="razer" i]').first
            try:
                await razer.click(timeout=5000)
            except:
                # Intentar buscar por texto
                razer = page.locator('text=Razer').first
                await razer.click(timeout=5000)
            await page.wait_for_timeout(2000)

            # Tomar screenshot antes de pagar
            await page.screenshot(path="garena_before_pay.png")

            # === PASO 7: Buscar y hacer clic en botón de pagar ===
            print("[GARENA] Buscando botón de pago...")
            # Puede haber un botón de confirmar/pagar
            pay_btn = page.locator('button:has-text("Comprar"), button:has-text("Pagar"), button:has-text("Confirmar"), button:has-text("Buy"), button:has-text("Pay")').first
            await pay_btn.click(timeout=5000)
            await page.wait_for_timeout(5000)

            # === PASO 8: Manejar redirección a Razer Gold ===
            print(f"[GARENA] URL actual: {page.url}")

            # Verificar si se abrió nueva pestaña
            all_pages = context.pages
            for pg in all_pages:
                if "razer" in pg.url.lower() or "gold" in pg.url.lower():
                    page = pg
                    break

            await page.wait_for_load_state("networkidle", timeout=20000)
            await page.screenshot(path="garena_razer_page.png")
            print(f"[GARENA] Página Razer Gold: {page.url}")

            # === PASO 9: Login en Razer Gold ===
            print("[GARENA] Login en Razer Gold...")
            # Buscar campo de email
            email_input = page.locator('input[type="email"], input[name*="email"], input[placeholder*="email" i], input[id*="email" i]').first
            try:
                await email_input.fill(RAZER_EMAIL, timeout=5000)
            except:
                # Puede que ya esté logueado, buscar input de texto
                email_input = page.locator('input[type="text"]').first
                await email_input.fill(RAZER_EMAIL, timeout=5000)

            # Buscar campo de contraseña
            pass_input = page.locator('input[type="password"]').first
            await pass_input.fill(RAZER_PASSWORD)
            await page.wait_for_timeout(1000)

            # Clic en login/sign in
            login_btn = page.locator('button[type="submit"], button:has-text("Sign In"), button:has-text("Log In"), button:has-text("Iniciar"), button:has-text("Login")').first
            await login_btn.click()
            await page.wait_for_timeout(5000)

            await page.screenshot(path="garena_after_razer_login.png")
            print(f"[GARENA] Después de login Razer: {page.url}")

            # === PASO 10: Confirmar pago en Razer Gold ===
            print("[GARENA] Confirmando pago en Razer Gold...")
            confirm_btn = page.locator('button:has-text("Confirm"), button:has-text("Pay"), button:has-text("Confirmar"), button:has-text("Pagar"), button:has-text("Complete")').first
            try:
                await confirm_btn.click(timeout=10000)
            except:
                print("[GARENA] No se encontró botón de confirmar, puede requerir acción manual")
                await page.screenshot(path="garena_confirm_error.png")
            
            await page.wait_for_timeout(8000)

            # === PASO 11: Verificar resultado ===
            await page.screenshot(path="garena_result.png")
            page_text = await page.locator('body').text_content()
            page_text_lower = page_text.lower() if page_text else ""

            if any(word in page_text_lower for word in ['success', 'éxito', 'exitosa', 'completado', 'completed', 'thank', 'gracias']):
                resultado["ok"] = True
                resultado["mensaje"] = "Recarga completada exitosamente via PagoStore Garena"
                print("[GARENA] ¡Recarga exitosa!")
            else:
                resultado["error"] = "No se pudo confirmar el resultado. Revisa garena_result.png"
                print("[GARENA] Resultado incierto - revisa screenshots")

        except Exception as e:
            resultado["error"] = str(e)
            print(f"[GARENA] Error: {e}")
            try:
                await page.screenshot(path="garena_error.png")
            except:
                pass

        finally:
            await browser.close()

    return resultado


def recargar_garena_sync(player_id, monto_diamantes, headless=True):
    """Versión síncrona del scraping para usar desde Flask"""
    return asyncio.run(recargar_garena(player_id, monto_diamantes, headless))


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Uso: python garena_api.py <ID_FREEFIRE> <DIAMANTES>")
        print("Ejemplo: python garena_api.py 3648494384 100")
        print(f"Montos disponibles: {MONTOS_DISPONIBLES}")
        sys.exit(1)

    pid = sys.argv[1]
    diamantes = int(sys.argv[2])

    if not RAZER_EMAIL:
        os.environ['RAZER_EMAIL'] = 'leomarspark27@gmail.com'
        os.environ['RAZER_PASSWORD'] = 'Homero.1234'
        os.environ['TWOCAPTCHA_API_KEY'] = 'dc0f07e81c4a5d27b0a16c73e2b9ba3a'
        RAZER_EMAIL = os.environ['RAZER_EMAIL']
        RAZER_PASSWORD = os.environ['RAZER_PASSWORD']
        TWOCAPTCHA_API_KEY = os.environ['TWOCAPTCHA_API_KEY']

    print(f"\nRecargando {diamantes} diamantes para ID {pid}...")
    print(f"Razer Gold: {RAZER_EMAIL}")
    result = recargar_garena_sync(pid, diamantes, headless=False)
    print(f"\nResultado: {result}")
