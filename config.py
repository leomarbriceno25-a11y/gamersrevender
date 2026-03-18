import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

_secret_file = os.path.join(BASE_DIR, 'instance', '.secret_key')
def _get_secret_key():
    os.makedirs(os.path.dirname(_secret_file), exist_ok=True)
    if os.path.exists(_secret_file):
        with open(_secret_file, 'r') as f:
            return f.read().strip()
    import secrets as _s
    key = _s.token_hex(32)
    with open(_secret_file, 'w') as f:
        f.write(key)
    return key
SECRET_KEY = os.environ.get('SECRET_KEY') or _get_secret_key()
SQLALCHEMY_DATABASE_URI = f"sqlite:///{os.path.join(BASE_DIR, 'instance', 'tienda.db')}"

# Tienda
TIENDA_NOMBRE = 'TiendaGiftVen API'
WHATSAPP_NUMERO = '521234567890'

# API Hype Games
HYPE_API_URL = 'https://redeem.hype.games'

# GamePoint Club API
GAMEPOINT_PARTNER_ID = os.environ.get('GAMEPOINT_PARTNER_ID', '8e444f41-3c46-4226-b90d-2fb8db46fbed')
GAMEPOINT_SECRET_KEY = os.environ.get('GAMEPOINT_SECRET_KEY', '4d642026a78b03d66710')
GAMEPOINT_API_URL = 'https://api.gamepointclub.net'
