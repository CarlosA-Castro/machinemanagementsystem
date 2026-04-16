import logging

import sentry_sdk
from flask import Blueprint, request, jsonify, session, redirect, render_template

from config import LOGGER_NAME
from database import get_db_connection, get_db_cursor
from utils.responses import handle_api_errors
from utils.timezone import get_colombia_time
from utils.location_scope import (
    build_user_location_context,
    save_location_context_to_session,
    get_location_context_for_frontend,
    enforce_location_scope,
    set_active_location,
)
from datetime import datetime

logger = logging.getLogger(LOGGER_NAME)

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/')
def landing():
    return render_template('landing.html')


@auth_bp.route('/login', methods=['GET'])
def mostrar_login():
    session.clear()
    return render_template('login.html')


@auth_bp.route('/login', methods=['POST'])
@handle_api_errors
def procesar_login():
    """Procesa el login del usuario."""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        codigo = data.get('codigo')

        if not codigo:
            return jsonify({'valido': False, 'error': 'Código requerido'}), 400

        connection = get_db_connection()
        if not connection:
            return jsonify({'valido': False, 'error': 'Error de conexión a BD'}), 500

        cursor = get_db_cursor(connection)
        cursor.execute("SELECT * FROM users WHERE password = %s", (codigo,))
        usuario = cursor.fetchone()

        if usuario:
            session['user_id']    = usuario['id']
            session['user_name']  = usuario['name']
            session['user_role']  = usuario['role']
            session['user_local'] = usuario.get('local', 'El Mekatiadero')
            session['logged_in']  = True
            session['last_activity'] = datetime.utcnow().isoformat()
            session.permanent = True
            session.modified  = True

            # Construir y guardar contexto de local en sesión
            loc_ctx = build_user_location_context(usuario, cursor)
            save_location_context_to_session(loc_ctx)

            logger.info(f"Usuario {usuario['name']} inició sesión")

            role = usuario.get('role', '')

            # socios: flujo propio, sin selector de local
            if role == 'socio':
                return jsonify({
                    'valido':               True,
                    'nombre':               usuario.get('name', 'Usuario'),
                    'role':                 role,
                    'local':                usuario.get('local', ''),
                    'user_id':              usuario['id'],
                    'redirect_to':          'socios',
                    'needs_location_select': False,
                })

            # Determinar si hay que mostrar el selector de local
            needs_select = loc_ctx['can_switch_location'] and len(loc_ctx['allowed_location_ids']) > 1

            # Si solo hay un local disponible para el admin, seleccionarlo automáticamente
            if loc_ctx['can_switch_location'] and len(loc_ctx['allowed_location_ids']) == 1:
                only_id = loc_ctx['allowed_location_ids'][0]
                try:
                    cursor.execute("SELECT name FROM location WHERE id = %s", (only_id,))
                    row = cursor.fetchone()
                    only_name = row['name'] if row else ''
                except Exception:
                    only_name = ''
                set_active_location(only_id, only_name)
                needs_select = False

            # Si el rol fijo no tiene local asignado: bloquear login
            if role in ('cajero', 'admin_restaurante') and not loc_ctx['assigned_location_id']:
                session.clear()
                return jsonify({
                    'valido': False,
                    'error':  'Tu usuario no está asignado a ningún local. Contacta al administrador.',
                }), 403

            locales_disponibles = []
            if needs_select:
                try:
                    cursor.execute(
                        "SELECT id, name FROM location WHERE status = 'activo' ORDER BY name"
                    )
                    locales_disponibles = [{'id': r['id'], 'name': r['name']} for r in cursor.fetchall()]
                except Exception as e:
                    logger.error(f"Error cargando locales para selector: {e}")

            return jsonify({
                'valido':                True,
                'nombre':                usuario.get('name', 'Usuario'),
                'role':                  role,
                'local':                 usuario.get('local', ''),
                'user_id':               usuario['id'],
                'redirect_to':           None,
                'needs_location_select': needs_select,
                'locales_disponibles':   locales_disponibles,
            })
        else:
            return jsonify({'valido': False, 'error': 'Código inválido'}), 401

    except Exception as e:
        logger.error(f"Error en login: {e}")
        return jsonify({'valido': False, 'error': f'Error interno: {str(e)}'}), 500
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


@auth_bp.route('/test-db')
def test_db():
    """Prueba la conexión a BD (utilitario)."""
    try:
        connection = get_db_connection()
        if connection:
            cursor = get_db_cursor(connection)
            cursor.execute("SELECT COUNT(*) as count FROM users")
            resultado = cursor.fetchone()
            cursor.close()
            connection.close()
            return f"Conexión exitosa. Usuarios en BD: {resultado['count']}"
        return "No se pudo conectar a la BD"
    except Exception as e:
        return f"Error: {str(e)}"


@auth_bp.route('/local')
def mostrar_local():
    if not session.get('logged_in'):
        return redirect('/login')
    hora_colombia = get_colombia_time()
    return render_template(
        'local.html',
        nombre_usuario=session.get('user_name', 'Usuario'),
        local_usuario=session.get('user_local', 'El Mekatiadero'),
        hora_actual=hora_colombia.strftime('%H:%M:%S'),
        fecha_actual=hora_colombia.strftime('%Y-%m-%d'),
    )


@auth_bp.route('/package')
def mostrar_package():
    if not session.get('logged_in'):
        return redirect('/login')
    return render_template(
        'package.html',
        nombre_usuario=session.get('user_name', 'Usuario'),
        local_usuario=session.get('user_local', 'El Mekatiadero'),
    )


@auth_bp.route('/package/failure')
def mostrar_package_failure():
    if not session.get('logged_in'):
        return redirect('/login')
    return render_template(
        'packfailure.html',
        nombre_usuario=session.get('user_name', 'Usuario'),
        local_usuario=session.get('user_local', 'El Mekatiadero'),
    )


@auth_bp.route('/machinereport')
def mostrar_machine_report():
    if not session.get('logged_in'):
        return redirect('/login')
    return render_template(
        'machinereport.html',
        nombre_usuario=session.get('user_name', 'Usuario'),
        local_usuario=session.get('user_local', 'El Mekatiadero'),
    )


@auth_bp.route('/sales')
def mostrar_sales():
    if not session.get('logged_in'):
        return redirect('/login')
    return render_template(
        'sales.html',
        nombre_usuario=session.get('user_name', 'Usuario'),
        local_usuario=session.get('user_local', 'El Mekatiadero'),
    )


@auth_bp.route('/logout')
def logout():
    usuario = session.get('user_name', 'Usuario')
    session.clear()
    logger.info(f"Usuario {usuario} cerró sesión")
    return redirect('/login')


@auth_bp.route('/Login.html')
def redirect_login():
    return redirect('/login')


# ── Endpoints de contexto de local ───────────────────────────────────────────

@auth_bp.route('/api/session/contexto-local')
def api_contexto_local():
    """Retorna el contexto de local activo de la sesión actual."""
    if not session.get('logged_in'):
        return jsonify({'error': 'No autenticado'}), 401
    return jsonify(get_location_context_for_frontend())


@auth_bp.route('/api/session/locales-disponibles')
def api_locales_disponibles():
    """Retorna los locales que el usuario actual puede seleccionar."""
    if not session.get('logged_in'):
        return jsonify({'error': 'No autenticado'}), 401

    can_switch = session.get('can_switch_location', False)
    assigned_id = session.get('assigned_location_id')

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión'}), 500
        cursor = get_db_cursor(connection)

        if can_switch:
            cursor.execute("SELECT id, name FROM location WHERE status = 'activo' ORDER BY name")
        elif assigned_id:
            cursor.execute("SELECT id, name FROM location WHERE id = %s", (assigned_id,))
        else:
            return jsonify({'locales': []})

        locales = [{'id': r['id'], 'name': r['name']} for r in cursor.fetchall()]
        return jsonify({'locales': locales})

    except Exception as e:
        logger.error(f"Error en api_locales_disponibles: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


@auth_bp.route('/api/session/seleccionar-local', methods=['POST'])
@handle_api_errors
def api_seleccionar_local():
    """
    Establece el local activo en sesión.
    Body: { "location_id": 2 }  — null para modo "todos los locales"
    Solo roles con can_switch_location pueden usar este endpoint.
    """
    if not session.get('logged_in'):
        return jsonify({'error': 'No autenticado'}), 401

    data = request.get_json() or {}
    location_id = data.get('location_id')  # None = ver todos

    # Validar alcance antes de aplicar
    enforce_location_scope(location_id)

    location_name = None
    if location_id is not None:
        connection = None
        cursor = None
        try:
            connection = get_db_connection()
            cursor = get_db_cursor(connection)
            cursor.execute("SELECT name FROM location WHERE id = %s AND status = 'activo'", (location_id,))
            row = cursor.fetchone()
            if not row:
                return jsonify({'error': 'Local no encontrado o inactivo'}), 404
            location_name = row['name']
        except Exception as e:
            logger.error(f"Error resolviendo nombre de local: {e}")
            return jsonify({'error': str(e)}), 500
        finally:
            if cursor:     cursor.close()
            if connection: connection.close()

    set_active_location(location_id, location_name)
    logger.info(
        f"Usuario {session.get('user_name')} cambió local activo → "
        f"{location_name or 'Todos los locales'}"
    )

    return jsonify({
        'ok':                  True,
        'active_location_id':  location_id,
        'active_location_name': location_name,
        'redirect_to':         '/local',
    })
