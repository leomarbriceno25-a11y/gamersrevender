import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

SECRET_KEY = os.environ.get('SECRET_KEY', 'gamersrev-secret-key-cambiar-en-produccion')
SQLALCHEMY_DATABASE_URI = f"sqlite:///{os.path.join(BASE_DIR, 'instance', 'tienda.db')}"

# Tienda
TIENDA_NOMBRE = 'TiendaGiftVen API'
WHATSAPP_NUMERO = '521234567890'

# API Hype Games
HYPE_API_URL = 'https://redeem.hype.games'
