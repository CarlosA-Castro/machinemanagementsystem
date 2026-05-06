"""
machine_auth.py — Autenticación de dispositivos ESP32 mediante token.

Cada máquina tiene un token único (machine_token) almacenado en la BD.
El ESP32 lo envía como header X-Machine-Token en cada request al backend.

Uso:
    from utils.machine_auth import require_machine_token

    @esp32_bp.route('/api/esp32/heartbeat', methods=['POST'])
    @require_machine_token
    def esp32_heartbeat():
        # g.machine_id y g.machine_name disponibles aquí
        ...
"""
import logging
from functools import wraps

from flask import request, jsonify, g

from config import LOGGER_NAME
from database import get_db_connection, get_db_cursor

logger = logging.getLogger(LOGGER_NAME)

MACHINE_TOKEN_HEADER = 'X-Machine-Token'


def require_machine_token(f):
    """
    Decorador que valida el token del dispositivo ESP32.

    - Lee el header X-Machine-Token de la request.
    - Busca el token en la tabla machine (solo máquinas activas).
    - Si es válido: inyecta g.machine_id y g.machine_name para uso en la ruta.
    - Si no: retorna 401 sin ejecutar la lógica de negocio.
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        token = request.headers.get(MACHINE_TOKEN_HEADER)

        if not token:
            logger.warning(
                '[machine_auth] Request sin X-Machine-Token — %s %s',
                request.method, request.path
            )
            return jsonify({
                'status':  'error',
                'code':    'AUTH001',
                'message': f'Header {MACHINE_TOKEN_HEADER} requerido',
            }), 401

        connection = None
        cursor = None
        try:
            connection = get_db_connection()
            if not connection:
                return jsonify({'status': 'error', 'code': 'E006', 'message': 'BD no disponible'}), 500

            cursor = get_db_cursor(connection)
            cursor.execute(
                "SELECT id, name FROM machine WHERE machine_token = %s AND status != 'eliminado'",
                (token,)
            )
            machine = cursor.fetchone()

        except Exception as e:
            logger.error('[machine_auth] Error validando token: %s', e)
            return jsonify({'status': 'error', 'code': 'E001', 'message': 'Error interno'}), 500
        finally:
            if cursor:     cursor.close()
            if connection: connection.close()

        if not machine:
            logger.warning(
                '[machine_auth] Token inválido — %s %s | token=%.8s...',
                request.method, request.path, token
            )
            return jsonify({
                'status':  'error',
                'code':    'AUTH002',
                'message': 'Token de máquina inválido o máquina inactiva',
            }), 401

        # Inyectar info de la máquina para uso en la ruta
        g.machine_id   = machine['id']
        g.machine_name = machine['name']

        logger.debug('[machine_auth] ✓ Máquina autenticada: %s (id=%s)', machine['name'], machine['id'])
        return f(*args, **kwargs)

    return wrapper
