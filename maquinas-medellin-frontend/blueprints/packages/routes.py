import logging

import sentry_sdk
from flask import Blueprint, request, jsonify

from config import LOGGER_NAME
from database import get_db_connection, get_db_cursor
from utils.auth import require_login
from utils.location_scope import get_active_location, user_can_view_all
from utils.responses import api_response, handle_api_errors
from utils.validators import validate_required_fields

logger = logging.getLogger(LOGGER_NAME)

packages_bp = Blueprint('packages', __name__)


@packages_bp.route('/api/paquetes', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def listar_paquetes():
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)
        active_id, _ = get_active_location()

        if user_can_view_all() and active_id is None:
            cursor.execute(
                "SELECT * FROM turnpackage ORDER BY isActive DESC, name"
            )
        else:
            eff = active_id if active_id is not None else -1
            cursor.execute(
                "SELECT * FROM turnpackage WHERE location_id = %s ORDER BY isActive DESC, name",
                (eff,),
            )

        return jsonify(cursor.fetchall())

    except Exception as e:
        logger.error(f"Error listando paquetes: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:  cursor.close()
        if connection: connection.close()


@packages_bp.route('/api/paquetes/<int:paquete_id>', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_paquete(paquete_id):
    """Obtener un paquete específico"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)
        cursor.execute("SELECT * FROM turnpackage WHERE id = %s", (paquete_id,))
        paquete = cursor.fetchone()

        if not paquete:
            return api_response('Q004', http_status=404, data={'paquete_id': paquete_id})

        return jsonify(paquete)

    except Exception as e:
        logger.error(f"Error obteniendo paquete: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:  cursor.close()
        if connection: connection.close()


@packages_bp.route('/api/paquetes', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
@validate_required_fields(['name', 'turns', 'price'])
def crear_paquete():
    """Crear un nuevo paquete"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        name = data['name']
        turns = data['turns']
        price = data['price']
        isActive = data.get('isActive', True)

        if turns < 1:
            return api_response('E005', http_status=400, data={'message': 'Turnos debe ser mayor a 0'})
        if price < 1000:
            return api_response('E005', http_status=400, data={'message': 'Precio debe ser mayor a $1,000'})

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        active_id, _ = get_active_location()
        location_id = active_id if active_id is not None else None

        cursor.execute(
            "SELECT id FROM turnpackage WHERE name = %s AND (location_id <=> %s)",
            (name, location_id),
        )
        if cursor.fetchone():
            return api_response('E007', http_status=400, data={'message': 'Paquete ya existe en este local'})

        cursor.execute("""
            INSERT INTO turnpackage (name, turns, price, isActive, location_id)
            VALUES (%s, %s, %s, %s, %s)
        """, (name, turns, price, isActive, location_id))

        connection.commit()

        logger.info(f"Paquete creado: {name} (Turnos: {turns}, Precio: {price}, Local: {location_id})")

        return api_response('S002', status='success', data={'paquete_id': cursor.lastrowid})

    except Exception as e:
        logger.error(f"Error creando paquete: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:  cursor.close()
        if connection: connection.close()


@packages_bp.route('/api/paquetes/<int:paquete_id>', methods=['PUT'])
@handle_api_errors
@require_login(['admin'])
@validate_required_fields(['name', 'turns', 'price'])
def actualizar_paquete(paquete_id):
    """Actualizar un paquete existente"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        name = data['name']
        turns = data['turns']
        price = data['price']
        isActive = data.get('isActive')

        if turns < 1:
            return api_response('E005', http_status=400, data={'message': 'Turnos debe ser mayor a 0'})
        if price < 1000:
            return api_response('E005', http_status=400, data={'message': 'Precio debe ser mayor a $1,000'})

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("SELECT id FROM turnpackage WHERE id = %s", (paquete_id,))
        if not cursor.fetchone():
            return api_response('Q004', http_status=404, data={'paquete_id': paquete_id})

        cursor.execute("SELECT id FROM turnpackage WHERE name = %s AND id != %s", (name, paquete_id))
        if cursor.fetchone():
            return api_response('E007', http_status=400, data={'message': 'Nombre de paquete ya existe'})

        cursor.execute("""
            UPDATE turnpackage
            SET name = %s, turns = %s, price = %s, isActive = %s
            WHERE id = %s
        """, (name, turns, price, isActive, paquete_id))

        connection.commit()

        logger.info(f"Paquete actualizado: {name} (ID: {paquete_id})")

        return api_response('S003', status='success')

    except Exception as e:
        logger.error(f"Error actualizando paquete: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:  cursor.close()
        if connection: connection.close()


@packages_bp.route('/api/paquetes/<int:paquete_id>', methods=['DELETE'])
@handle_api_errors
@require_login(['admin'])
def eliminar_paquete(paquete_id):
    """Eliminar un paquete"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("SELECT name FROM turnpackage WHERE id = %s", (paquete_id,))
        paquete = cursor.fetchone()
        if not paquete:
            return api_response('Q004', http_status=404, data={'paquete_id': paquete_id})

        cursor.execute("""
            SELECT COUNT(*) as uso_count FROM qrcode WHERE turnPackageId = %s
        """, (paquete_id,))
        uso_count = cursor.fetchone()['uso_count']

        if uso_count > 0:
            return api_response(
                'W004',
                status='warning',
                http_status=400,
                data={
                    'message': f'Paquete en uso por {uso_count} códigos QR',
                    'uso_count': uso_count
                }
            )

        cursor.execute("DELETE FROM turnpackage WHERE id = %s", (paquete_id,))
        connection.commit()

        logger.info(f"Paquete eliminado: {paquete['name']} (ID: {paquete_id})")

        return api_response('S004', status='success')

    except Exception as e:
        logger.error(f"Error eliminando paquete: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:  cursor.close()
        if connection: connection.close()
