# GamRevender - Plataforma de Recargas para Revendedores

Plataforma Flask + SQLite para gestión de recargas de diamantes Free Fire y Gift Cards.

## Características
- Sistema de cartera/wallet por usuario
- API REST para conexión de revendedores
- Panel de administración completo
- Catálogo de productos (Free Fire, Gift Cards)
- Historial de transacciones y pedidos

## Credenciales de prueba
- **Admin:** admin@gamersrev.com / admin123

## API Endpoints
- `GET /api/v1/saldo` - Consultar saldo
- `GET /api/v1/productos` - Listar productos
- `POST /api/v1/comprar` - Realizar compra
- `GET /api/v1/pedidos` - Listar pedidos
- `GET /api/v1/transacciones` - Historial

Header requerido: `X-API-Key: TU_API_KEY`

## Deploy en VPS
```bash
pip install -r requirements.txt
python app.py
```

## Producción con Gunicorn
```bash
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```
