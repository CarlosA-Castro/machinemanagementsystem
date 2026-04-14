import logging

import sentry_sdk
from flask import Blueprint, request, jsonify, session, redirect, render_template

from config import LOGGER_NAME
from database import get_db_connection, get_db_cursor
from utils.responses import handle_api_errors
from utils.timezone import get_colombia_time
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

            logger.info(f"Usuario {usuario['name']} inició sesión")

            return jsonify({
                'valido':      True,
                'nombre':      usuario.get('name', 'Usuario'),
                'role':        usuario.get('role', 'Cajero'),
                'local':       usuario.get('local', 'El Mekatiadero'),
                'user_id':     usuario['id'],
                'redirect_to': 'socios' if usuario.get('role') == 'socio' else None,
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
