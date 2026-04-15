import logging

from flask import json, request

from config import LOGGER_NAME
from database import get_db_connection

logger = logging.getLogger(LOGGER_NAME)


def log_transaction(
    tipo,
    descripcion,
    categoria='operacional',
    usuario=None,
    usuario_id=None,
    maquina_id=None,
    maquina_nombre=None,
    entidad=None,
    entidad_id=None,
    monto=None,
    datos_extra=None,
    estado='ok',
):
    """Registra un evento transaccional en DB y en logs de aplicación."""
    try:
        extra_parts = []
        if maquina_nombre:
            extra_parts.append(f"Máquina={maquina_nombre}")
        if monto is not None:
            extra_parts.append(f"Monto=${monto:,.0f}")
        if usuario:
            extra_parts.append(f"Usuario={usuario}")
        if entidad and entidad_id:
            extra_parts.append(f"{entidad}#{entidad_id}")

        log_line = f"[TXN:{tipo.upper()}] {descripcion}"
        if extra_parts:
            log_line += f" | {' | '.join(extra_parts)}"

        if estado == 'error':
            logger.error(log_line)
        elif estado == 'advertencia':
            logger.warning(log_line)
        else:
            logger.info(log_line)
    except Exception:
        pass

    connection = None
    cursor = None
    try:
        ip = None
        try:
            ip = request.remote_addr
        except RuntimeError:
            pass

        connection = get_db_connection()
        if not connection:
            return

        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO transaction_logs
                (tipo, categoria, descripcion, usuario, usuario_id, maquina_id, maquina_nombre,
                 entidad, entidad_id, monto, datos_extra, ip_address, estado)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                tipo[:50],
                categoria[:30],
                descripcion[:500],
                usuario,
                usuario_id,
                maquina_id,
                maquina_nombre,
                entidad,
                entidad_id,
                monto,
                json.dumps(datos_extra, default=str) if datos_extra else None,
                ip,
                estado,
            ),
        )
        connection.commit()
    except Exception as e:
        logger.warning(f"[TXN] No se pudo guardar en transaction_logs: {e}")
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()
