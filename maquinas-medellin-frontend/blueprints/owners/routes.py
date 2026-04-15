import logging

import sentry_sdk
from flask import Blueprint, request, jsonify

from config import LOGGER_NAME
from database import get_db_connection, get_db_cursor
from utils.auth import require_login
from utils.responses import api_response, handle_api_errors
from utils.validators import validate_required_fields

logger = logging.getLogger(LOGGER_NAME)

owners_bp = Blueprint('owners', __name__)


@owners_bp.route('/api/propietarios', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_propietarios():
    """Obtener todos los propietarios"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("""
            SELECT * FROM propietarios
            ORDER BY nombre
        """)

        propietarios = cursor.fetchall()

        propietarios_formateados = []
        for prop in propietarios:
            propietarios_formateados.append({
                'id': prop['id'],
                'nombre': prop['nombre'],
                'telefono': prop.get('telefono', ''),
                'email': prop.get('email', ''),
                'notas': prop.get('notas', '')
            })

        return jsonify(propietarios_formateados)

    except Exception as e:
        logger.error(f"Error obteniendo propietarios: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


@owners_bp.route('/api/propietarios/<int:propietario_id>', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_propietario(propietario_id):
    """Obtener un propietario específico"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("SELECT * FROM Propietarios WHERE id = %s", (propietario_id,))
        propietario = cursor.fetchone()

        if not propietario:
            return api_response('E002', http_status=404, data={'propietario_id': propietario_id})

        # Obtener máquinas asociadas
        cursor.execute("""
            SELECT m.id, m.name, mp.porcentaje_propiedad
            FROM MaquinaPropietario mp
            JOIN machine m ON mp.maquina_id = m.id
            WHERE mp.propietario_id = %s
        """, (propietario_id,))

        maquinas = cursor.fetchall()

        return jsonify({
            'id': propietario['id'],
            'nombre': propietario['nombre'],
            'telefono': propietario.get('telefono', ''),
            'email': propietario.get('email', ''),
            'notas': propietario.get('notas', ''),
            'maquinas': maquinas
        })

    except Exception as e:
        logger.error(f"Error obteniendo propietario: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


@owners_bp.route('/api/propietarios', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
@validate_required_fields(['nombre'])
def crear_propietario():
    """Crear un nuevo propietario"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        nombre   = data['nombre']
        telefono = data.get('telefono', '')
        email    = data.get('email', '')
        notas    = data.get('notas', '')

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("SELECT id FROM Propietarios WHERE nombre = %s", (nombre,))
        if cursor.fetchone():
            return api_response('E007', http_status=400, data={'message': 'Propietario ya existe'})

        cursor.execute("""
            INSERT INTO Propietarios (nombre, telefono, email, notas)
            VALUES (%s, %s, %s, %s)
        """, (nombre, telefono, email, notas))

        connection.commit()

        logger.info(f"Propietario creado: {nombre}")

        return api_response('S002', status='success', data={'propietario_id': cursor.lastrowid})

    except Exception as e:
        logger.error(f"Error creando propietario: {e}")
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


@owners_bp.route('/api/propietarios/<int:propietario_id>', methods=['PUT'])
@handle_api_errors
@require_login(['admin'])
@validate_required_fields(['nombre'])
def actualizar_propietario(propietario_id):
    """Actualizar un propietario existente"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        nombre   = data['nombre']
        telefono = data.get('telefono', '')
        email    = data.get('email', '')
        notas    = data.get('notas', '')

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("SELECT id FROM Propietarios WHERE id = %s", (propietario_id,))
        if not cursor.fetchone():
            return api_response('E002', http_status=404, data={'propietario_id': propietario_id})

        cursor.execute("SELECT id FROM Propietarios WHERE nombre = %s AND id != %s", (nombre, propietario_id))
        if cursor.fetchone():
            return api_response('E007', http_status=400, data={'message': 'Nombre de propietario ya existe'})

        cursor.execute("""
            UPDATE Propietarios
            SET nombre = %s, telefono = %s, email = %s, notas = %s
            WHERE id = %s
        """, (nombre, telefono, email, notas, propietario_id))

        connection.commit()

        logger.info(f"Propietario actualizado: {nombre} (ID: {propietario_id})")

        return api_response('S003', status='success')

    except Exception as e:
        logger.error(f"Error actualizando propietario: {e}")
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


@owners_bp.route('/api/propietarios/<int:propietario_id>', methods=['DELETE'])
@handle_api_errors
@require_login(['admin'])
def eliminar_propietario(propietario_id):
    """Eliminar un propietario"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("SELECT nombre FROM Propietarios WHERE id = %s", (propietario_id,))
        propietario = cursor.fetchone()
        if not propietario:
            return api_response('E002', http_status=404, data={'propietario_id': propietario_id})

        cursor.execute(
            "SELECT COUNT(*) as count FROM MaquinaPropietario WHERE propietario_id = %s",
            (propietario_id,)
        )
        maquinas_count = cursor.fetchone()['count']

        if maquinas_count > 0:
            return api_response(
                'W006',
                status='warning',
                http_status=400,
                data={
                    'message': f'Propietario tiene {maquinas_count} máquinas asociadas',
                    'maquinas_count': maquinas_count
                }
            )

        cursor.execute("DELETE FROM Propietarios WHERE id = %s", (propietario_id,))
        connection.commit()

        logger.info(f"Propietario eliminado: {propietario['nombre']} (ID: {propietario_id})")

        return api_response('S004', status='success')

    except Exception as e:
        logger.error(f"Error eliminando propietario: {e}")
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()
