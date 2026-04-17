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
- `POST /api/v1/comprar` - Realizar compra (soporta `merchant_ref`)
- `GET /api/v1/recargas/status?merchant_ref=...` - Consultar estado por referencia (pull recomendado)
- `GET /api/v1/pedidos` - Listar pedidos
- `GET /api/v1/pedido/:id` - Detalle de pedido
- `GET /api/v1/transacciones` - Historial
- `GET /api/v1/webhook` - Ver webhook configurado
- `POST /api/v1/webhook` - Registrar/eliminar webhook (opcional)

Header requerido: `X-API-Key: TU_API_KEY`

### Flujo recomendado para revendedor
1. Crear pedido con `POST /api/v1/comprar` enviando `merchant_ref` único por venta.
2. Si responde `estado=procesando`, consultar `GET /api/v1/recargas/status?merchant_ref=...` cada 20-60s.
3. Webhook se mantiene disponible, pero no es obligatorio.

## Deploy en VPS
```bash
pip install -r requirements.txt
python app.py
```

## Producción con Gunicorn
```bash
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```
