import json
import logging
import re

from flask import Blueprint, request, jsonify

from config import LOGGER_NAME
from database import get_db_connection, get_db_cursor
from utils.auth import require_login
from utils.responses import api_response, handle_api_errors
from utils.timezone import get_colombia_time

logger = logging.getLogger(LOGGER_NAME)

roles_bp = Blueprint('roles', __name__)


@roles_bp.route('/api/roles/sistema', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_roles_sistema():
    """Obtener todos los roles del sistema"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)
        cursor.execute("""
            SELECT
                r.id, r.nombre, r.descripcion, r.color, r.icono,
                r.nivel_acceso, r.permisos, r.activo,
                COUNT(u.id) as total_usuarios
            FROM roles r
            LEFT JOIN users u ON u.role = r.id
            GROUP BY r.id
            ORDER BY r.createdAt
        """)
        roles = cursor.fetchall()

        for rol in roles:
            if rol['permisos'] and isinstance(rol['permisos'], str):
                rol['permisos'] = json.loads(rol['permisos'])

        return jsonify({
            'roles': roles,
            'total_roles': len(roles),
            'timestamp': get_colombia_time().isoformat()
        })

    except Exception as e:
        logger.error(f"Error obteniendo roles: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


@roles_bp.route('/api/roles/agregar-automatico', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
def agregar_nuevo_rol_automatico():
    """Crear un nuevo rol automáticamente"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        nuevo_rol    = data.get('nuevo_rol', '').strip().lower()
        nombre       = data.get('nombre', nuevo_rol.capitalize().replace('_', ' '))
        descripcion  = data.get('descripcion', '')
        nivel_acceso = data.get('nivel_acceso', 'bajo')
        permisos     = data.get('permisos', [])
        color        = data.get('color', 'gray')
        icono        = data.get('icono', 'user')

        if not nuevo_rol:
            return api_response('E005', http_status=400, data={'message': 'Nombre del rol requerido'})
        if not re.match(r'^[a-z_]+$', nuevo_rol):
            return api_response('E005', http_status=400, data={'message': 'Solo letras minúsculas y guiones bajos'})
        if len(nuevo_rol) > 50:
            return api_response('E005', http_status=400, data={'message': 'Máximo 50 caracteres'})

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("SELECT id FROM roles WHERE id = %s", (nuevo_rol,))
        if cursor.fetchone():
            return api_response('E007', http_status=400, data={'message': 'El rol ya existe'})

        cursor.execute("""
            INSERT INTO roles (id, nombre, descripcion, color, icono, nivel_acceso, permisos)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            nuevo_rol,
            nombre,
            descripcion or f'Rol {nombre}',
            color,
            icono,
            nivel_acceso,
            json.dumps(permisos)
        ))

        connection.commit()
        logger.info(f"Rol creado: {nuevo_rol}")

        return jsonify({
            'success': True,
            'rol': {
                'id': nuevo_rol,
                'nombre': nombre,
                'descripcion': descripcion,
                'nivel_acceso': nivel_acceso,
                'permisos': permisos
            },
            'message': f'Rol "{nombre}" creado exitosamente'
        })

    except Exception as e:
        logger.error(f"Error creando rol: {e}")
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


@roles_bp.route('/api/roles/<rol_id>', methods=['DELETE'])
@handle_api_errors
@require_login(['admin'])
def eliminar_rol(rol_id):
    """Eliminar un rol (no permite eliminar 'admin')"""
    roles_protegidos = ['admin']
    if rol_id in roles_protegidos:
        return api_response('E005', http_status=400, data={'message': 'El rol administrador no se puede eliminar'})

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("SELECT COUNT(*) as total FROM users WHERE role = %s", (rol_id,))
        total = cursor.fetchone()['total']
        if total > 0:
            return api_response(
                'E005',
                http_status=400,
                data={'message': f'Hay {total} usuarios con este rol. Reasígnalos primero.'}
            )

        cursor.execute("DELETE FROM roles WHERE id = %s", (rol_id,))
        connection.commit()

        return api_response('S004', status='success')

    except Exception as e:
        logger.error(f"Error eliminando rol: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()
