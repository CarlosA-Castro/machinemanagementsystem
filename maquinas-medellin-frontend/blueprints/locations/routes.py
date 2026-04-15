import logging
import traceback

import sentry_sdk
from flask import Blueprint, request, jsonify

from config import LOGGER_NAME
from database import get_db_connection, get_db_cursor
from utils.auth import require_login
from utils.responses import api_response, handle_api_errors
from utils.validators import validate_required_fields

logger = logging.getLogger(LOGGER_NAME)

locations_bp = Blueprint('locations', __name__)


@locations_bp.route('/api/locales', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_locales():
    """Obtener todos los locales con estadísticas"""
    connection = None
    cursor = None
    try:
        logger.info("=== OBTENIENDO LOCALES ===")

        connection = get_db_connection()
        if not connection:
            logger.error("No se pudo conectar a la BD")
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("""
            SELECT
                l.id, l.name, l.address, l.city, l.status,
                l.telefono, l.horario, l.notas
            FROM location l
            ORDER BY l.name
        """)

        locales = cursor.fetchall()
        logger.info(f"Locales encontrados: {len(locales)}")

        if not locales:
            return jsonify([])

        locales_con_estadisticas = []
        for local in locales:
            cursor.execute("""
                SELECT
                    COUNT(m.id) as maquinas_count,
                    SUM(CASE WHEN m.status = 'activa' THEN 1 ELSE 0 END) as maquinas_activas
                FROM machine m
                WHERE m.location_id = %s
            """, (local['id'],))

            stats = cursor.fetchone()

            locales_con_estadisticas.append({
                'id': local['id'],
                'name': local['name'],
                'address': local.get('address', ''),
                'city': local.get('city', ''),
                'status': local.get('status', 'activo'),
                'telefono': local.get('telefono', ''),
                'horario': local.get('horario', ''),
                'notas': local.get('notas', ''),
                'maquinas_count': stats['maquinas_count'] if stats else 0,
                'maquinas_activas': stats['maquinas_activas'] if stats else 0,
            })

        logger.info("Locales procesados exitosamente")
        return jsonify(locales_con_estadisticas)

    except Exception as e:
        logger.error(f"Error obteniendo locales: {e}", exc_info=True)
        logger.error(f"Traceback completo: {traceback.format_exc()}")
        return api_response('E001', http_status=500, data={'error_detail': str(e)})
    finally:
        if cursor:  cursor.close()
        if connection: connection.close()


@locations_bp.route('/api/locales/<int:local_id>', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_local(local_id):
    """Obtener un local específico"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)
        cursor.execute("SELECT * FROM location WHERE id = %s", (local_id,))
        local = cursor.fetchone()

        if not local:
            return api_response('E002', http_status=404, data={'local_id': local_id})

        return jsonify({
            'id': local['id'],
            'name': local['name'],
            'address': local.get('address', ''),
            'city': local.get('city', ''),
            'status': local.get('status', 'activo'),
            'telefono': local.get('telefono', ''),
            'horario': local.get('horario', ''),
            'notas': local.get('notas', ''),
        })

    except Exception as e:
        logger.error(f"Error obteniendo local: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:  cursor.close()
        if connection: connection.close()


@locations_bp.route('/api/locales', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
@validate_required_fields(['name', 'address', 'city'])
def crear_local():
    """Crear un nuevo local"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        name     = data['name']
        address  = data['address']
        city     = data['city']
        status   = data.get('status', 'activo')
        telefono = data.get('telefono', '')
        horario  = data.get('horario', '')
        notas    = data.get('notas', '')

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("SELECT id FROM location WHERE name = %s", (name,))
        if cursor.fetchone():
            return api_response('E007', http_status=400, data={'message': 'Local ya existe'})

        cursor.execute("""
            INSERT INTO location (name, address, city, status, telefono, horario, notas)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (name, address, city, status, telefono, horario, notas))

        connection.commit()

        logger.info(f"Local creado: {name} en {city}")

        return api_response('S002', status='success', data={'local_id': cursor.lastrowid})

    except Exception as e:
        logger.error(f"Error creando local: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:  cursor.close()
        if connection: connection.close()


@locations_bp.route('/api/locales/<int:local_id>', methods=['PUT'])
@handle_api_errors
@require_login(['admin'])
@validate_required_fields(['name', 'address', 'city'])
def actualizar_local(local_id):
    """Actualizar un local existente"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        name     = data['name']
        address  = data['address']
        city     = data['city']
        status   = data.get('status')
        telefono = data.get('telefono', '')
        horario  = data.get('horario', '')
        notas    = data.get('notas', '')

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("SELECT id FROM location WHERE id = %s", (local_id,))
        if not cursor.fetchone():
            return api_response('E002', http_status=404, data={'local_id': local_id})

        cursor.execute("SELECT id FROM location WHERE name = %s AND id != %s", (name, local_id))
        if cursor.fetchone():
            return api_response('E007', http_status=400, data={'message': 'Nombre de local ya existe'})

        cursor.execute("""
            UPDATE location
            SET name = %s, address = %s, city = %s, status = %s,
                telefono = %s, horario = %s, notas = %s
            WHERE id = %s
        """, (name, address, city, status, telefono, horario, notas, local_id))

        connection.commit()

        logger.info(f"Local actualizado: {name} (ID: {local_id})")

        return api_response('S003', status='success')

    except Exception as e:
        logger.error(f"Error actualizando local: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:  cursor.close()
        if connection: connection.close()


@locations_bp.route('/api/locales/<int:local_id>', methods=['DELETE'])
@handle_api_errors
@require_login(['admin'])
def eliminar_local(local_id):
    """Eliminar un local"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("SELECT name FROM location WHERE id = %s", (local_id,))
        local = cursor.fetchone()
        if not local:
            return api_response('E002', http_status=404, data={'local_id': local_id})

        cursor.execute(
            "SELECT COUNT(*) as maquinas_count FROM machine WHERE location_id = %s", (local_id,)
        )
        maquinas_count = cursor.fetchone()['maquinas_count']

        if maquinas_count > 0:
            return api_response(
                'W005',
                status='warning',
                http_status=400,
                data={
                    'message': f'Local tiene {maquinas_count} máquinas asignadas',
                    'maquinas_count': maquinas_count,
                }
            )

        cursor.execute("DELETE FROM location WHERE id = %s", (local_id,))
        connection.commit()

        logger.info(f"Local eliminado: {local['name']} (ID: {local_id})")

        return api_response('S004', status='success')

    except Exception as e:
        logger.error(f"Error eliminando local: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:  cursor.close()
        if connection: connection.close()
