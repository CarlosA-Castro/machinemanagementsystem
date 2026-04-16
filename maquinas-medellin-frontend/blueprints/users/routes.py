import logging

import sentry_sdk
from flask import Blueprint, request, jsonify, session

from config import LOGGER_NAME
from database import get_db_connection, get_db_cursor
from utils.auth import require_login
from utils.responses import api_response, handle_api_errors
from utils.validators import validate_required_fields
from utils.location_scope import apply_location_filter, get_active_location, user_can_view_all

logger = logging.getLogger(LOGGER_NAME)

users_bp = Blueprint('users', __name__)


# ── Debug ─────────────────────────────────────────────────────────────────────

@users_bp.route('/debug/usuarios')
@require_login(['admin'])
def debug_usuarios():
    """Debug: ver usuarios en formato crudo."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'No connection'}), 500

        cursor = get_db_cursor(connection)
        cursor.execute("""
            SELECT u.*, creador.name as creador_nombre
            FROM users u
            LEFT JOIN users creador ON u.createdBy = creador.id
            ORDER BY u.createdAt DESC
        """)
        usuarios = cursor.fetchall()

        usuarios_fmt = []
        for u in usuarios:
            d = dict(u)
            for k, v in d.items():
                if hasattr(v, 'isoformat'):
                    d[k] = v.isoformat()
            usuarios_fmt.append(d)

        return jsonify({'count': len(usuarios_fmt), 'data': usuarios_fmt})

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


# ── CRUD usuarios ─────────────────────────────────────────────────────────────

@users_bp.route('/api/usuarios', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_usuarios():
    """Obtener todos los usuarios."""
    logger.info(f"API Usuarios llamada por: {session.get('user_name')}")
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        # Filtrar usuarios por local activo (socios no tienen location_id, se excluyen del filtro)
        active_id, _ = get_active_location()
        can_all = user_can_view_all()
        if can_all and active_id is None:
            loc_clause = ""
            loc_params = []
        else:
            eff = active_id if active_id is not None else -1
            loc_clause = "AND (u.location_id = %s OR u.role = 'socio')"
            loc_params  = [eff]

        cursor.execute(f"""
            SELECT
                u.*,
                creador.name as creador_nombre,
                COALESCE(u.isActive, TRUE) as isActive
            FROM users u
            LEFT JOIN users creador ON u.createdBy = creador.id
            WHERE 1=1 {loc_clause}
            ORDER BY u.createdAt DESC
        """, loc_params)
        usuarios = cursor.fetchall()

        resultado = []
        for usuario in usuarios:
            is_active = usuario.get('isActive', True)
            if is_active is None:
                is_active = True
            resultado.append({
                'id':        usuario['id'],
                'name':      usuario['name'],
                'role':      usuario['role'],
                'local':     usuario.get('local', 'El Mekatiadero'),
                'createdBy': usuario['createdBy'],
                'creador':   {'name': usuario['creador_nombre']} if usuario['creador_nombre'] else None,
                'createdAt': usuario['createdAt'].isoformat() if usuario.get('createdAt') else None,
                'notes':     usuario.get('notes', ''),
                'isActive':  is_active,
            })

        return jsonify(resultado)

    except Exception as e:
        logger.error(f"Error obteniendo usuarios: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


@users_bp.route('/api/usuarios/<int:usuario_id>', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_usuario(usuario_id):
    """Obtener un usuario específico."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)
        cursor.execute("SELECT * FROM users WHERE id = %s", (usuario_id,))
        usuario = cursor.fetchone()

        if not usuario:
            return api_response('U001', http_status=404, data={'usuario_id': usuario_id})

        return jsonify({
            'id':        usuario['id'],
            'name':      usuario['name'],
            'role':      usuario['role'],
            'createdBy': usuario['createdBy'],
            'createdAt': usuario['createdAt'],
            'notes':     usuario['notes'],
        })

    except Exception as e:
        logger.error(f"Error obteniendo usuario: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


@users_bp.route('/api/usuarios', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
@validate_required_fields(['name', 'password', 'role'])
def crear_usuario():
    connection = None
    cursor = None
    try:
        data     = request.get_json()
        name     = data['name']
        password = data['password']
        role     = data['role']
        notes    = data.get('notes', '')

        if len(password) < 6:
            return api_response('U003', http_status=400)

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("SELECT id FROM roles WHERE id = %s AND activo = TRUE", (role,))
        if not cursor.fetchone():
            return api_response('U004', http_status=400, data={'message': 'Rol no válido'})

        cursor.execute("SELECT id FROM users WHERE name = %s", (name,))
        if cursor.fetchone():
            return api_response('U002', http_status=400, data={'name': name})

        cursor.execute(
            "INSERT INTO users (name, password, role, createdBy, notes) VALUES (%s, %s, %s, %s, %s)",
            (name, password, role, session.get('user_id'), notes)
        )
        connection.commit()
        logger.info(f"Usuario creado: {name} ({role})")

        return api_response('S002', status='success', data={'usuario_id': cursor.lastrowid})

    except Exception as e:
        logger.error(f"Error creando usuario: {e}")
        if connection: connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


@users_bp.route('/api/usuarios/<int:usuario_id>', methods=['PUT'])
@handle_api_errors
@require_login(['admin'])
@validate_required_fields(['name', 'role'])
def actualizar_usuario(usuario_id):
    connection = None
    cursor = None
    try:
        data      = request.get_json()
        name      = data['name']
        password  = data.get('password')
        role      = data['role']
        notes     = data.get('notes')
        isActive  = data.get('isActive')

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("SELECT id FROM roles WHERE id = %s AND activo = TRUE", (role,))
        if not cursor.fetchone():
            return api_response('U004', http_status=400, data={'message': 'Rol no válido'})

        cursor.execute("SELECT id FROM users WHERE id = %s", (usuario_id,))
        if not cursor.fetchone():
            return api_response('U001', http_status=404, data={'usuario_id': usuario_id})

        cursor.execute("SELECT id FROM users WHERE name = %s AND id != %s", (name, usuario_id))
        if cursor.fetchone():
            return api_response('U002', http_status=400, data={'name': name})

        if password:
            cursor.execute(
                "UPDATE users SET name=%s, password=%s, role=%s, notes=%s, isActive=%s WHERE id=%s",
                (name, password, role, notes, isActive, usuario_id)
            )
        else:
            cursor.execute(
                "UPDATE users SET name=%s, role=%s, notes=%s, isActive=%s WHERE id=%s",
                (name, role, notes, isActive, usuario_id)
            )

        connection.commit()
        logger.info(f"Usuario actualizado: {name} (ID: {usuario_id})")
        return api_response('S003', status='success')

    except Exception as e:
        logger.error(f"Error actualizando usuario: {e}")
        if connection: connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


@users_bp.route('/api/usuarios/<int:usuario_id>', methods=['DELETE'])
@handle_api_errors
@require_login(['admin'])
def eliminar_usuario(usuario_id):
    """Eliminar un usuario. No permite auto-eliminación."""
    if usuario_id == session.get('user_id'):
        return api_response('U005', http_status=400)

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("SELECT name FROM users WHERE id = %s", (usuario_id,))
        usuario = cursor.fetchone()
        if not usuario:
            return api_response('U001', http_status=404, data={'usuario_id': usuario_id})

        cursor.execute("DELETE FROM users WHERE id = %s", (usuario_id,))
        connection.commit()

        logger.info(f"Usuario eliminado: {usuario['name']} (ID: {usuario_id})")
        return api_response('S004', status='success')

    except Exception as e:
        logger.error(f"Error eliminando usuario: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()
