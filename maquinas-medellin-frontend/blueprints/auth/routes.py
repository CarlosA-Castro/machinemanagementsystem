import logging

import sentry_sdk
from flask import Blueprint, request, jsonify, session, redirect, render_template
from werkzeug.security import check_password_hash, generate_password_hash

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


@auth_bp.route('/api/public/promedios')
def public_promedios():
    """Estadísticas anonimizadas para el simulador de inversión (sin autenticación)."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Promedio de turnos jugados por máquina por mes (últimos 3 meses)
        cur.execute("""
            SELECT COALESCE(AVG(t.turnos_mes), 0) AS avg_turnos
            FROM (
                SELECT machineId,
                       DATE_FORMAT(usedAt, '%%Y-%%m') AS mes,
                       COUNT(*) AS turnos_mes
                FROM turnusage
                WHERE usedAt >= DATE_SUB(NOW(), INTERVAL 3 MONTH)
                GROUP BY machineId, mes
            ) t
        """)
        row = cur.fetchone()
        avg_turnos = float(row['avg_turnos'] if row else 0) or 120.0

        # Precio promedio por turno (de paquetes activos, excluyendo paquete free)
        cur.execute("""
            SELECT COALESCE(AVG(price / NULLIF(turns, 0)), 0) AS avg_precio_turno
            FROM turnpackage
            WHERE id != 1 AND turns > 0 AND price > 0
        """)
        row = cur.fetchone()
        avg_precio_turno = float(row['avg_precio_turno'] if row else 0) or 3000.0

        # % utilidad promedio: 100 - negocio - admin
        try:
            cur.execute("""
                SELECT COALESCE(
                    AVG(100 - COALESCE(porcentaje_restaurante, 35) - COALESCE(porcentaje_admin, 25)),
                    40
                ) AS avg_pct_util
                FROM maquinaporcentajerestaurante
            """)
            row = cur.fetchone()
            avg_pct_util = float(row['avg_pct_util'] if row else 0) or 40.0
        except Exception:
            avg_pct_util = 40.0

        cur.close()
        conn.close()

        return jsonify({
            'avg_turnos_mes':    round(avg_turnos, 1),
            'avg_precio_turno':  round(avg_precio_turno, 0),
            'avg_pct_util':      round(avg_pct_util, 1),
        })
    except Exception as e:
        logger.error('public_promedios error: %s', e)
        return jsonify({
            'avg_turnos_mes':   120.0,
            'avg_precio_turno': 3000.0,
            'avg_pct_util':     40.0,
        })


@auth_bp.route('/api/contacto-inversor', methods=['POST'])
def contacto_inversor():
    """Recibe formulario de contacto de posibles inversionistas."""
    try:
        data = request.get_json(silent=True) or {}
        nombre   = (data.get('nombre') or '').strip()[:120]
        whatsapp = (data.get('whatsapp') or '').strip()[:30]
        email    = (data.get('email') or '').strip()[:120] or None
        maquinas = (data.get('maquinas_interes') or '1').strip()[:20]
        mensaje  = (data.get('mensaje') or '').strip()[:1000] or None

        if not nombre or not whatsapp:
            return jsonify({'ok': False, 'error': 'Nombre y WhatsApp son requeridos'}), 400

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO contacto_inversor
               (nombre, whatsapp, email, maquinas_interes, mensaje)
               VALUES (%s, %s, %s, %s, %s)""",
            (nombre, whatsapp, email, maquinas, mensaje),
        )
        conn.commit()
        cur.close()
        conn.close()
        logger.info('Nuevo contacto inversor: %s (%s)', nombre, whatsapp)
        return jsonify({'ok': True})
    except Exception as e:
        logger.error('contacto_inversor error: %s', e)
        return jsonify({'ok': False, 'error': 'Error al guardar. Intenta de nuevo.'}), 500


@auth_bp.route('/login', methods=['GET'])
def mostrar_login():
    session.clear()
    return render_template('login.html')


@auth_bp.route('/login', methods=['POST'])
@handle_api_errors
def procesar_login():
    """Procesa el login del usuario con nombre + contraseña."""
    connection = None
    cursor = None
    try:
        data     = request.get_json()
        nombre   = (data.get('nombre') or '').strip().upper()
        password = (data.get('password') or '').strip()

        if not nombre or not password:
            return jsonify({'valido': False, 'error': 'Nombre y contraseña requeridos'}), 400

        connection = get_db_connection()
        if not connection:
            return jsonify({'valido': False, 'error': 'Error de conexión a BD'}), 500

        cursor = get_db_cursor(connection)
        cursor.execute("SELECT * FROM users WHERE name = %s", (nombre,))
        usuario = cursor.fetchone()

        if not usuario:
            return jsonify({'valido': False, 'error': 'Usuario o contraseña incorrectos'}), 401

        # Verificar contraseña: hash (nuevo) o texto plano con migración lazy (legacy)
        password_hash = usuario.get('password_hash')
        password_plain = usuario.get('password', '')

        if password_hash:
            autenticado = check_password_hash(password_hash, password)
        else:
            autenticado = (password == password_plain)
            if autenticado:
                # Migrar a hash en el primer login exitoso
                nuevo_hash = generate_password_hash(password)
                try:
                    cursor.execute(
                        "UPDATE users SET password_hash = %s WHERE id = %s",
                        (nuevo_hash, usuario['id'])
                    )
                    connection.commit()
                except Exception as mig_err:
                    logger.warning(f"No se pudo migrar hash para {nombre}: {mig_err}")

        if not autenticado:
            logger.warning(f"Contraseña incorrecta para usuario: {nombre}")
            return jsonify({'valido': False, 'error': 'Usuario o contraseña incorrectos'}), 401

        # Bloquear usuarios desactivados antes de cualquier operación de sesión
        if not usuario.get('isActive', True):
            logger.warning(f"Intento de login de usuario inactivo: {usuario.get('name')}")
            return jsonify({'valido': False, 'error': 'Usuario inactivo. Contacte al administrador.'}), 403

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


def _local_activo() -> str:
    """Nombre del local activo en sesión, con fallback a user_local."""
    return (
        session.get('active_location_name')
        or session.get('user_local', 'El Mekatiadero')
    )


@auth_bp.route('/local')
def mostrar_local():
    if not session.get('logged_in'):
        return redirect('/login')
    hora_colombia = get_colombia_time()
    return render_template(
        'local.html',
        nombre_usuario=session.get('user_name', 'Usuario'),
        local_usuario=_local_activo(),
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
        local_usuario=_local_activo(),
    )


@auth_bp.route('/package/failure')
def mostrar_package_failure():
    if not session.get('logged_in'):
        return redirect('/login')
    return render_template(
        'packfailure.html',
        nombre_usuario=session.get('user_name', 'Usuario'),
        local_usuario=_local_activo(),
    )


@auth_bp.route('/machinereport')
def mostrar_machine_report():
    if not session.get('logged_in'):
        return redirect('/login')
    return render_template(
        'machinereport.html',
        nombre_usuario=session.get('user_name', 'Usuario'),
        local_usuario=_local_activo(),
    )


@auth_bp.route('/sales')
def mostrar_sales():
    if not session.get('logged_in'):
        return redirect('/login')
    return render_template(
        'sales.html',
        nombre_usuario=session.get('user_name', 'Usuario'),
        local_usuario=_local_activo(),
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
