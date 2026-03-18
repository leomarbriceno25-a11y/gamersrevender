"""
Script de exploración de PagoStore Garena para identificar selectores.
Abre el navegador en modo visible para ver el flujo real.
"""
import asyncio
from playwright.async_api import async_playwright

async def explorar():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=1000)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 720},
            locale="es-CO",
            timezone_id="America/Bogota"
        )
        page = await context.new_page()

        print("[1] Abriendo PagoStore Free Fire...")
        await page.goto("https://pagostore.garena.com/app/100067/idlogin", wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(5000)

        # Tomar screenshot
        await page.screenshot(path="garena_step1.png", full_page=True)
        print("[1] Screenshot guardado: garena_step1.png")

        # Imprimir todos los inputs visibles
        inputs = await page.locator('input').all()
        print(f"\n[1] Inputs encontrados: {len(inputs)}")
        for i, inp in enumerate(inputs):
            try:
                placeholder = await inp.get_attribute('placeholder') or ''
                tipo = await inp.get_attribute('type') or ''
                name = await inp.get_attribute('name') or ''
                visible = await inp.is_visible()
                print(f"  Input {i}: type={tipo} name={name} placeholder={placeholder} visible={visible}")
            except:
                pass

        # Imprimir todos los botones visibles
        buttons = await page.locator('button').all()
        print(f"\n[1] Botones encontrados: {len(buttons)}")
        for i, btn in enumerate(buttons):
            try:
                text = await btn.text_content()
                visible = await btn.is_visible()
                if visible and text.strip():
                    print(f"  Button {i}: '{text.strip()}'")
            except:
                pass

        # Esperar a que el usuario interactúe manualmente
        print("\n" + "="*60)
        print("NAVEGADOR ABIERTO - Explora la página manualmente")
        print("Presiona ENTER para cerrar cuando termines...")
        print("="*60)
        input()

        await browser.close()

asyncio.run(explorar())
