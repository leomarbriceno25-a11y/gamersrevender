import requests
import threading
from models import get_db


def get_telegram_config():
    """Obtener token y chat_id de la tabla configuracion"""
    try:
        db = get_db()
        rows = db.execute("SELECT clave, valor FROM configuracion WHERE clave IN ('telegram_bot_token', 'telegram_chat_id', 'telegram_activo')").fetchall()
        db.close()
        config = {r['clave']: r['valor'] for r in rows}
        if config.get('telegram_activo') != '1':
            return None, None
        return config.get('telegram_bot_token', ''), config.get('telegram_chat_id', '')
    except Exception:
        return None, None


def enviar_telegram(mensaje):
    """Enviar mensaje por Telegram Bot API (async en thread aparte para no bloquear)"""
    def _send():
        try:
            token, chat_id = get_telegram_config()
            if not token or not chat_id:
                return
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            requests.post(url, json={
                'chat_id': chat_id,
                'text': mensaje,
                'parse_mode': 'HTML'
            }, timeout=10)
        except Exception as e:
            print(f"[TELEGRAM] Error enviando notificación: {e}")
    threading.Thread(target=_send, daemon=True).start()


def notificar_recarga(usuario_nombre, monto, metodo_pago, referencia=''):
    """Notificar al admin cuando un usuario solicita recarga"""
    msg = (
        f"💰 <b>Nueva Solicitud de Recarga</b>\n\n"
        f"👤 Usuario: <b>{usuario_nombre}</b>\n"
        f"💵 Monto: <b>${monto:.2f}</b>\n"
        f"💳 Método: <b>{metodo_pago}</b>\n"
    )
    if referencia:
        msg += f"🔖 Referencia: <code>{referencia}</code>\n"
    msg += f"\n📋 Revisa en el panel de admin para aprobar/rechazar."
    enviar_telegram(msg)


def notificar_stock_bajo(producto_nombre, producto_id, stock_actual, stock_minimo):
    """Notificar al admin cuando el stock de pines baja del mínimo"""
    msg = (
        f"⚠️ <b>Stock Bajo - Almacén</b>\n\n"
        f"📦 Producto: <b>{producto_nombre}</b> (ID: {producto_id})\n"
        f"📉 Stock actual: <b>{stock_actual}</b> códigos\n"
        f"🔻 Mínimo configurado: <b>{stock_minimo}</b>\n"
        f"\n🔄 Reponer stock desde Admin → Almacén."
    )
    enviar_telegram(msg)
