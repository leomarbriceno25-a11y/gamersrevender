import json
import sys
import time

sys.path.insert(0, '/var/www/tienda')

from gamepoint_api import recarga_completa  # noqa: E402
from models import get_db, recargar_saldo  # noqa: E402

ORDER_ID = 20204


def registrar_auditoria(pedido_id, usuario_id, producto_id, estado, detalle, referencia, payload):
    db = get_db()
    try:
        db.execute(
            "INSERT INTO recargas_auditoria (pedido_id, usuario_id, producto_id, proveedor, etapa, estado, detalle, referencia, payload) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                pedido_id,
                usuario_id,
                producto_id,
                'gamepoint',
                'retry_manual_admin',
                estado,
                detalle,
                referencia,
                json.dumps(payload, ensure_ascii=False)[:4000],
            ),
        )
        db.commit()
    finally:
        db.close()


def main():
    db = get_db()
    pedido = db.execute(
        """
        SELECT p.id, p.usuario_id, p.producto_id, p.total, p.id_juego, p.estado, p.referencia_externa,
               pr.nombre as producto_nombre, pr.gamepoint_product_id, pr.gamepoint_package_id
        FROM pedidos p
        JOIN productos pr ON pr.id = p.producto_id
        WHERE p.id = ?
        """,
        (ORDER_ID,),
    ).fetchone()
    db.close()

    if not pedido:
        print({'ok': False, 'error': f'Pedido #{ORDER_ID} no existe'})
        return

    if int(pedido['gamepoint_product_id'] or 0) <= 0 or int(pedido['gamepoint_package_id'] or 0) <= 0:
        print({'ok': False, 'error': f'Pedido #{ORDER_ID} no es GamePoint válido'})
        return

    fields = {'input1': str(pedido['id_juego'] or '').strip()}
    if not fields['input1']:
        print({'ok': False, 'error': f'Pedido #{ORDER_ID} sin id_juego'})
        return

    merchant_code = f"RETRY{ORDER_ID}_{int(time.time())}"
    resultado = recarga_completa(
        product_id=int(pedido['gamepoint_product_id']),
        fields=fields,
        package_id=int(pedido['gamepoint_package_id']),
        merchant_code=merchant_code,
        wait=True,
    )

    ref = str(resultado.get('referenceno', '') or '').strip()
    status = str(resultado.get('status', '') or '').strip().lower()

    nuevo_estado = 'procesando'
    detalle = f"Retry manual pedido #{ORDER_ID}. status={status or '-'}"

    if resultado.get('ok') and status == 'success':
        nuevo_estado = 'completado'
        db2 = get_db()
        try:
            db2.execute(
                "UPDATE pedidos SET estado = 'completado', nombre_jugador = ?, referencia_externa = ? WHERE id = ?",
                (str(resultado.get('ingamename', '') or pedido['id_juego']), ref, ORDER_ID),
            )
            db2.commit()
        finally:
            db2.close()
    elif status == 'failed':
        nuevo_estado = 'cancelado'
        db2 = get_db()
        try:
            db2.execute("UPDATE pedidos SET estado = 'cancelado', referencia_externa = ? WHERE id = ?", (ref, ORDER_ID))
            db2.commit()
        finally:
            db2.close()

        db3 = get_db()
        try:
            ya_reembolsado = db3.execute(
                "SELECT id FROM transacciones WHERE usuario_id = ? AND tipo = 'recarga' AND descripcion LIKE ? ORDER BY id DESC LIMIT 1",
                (int(pedido['usuario_id']), f"%pedido #{ORDER_ID}%"),
            ).fetchone()
        finally:
            db3.close()

        if not ya_reembolsado:
            recargar_saldo(int(pedido['usuario_id']), float(pedido['total']), f"Reembolso: Retry GamePoint FAIL pedido #{ORDER_ID}")
            detalle += ' | reembolso aplicado'
        else:
            detalle += ' | reembolso ya existía'
    else:
        db2 = get_db()
        try:
            if ref:
                db2.execute("UPDATE pedidos SET estado = 'procesando', referencia_externa = ? WHERE id = ?", (ref, ORDER_ID))
            else:
                db2.execute("UPDATE pedidos SET estado = 'procesando' WHERE id = ?", (ORDER_ID,))
            db2.commit()
        finally:
            db2.close()

    registrar_auditoria(
        pedido_id=ORDER_ID,
        usuario_id=int(pedido['usuario_id']),
        producto_id=int(pedido['producto_id']),
        estado=nuevo_estado,
        detalle=detalle,
        referencia=ref,
        payload=resultado,
    )

    dbf = get_db()
    try:
        final = dbf.execute("SELECT id, estado, referencia_externa, nombre_jugador FROM pedidos WHERE id = ?", (ORDER_ID,)).fetchone()
    finally:
        dbf.close()

    print({'ok': True, 'resultado': resultado, 'pedido_final': dict(final) if final else None})


if __name__ == '__main__':
    main()
