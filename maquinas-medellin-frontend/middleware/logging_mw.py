import logging
import time

from flask import request, session, g, json

from config import LOGGER_NAME, SKIP_ACCESS_LOG
from database import get_db_connection

logger = logging.getLogger(LOGGER_NAME)


# ── Hooks de request ──────────────────────────────────────────────────────────

def before_request_log():
    """Registra el timestamp de inicio del request en g para calcular duración."""
    g._req_start = time.time()


def after_request_log(response):
    """
    after_request hook.
    Loguea método, path, status, duración e IP en consola y en la tabla access_logs.
    Las rutas en SKIP_ACCESS_LOG (polling, heartbeat, etc.) se omiten.
    """
    try:
        path = request.path
        if any(path.startswith(p) for p in SKIP_ACCESS_LOG):
            return response

        duration_ms = int((time.time() - getattr(g, '_req_start', time.time())) * 1000)
        status      = response.status_code
        method      = request.method
        user_id     = session.get('user_id')
        user_name   = session.get('user_name', '-')
        ip          = request.remote_addr

        log_fn = (
            logger.error   if status >= 500 else
            logger.warning if status >= 400 else
            logger.info
        )
        log_fn(f"[HTTP] {method} {path} → {status} | {duration_ms}ms | {ip} | {user_name}")

        try:
            connection = get_db_connection()
            if connection:
                cur = connection.cursor()
                cur.execute(
                    """INSERT INTO access_logs
                           (method, path, status_code, response_time_ms,
                            user_id, user_name, ip_address, user_agent)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        method, path[:500], status, duration_ms,
                        user_id, user_name, ip,
                        (request.user_agent.string[:500] if request.user_agent else None),
                    )
                )
                connection.commit()
                cur.close()
                connection.close()
        except Exception:
            pass

    except Exception:
        pass

    return response


# ── Log transaccional ─────────────────────────────────────────────────────────

def log_transaccion(
    tipo, descripcion,
    categoria='operacional',
    usuario=None, usuario_id=None,
    maquina_id=None, maquina_nombre=None,
    entidad=None, entidad_id=None,
    monto=None, datos_extra=None,
    estado='ok'
):
    """
    Registra un evento en la tabla transaction_logs y en el logger.
    Llamar desde cualquier endpoint financiero u operacional relevante.
    """
    # ── Logger ────────────────────────────────────────────────────────────────
    try:
        partes = []
        if maquina_nombre:   partes.append(f"Máquina={maquina_nombre}")
        if monto is not None: partes.append(f"Monto=${monto:,.0f}")
        if usuario:          partes.append(f"Usuario={usuario}")
        if entidad and entidad_id: partes.append(f"{entidad}#{entidad_id}")

        linea = f"[TXN:{tipo.upper()}] {descripcion}"
        if partes:
            linea += " — " + " | ".join(partes)

        if estado == 'error':
            logger.error(linea)
        elif estado == 'advertencia':
            logger.warning(linea)
        else:
            logger.info(linea)
    except Exception:
        pass

    # ── Base de datos ─────────────────────────────────────────────────────────
    try:
        ip = None
        try:
            ip = request.remote_addr
        except RuntimeError:
            pass  # fuera de contexto de request (p.ej. tareas en background)

        connection = get_db_connection()
        if not connection:
            return

        cursor = connection.cursor()
        cursor.execute(
            """INSERT INTO transaction_logs
                   (tipo, categoria, descripcion, usuario, usuario_id,
                    maquina_id, maquina_nombre, entidad, entidad_id,
                    monto, datos_extra, ip_address, estado)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                tipo[:50], categoria[:30], descripcion[:500],
                usuario, usuario_id, maquina_id, maquina_nombre,
                entidad, entidad_id, monto,
                json.dumps(datos_extra, default=str) if datos_extra else None,
                ip, estado,
            )
        )
        connection.commit()
        cursor.close()
        connection.close()

    except Exception as e:
        logger.warning(f"[TXN] No se pudo guardar en transaction_logs: {e}")
