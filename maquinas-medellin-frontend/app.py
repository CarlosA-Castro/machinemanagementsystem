from flask import Flask, json, request, jsonify, render_template, redirect, url_for, session, send_file, g
import time
import mysql.connector
from mysql.connector import pooling
from flask_cors import CORS
import os
from datetime import datetime, timedelta
import pytz
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration
import logging
from logging.handlers import RotatingFileHandler
from functools import lru_cache, wraps
import re
import io
import csv
import zipfile
import traceback
from factory import create_app
from utils.transactions import log_transaction as shared_log_transaction
from utils.logs import (
    log_app_event as shared_log_app_event,
    log_error as shared_log_error,
    update_daily_statistics as shared_update_daily_statistics,
    check_alerts as shared_check_alerts,
    log_info as shared_log_info,
    log_warning as shared_log_warning,
    log_error_system as shared_log_error_system,
    log_user_action as shared_log_user_action,
    log_system_event as shared_log_system_event,
)

#  CONFIGURACION DE ZONA HORARIA 

COLOMBIA_TZ = pytz.timezone('America/Bogota')

def get_colombia_time():
    """Obtiene la hora actual en Colombia"""
    return datetime.now(COLOMBIA_TZ)

def format_datetime_for_db(dt):
    """Formatea datetime para guardar en BD (sin timezone)"""
    return dt.strftime('%Y-%m-%d %H:%M:%S')

def parse_db_datetime(dt_str):
    """Convierte string de BD a datetime con timezone Colombia"""
    if not dt_str:
        return None
    naive_dt = datetime.strptime(str(dt_str), '%Y-%m-%d %H:%M:%S')
    return COLOMBIA_TZ.localize(naive_dt)

# La app real se crea desde el factory para centralizar configuracion,
# logging y middleware sin romper las rutas legacy que aun viven aqui.
app = create_app()
SESSION_TIMEOUT = timedelta(hours=8)

# ============================================================
# ESTADO EN MEMORIA â€” HEARTBEATS ESP32
# Clave: machine_id (int)  Valor: {wifi, server, rssi, ts}
# No persiste entre reinicios del servidor (comportamiento correcto:
# si el servidor reinicia, los ESP32 vuelven a enviar heartbeat en segundos)
# ============================================================
import time as _time
_esp32_heartbeats: dict = {}   # { machine_id: {wifi, server, rssi, ts} }
_ESP32_ONLINE_TIMEOUT = 90    # segundos sin heartbeat â†’ considerado offline

def _parse_json_col(value, default):
    """Parsea una columna JSON que puede llegar como string o ya como objeto Python."""
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default

def _esp32_heartbeat_fields(machine_id: int) -> dict:
    """Devuelve los campos esp32_* para incluir en la respuesta de /api/maquinas."""
    hb = _esp32_heartbeats.get(int(machine_id))
    if hb and (_time.time() - hb['ts']) < _ESP32_ONLINE_TIMEOUT:
        return {
            'esp32_online': True,
            'esp32_wifi':   hb['wifi'],
            'esp32_server': hb['server'],
            'esp32_rssi':   hb['rssi'],
        }
    return {'esp32_online': False, 'esp32_wifi': False, 'esp32_server': False, 'esp32_rssi': 0}

# ============================================================
# LOGGING TRANSACCIONAL Y HOOKS DE REQUEST
# ============================================================

# Rutas que se omiten del access_log (ruido: polling, assets, health checks)
_SKIP_ACCESS_LOG = (
    '/static',
    '/favicon',
    '/api/logs',           # auto-refresh de la consola cada 5s
    '/api/esp32/check-commands',  # polling del ESP32 cada pocos segundos
    '/api/esp32/heartbeat',       # heartbeat del ESP32 cada 30s
    '/api/esp32/status',
    '/api/tft/',
)

def _log_transaccion(tipo, descripcion, categoria='operacional', usuario=None, usuario_id=None,
                     maquina_id=None, maquina_nombre=None, entidad=None, entidad_id=None,
                     monto=None, datos_extra=None, estado='ok'):
    """Wrapper legacy hacia el helper compartido de transacciones."""
    shared_log_transaction(
        tipo=tipo,
        descripcion=descripcion,
        categoria=categoria,
        usuario=usuario,
        usuario_id=usuario_id,
        maquina_id=maquina_id,
        maquina_nombre=maquina_nombre,
        entidad=entidad,
        entidad_id=entidad_id,
        monto=monto,
        datos_extra=datos_extra,
        estado=estado,
    )


_SESSION_SKIP = {'mostrar_login', 'procesar_login', 'static', None}

# Registrado desde middleware/session.py en factory.py
# @app.before_request
def check_session_timeout():
    """Cierra sesiÃ³n automÃ¡ticamente tras 8 horas de inactividad."""
    if request.endpoint in _SESSION_SKIP:
        return
    if not session.get('logged_in'):
        return

    last = session.get('last_activity')
    if last:
        try:
            idle = datetime.utcnow() - datetime.fromisoformat(last)
            if idle > SESSION_TIMEOUT:
                session.clear()
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return jsonify({'error': 'session_expired', 'redirect': '/login'}), 401
                return redirect('/login')
        except Exception:
            pass

    session['last_activity'] = datetime.utcnow().isoformat()
    session.modified = True


# Registrado desde middleware/logging_mw.py en factory.py
# @app.before_request
def _before_request_log():
    g._req_start = time.time()


# Registrado desde middleware/logging_mw.py en factory.py
# @app.after_request
def _after_request_log(response):
    try:
        path = request.path
        if any(path.startswith(p) for p in _SKIP_ACCESS_LOG):
            return response

        duration_ms = int((time.time() - getattr(g, '_req_start', time.time())) * 1000)
        status = response.status_code
        method = request.method
        user_id = session.get('user_id')
        user_name = session.get('user_name', '-')
        ip = request.remote_addr

        # Log en consola EC2 con formato rico
        log_fn = app.logger.error if status >= 500 else app.logger.warning if status >= 400 else app.logger.info
        log_fn(f"[HTTP] {method} {path} â†’ {status} | {duration_ms}ms | {ip} | {user_name}")

        # Insertar en access_logs
        try:
            connection = get_db_connection()
            if connection:
                cur = connection.cursor()
                cur.execute("""
                    INSERT INTO access_logs
                        (method, path, status_code, response_time_ms, user_id, user_name, ip_address, user_agent)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (method, path[:500], status, duration_ms, user_id, user_name, ip,
                      (request.user_agent.string[:500] if request.user_agent else None)))
                connection.commit()
                cur.close()
                connection.close()
        except Exception:
            pass
    except Exception:
        pass
    return response


# CLASE DE SERVICIO DE MENSAJES

class MessageService:
    """Servicio para gestionar mensajes desde la base de datos"""
    _cache = {}
    
    @classmethod
    @lru_cache(maxsize=128)
    def get_message(cls, message_code: str, language_code: str = 'es', **kwargs) -> dict:
        """Obtiene un mensaje de la base de datos y aplica formato"""
        try:
            
            cache_key = f"{message_code}_{language_code}"

            if cache_key in cls._cache:
                message_data = cls._cache[cache_key]
            else:
                # ConexiÃ³n a la base de datos
                connection = cls._get_connection()
                if not connection:
                    return cls._get_default_message(message_code)
                
                cursor = connection.cursor(dictionary=True)
                
                query = """
                    SELECT message_code, message_type, message_text, language_code
                    FROM system_messages 
                    WHERE message_code = %s AND language_code = %s
                """
                cursor.execute(query, (message_code, language_code))
                message = cursor.fetchone()
                
                # Fallback a espaÃ±ol 
                if not message and language_code != 'es':
                    cursor.execute("""
                        SELECT message_code, message_type, message_text, language_code
                        FROM system_messages 
                        WHERE message_code = %s AND language_code = 'es'
                    """, (message_code,))
                    message = cursor.fetchone()
                
                cursor.close()
                connection.close()
                
                if not message:
                    return cls._get_default_message(message_code)
                
                message_data = {
                    'code': message['message_code'],
                    'type': message['message_type'],
                    'text': message['message_text'],
                    'language': message['language_code']
                }
                
                # Guardar en cache
                cls._cache[cache_key] = message_data
            
            # Formatear mensaje con variables
            formatted_text = message_data['text']
            if kwargs:
                try:
                    formatted_text = formatted_text.format(**kwargs)
                except (KeyError, ValueError) as e:
                    app.logger.warning(f"Error formateando mensaje {message_code}: {e}")
                    formatted_text = f"{formatted_text} [Error de formato: {e}]"
            
            message_data['formatted'] = formatted_text
            return message_data
            
        except Exception as e:
            app.logger.error(f"Error obteniendo mensaje {message_code}: {e}")
            return cls._get_default_message(message_code)
    
    @classmethod
    def get_error_message(cls, error_code: str, **kwargs) -> str:
        """Obtiene solo el texto formateado de un error"""
        message = cls.get_message(error_code, **kwargs)
        return message.get('formatted', f"Error: {error_code}")
    
    @classmethod
    def get_json_response(cls, message_code: str, status: str = 'error', 
                         data: dict = None, http_status: int = 200, **kwargs) -> tuple:
        """Crea una respuesta JSON estandarizada"""
        message = cls.get_message(message_code, **kwargs)
        
        response = {
            'status': status,
            'code': message_code,
            'message': message['formatted'],
            'message_type': message['type'],
            'timestamp': datetime.now().isoformat()
        }
        
        if data:
            response['data'] = data
        
        return response, http_status
    
    @classmethod
    def clear_cache(cls):
        """Limpia el cache"""
        cls._cache.clear()
        cls.get_message.cache_clear()
        app.logger.info("Cache de mensajes limpiado")
    
    @classmethod
    def _get_connection(cls):
        """Obtiene conexiÃ³n a la base de datos"""
        try:
            conn = mysql.connector.connect(
                host=os.getenv("DB_HOST", "mysql"),
    user=os.getenv("DB_USER", "myuser"),
    password=os.getenv("DB_PASSWORD", "mypassword"),
    database=os.getenv("DB_NAME", "maquinasmedellin"),
    port=3306,
    auth_plugin="mysql_native_password"
)
            return conn
        except Exception as e:
            app.logger.error(f"Error conectando a BD para mensajes: {e}")
            return None
    
    @classmethod
    def _get_default_message(cls, message_code: str) -> dict:
        """Mensajes por defecto si no se encuentran en la BD"""
        default_messages = {
            'E001': {'code': 'E001', 'type': 'error', 'text': 'Error interno del servidor'},
            'E002': {'code': 'E002', 'type': 'error', 'text': 'Recurso no encontrado'},
            'E003': {'code': 'E003', 'type': 'error', 'text': 'No autorizado'},
            'E004': {'code': 'E004', 'type': 'error', 'text': 'Acceso prohibido'},
            'E005': {'code': 'E005', 'type': 'error', 'text': 'ParÃ¡metros invÃ¡lidos'},
            'E006': {'code': 'E006', 'type': 'error', 'text': 'Error de conexiÃ³n a la base de datos'},
            'A001': {'code': 'A001', 'type': 'error', 'text': 'Credenciales invÃ¡lidas'},
            'S001': {'code': 'S001', 'type': 'success', 'text': 'OperaciÃ³n exitosa'},
        }
        
        message = default_messages.get(message_code, {
            'code': message_code,
            'type': 'error',
            'text': f'Mensaje no configurado: {message_code}'
        })
        
        message['formatted'] = message['text']
        return message

# DECORADORES Y UTILIDADES 

def api_response(message_code: str, status: str = 'error', 
                http_status: int = 200, data: dict = None, **kwargs):
    """
    Helper para respuestas API estandarizadas
    """
    return MessageService.get_json_response(
        message_code, status, data, http_status, **kwargs
    )

def handle_api_errors(func):
    """
    Decorador para manejar errores en endpoints API
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            app.logger.error(f"Error en {func.__name__}: {str(e)}", exc_info=True)
            
            # Determinar tipo de error
            if isinstance(e, ValueError):
                return api_response('E005', http_status=400)
            elif isinstance(e, KeyError):
                return api_response('E005', http_status=400)
            elif isinstance(e, PermissionError):
                return api_response('E004', http_status=403)
            elif "no encontrado" in str(e).lower() or isinstance(e, FileNotFoundError):
                return api_response('E002', http_status=404)
            elif "no autorizado" in str(e).lower():
                return api_response('E003', http_status=401)
            else:
                return api_response('E001', http_status=500)
    return wrapper

def require_login(roles=None):
    """
    Decorador para requerir autenticaciÃ³n.
    Si se pasan roles, acepta:
    - Roles exactos en la lista
    - Cualquier rol que tenga permiso 'admin_panel' (para rutas admin)
    - El rol 'supervisor' u otros custom si tienen los permisos necesarios
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not session.get('logged_in'):
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return api_response('A004', http_status=401)
                return redirect('/login')

            if roles:
                user_role = session.get('user_role')

                # Si el rol estÃ¡ directamente en la lista permitida, ok
                if user_role in roles:
                    return func(*args, **kwargs)

                # Si no estÃ¡, verificar permisos desde la tabla roles
                try:
                    connection = get_db_connection()
                    if connection:
                        cursor = get_db_cursor(connection)
                        cursor.execute(
                            "SELECT permisos FROM roles WHERE id = %s AND activo = TRUE",
                            (user_role,)
                        )
                        rol_data = cursor.fetchone()
                        cursor.close()
                        connection.close()

                        if rol_data:
                            permisos = rol_data['permisos']
                            if isinstance(permisos, str):
                                permisos = json.loads(permisos)

                            # admin_restaurante nunca obtiene acceso admin aunque tenga admin_panel
                            es_admin_restaurante = (user_role == 'admin_restaurante')

                            # Si la ruta requiere admin y el rol tiene admin_panel, permitir
                            # (excepto admin_restaurante que tiene los mismos permisos que cajero)
                            if 'admin' in roles and 'admin_panel' in permisos and not es_admin_restaurante:
                                return func(*args, **kwargs)

                            # Si la ruta requiere cajero y el rol tiene permiso 'ver', permitir
                            if 'cajero' in roles and 'ver' in permisos:
                                return func(*args, **kwargs)

                            # Si la ruta requiere admin_restaurante y tiene 'ver' o 'reportes', permitir
                            if 'admin_restaurante' in roles and ('ver' in permisos or 'reportes' in permisos):
                                return func(*args, **kwargs)

                except Exception as e:
                    app.logger.error(f"Error verificando permisos en require_login: {e}")

                return api_response('E004', http_status=403)

            return func(*args, **kwargs)
        return wrapper
    return decorator

def require_permission(permission):
    """Decorador para verificar permisos especÃ­ficos desde tabla roles"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not session.get('logged_in'):
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return api_response('E003', http_status=401)
                return redirect('/login')

            user_role = session.get('user_role')

            try:
                connection = get_db_connection()
                if not connection:
                    return api_response('E006', http_status=500)
                cursor = get_db_cursor(connection)
                cursor.execute("SELECT permisos FROM roles WHERE id = %s AND activo = TRUE", (user_role,))
                rol = cursor.fetchone()
                cursor.close()
                connection.close()

                if not rol:
                    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                        return api_response('E004', http_status=403)
                    return redirect('/login')

                permisos = rol['permisos']
                if isinstance(permisos, str):
                    permisos = json.loads(permisos)

                if permission not in permisos:
                    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                        return api_response('E004', http_status=403, data={'message': f'No tienes permiso: {permission}'})
                    return render_template('error_permiso.html',
                        nombre_usuario=session.get('user_name', ''),
                        permiso_requerido=permission
                    ) if False else redirect('/local')

            except Exception as e:
                app.logger.error(f"Error verificando permiso {permission}: {e}")
                return api_response('E001', http_status=500)

            return func(*args, **kwargs)
        return wrapper
    return decorator


def get_user_permissions():
    """Obtener permisos del usuario actual desde la BD"""
    try:
        user_role = session.get('user_role')
        if not user_role:
            return []
        connection = get_db_connection()
        if not connection:
            return []
        cursor = get_db_cursor(connection)
        cursor.execute("SELECT permisos FROM roles WHERE id = %s AND activo = TRUE", (user_role,))
        rol = cursor.fetchone()
        cursor.close()
        connection.close()
        if not rol:
            return []
        permisos = rol['permisos']
        if isinstance(permisos, str):
            permisos = json.loads(permisos)
        return permisos or []
    except Exception as e:
        app.logger.error(f"Error obteniendo permisos: {e}")
        return []

def validate_required_fields(required_fields):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            data = request.get_json(silent=True) or request.form

            missing_fields = []
            for field in required_fields:
                if field not in data or data[field] in [None, '', []]:
                    missing_fields.append(field)

            if missing_fields:
                return api_response(
                    'E005',
                    http_status=400,
                    data={'missing_fields': missing_fields}
                )

            return func(*args, **kwargs)
        return wrapper
    return decorator


# CONFIGURACIÃ“N DEL POOL DE CONEXIONES

try:
    db_config = {
        "host": os.getenv("DB_HOST", "mysql"),
    "user": os.getenv("DB_USER", "myuser"),
    "password": os.getenv("DB_PASSWORD", "mypassword"),
    "database": os.getenv("DB_NAME", "maquinasmedellin"),
    "port": 3306,
    "pool_name": "maquinas_pool",
    "pool_size": 5,
    "auth_plugin": "mysql_native_password"
    }

    app.logger.info("ðŸ”§ Intentando crear pool de conexiones...")
    app.logger.info(f"   Host: {db_config['host']}")
    app.logger.info(f"   User: {db_config['user']}")
    app.logger.info(f"   Database: {db_config['database']}")
    app.logger.info(f"   Port: {db_config['port']}")
    
    # Probar conexiÃ³n simple primero
    test_conn = mysql.connector.connect(
         host=os.getenv("DB_HOST", "mysql"),
    user=os.getenv("DB_USER", "myuser"),
    password=os.getenv("DB_PASSWORD", "mypassword"),
    database=os.getenv("DB_NAME", "maquinasmedellin"),
    port=3306,
    auth_plugin="mysql_native_password"
)
    app.logger.info(" ConexiÃ³n simple exitosa")
    test_conn.close()
    
    # Ahora intentar el pool
    connection_pool = pooling.MySQLConnectionPool(**db_config)
    app.logger.info(" Pool de conexiones creado exitosamente")
    
except mysql.connector.Error as e:
    app.logger.error(f" Error MySQL especÃ­fico: {e}")
    app.logger.error(f"   Error number: {e.errno}")
    app.logger.error(f"   SQL state: {e.sqlstate}")
    connection_pool = None
except Exception as e:
    app.logger.error(f" Error general creando pool: {e}")
    import traceback
    traceback.print_exc()
    connection_pool = None


def get_db_connection():
    try:
       
        connection = mysql.connector.connect(
             host=os.getenv("DB_HOST", "mysql"),
    user=os.getenv("DB_USER", "myuser"),
    password=os.getenv("DB_PASSWORD", "mypassword"),
    database=os.getenv("DB_NAME", "maquinasmedellin"),
    port=3306,
    auth_plugin="mysql_native_password"
)
        cursor = connection.cursor()
        cursor.execute("SET time_zone = '-05:00'")
        cursor.close()
        return connection
    except Exception as e:
        app.logger.error(f" Error obteniendo conexiÃ³n: {e}")
        import traceback
        traceback.print_exc()
        return None

def get_db_cursor(connection):
    try:
        cursor = connection.cursor(dictionary=True)
        return cursor
    except Exception as e:
        app.logger.error(f" Error obteniendo cursor: {e}")
        return None

# ── Blueprints migrados (Fase 2-3) ───────────────────────────────────────────
# Los blueprints activos se registran exclusivamente desde factory.py.

# RUTAS PENDIENTES DE MIGRAR (Fase 3+)

# auth routes â†’ blueprints/auth/routes.py

# QR routes → blueprints/qr/routes.py

# admin routes â†’ blueprints/admin/routes.py

# users routes â†’ blueprints/users/routes.py

# paquetes routes → blueprints/packages/routes.py

# locales routes → blueprints/locations/routes.py

# machine action routes → blueprints/machines/routes.py

# messages routes → blueprints/messages/routes.py

# dashboard routes → blueprints/dashboard/routes.py

# actualizar_contador_diario → blueprints/qr/routes.py

# propietarios routes → blueprints/owners/routes.py

# machine reports/stats routes → blueprints/machines/routes.py

# roles routes → blueprints/roles/routes.py

# ==================== RUTAS PARA SOCIOS ====================

# APIs de liquidaciones y reportes migradas a:
# - blueprints/liquidaciones/routes.py

# APIs de socios, inversiones y pagos migradas a:
# - blueprints/socios/routes.py
# - blueprints/inversiones/routes.py
# - blueprints/pagos/routes.py

# ==================== MIDDLEWARE PARA LOGGING ====================

# Registrado desde middleware/logging_mw.py en factory.py
# @app.before_request
def log_request_info():
    """Middleware para registrar informaciÃ³n de cada request"""
    try:
        if request.path.startswith('/static/'):
            return
            
        # Registrar en access_logs
        connection = get_db_connection()
        if connection:
            cursor = get_db_cursor(connection)
            
            start_time = datetime.now()
            
            # Almacenar para usar despuÃ©s del request
            request.start_time = start_time
            
            cursor.close()
            connection.close()
            
    except Exception as e:
        app.logger.debug(f"Error en log_request_info: {e}")

# Registrado desde middleware/logging_mw.py en factory.py
# @app.after_request
def log_response_info(response):
    """Middleware para registrar informaciÃ³n de cada response"""
    try:
        if request.path.startswith('/static/'):
            return response
            
        if hasattr(request, 'start_time'):
            response_time = (datetime.now() - request.start_time).total_seconds() * 1000
            
            connection = get_db_connection()
            if connection:
                cursor = get_db_cursor(connection)
                
                try:
                    cursor.execute("""
                        INSERT INTO access_logs 
                        (method, path, query_string, status_code, response_time_ms, 
                         ip_address, user_agent, user_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        request.method,
                        request.path,
                        request.query_string.decode('utf-8') if request.query_string else '',
                        response.status_code,
                        int(response_time),
                        request.remote_addr,
                        request.user_agent.string if request.user_agent else '',
                        session.get('user_id')
                    ))
                    
                    connection.commit()
                    
                    # Actualizar estadÃ­sticas diarias
                    update_daily_statistics()
                    
                except Exception as e:
                    app.logger.error(f"Error insertando access log: {e}")
                    connection.rollback()
                
                cursor.close()
                connection.close()
        
    except Exception as e:
        app.logger.debug(f"Error en log_response_info: {e}")
    
    return response

def log_app_event(level, message, module=None, user_id=None):
    """Wrapper legacy hacia el helper compartido de logs."""
    return shared_log_app_event(level, message, module, user_id=user_id)

def log_error(error_type, error_message, stack_trace=None, module=None, user_id=None):
    """Wrapper legacy hacia el helper compartido de logs."""
    return shared_log_error(error_type, error_message, stack_trace, module, user_id)

def update_daily_statistics():
    """Wrapper legacy hacia el helper compartido de logs."""
    return shared_update_daily_statistics()

def check_alerts(level, message, module):
    """Wrapper legacy hacia el helper compartido de logs."""
    return shared_check_alerts(level, message, module)

# ==================== APIS PARA LOGS ====================

# Migrado a blueprints/logs/routes.py
# @app.route('/api/logs/transaccional-consolidado', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_logs_transaccional_consolidado():
    """
    Endpoint consolidado para la pÃ¡gina de Logs Transaccionales.
    Retorna en una sola llamada: KPIs, ventas, fallas ESP32, por mÃ¡quina, grÃ¡fica, feed de actividad.
    """
    connection = None
    cursor = None
    try:
        hoy = get_colombia_time().strftime('%Y-%m-%d')
        fecha_inicio = request.args.get('fecha_inicio', hoy)
        fecha_fin = request.args.get('fecha_fin', hoy)
        limit_feed = int(request.args.get('limit', 100))

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)

        # â”€â”€ KPI 1-2: Ventas â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        cursor.execute("""
            SELECT
                COUNT(DISTINCT qh.qr_code)          AS paquetes_vendidos,
                COALESCE(SUM(tp.price), 0)           AS ingresos_ventas
            FROM qrhistory qh
            JOIN qrcode qr   ON qr.code = qh.qr_code
            JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
              AND qr.turnPackageId IS NOT NULL
              AND qr.turnPackageId != 1
              AND qh.es_venta_real = TRUE
        """, (fecha_inicio, fecha_fin))
        kpi_ventas = cursor.fetchone()

        # â”€â”€ KPI 3: Turnos jugados (ESP32) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        cursor.execute("""
            SELECT COUNT(*) AS turnos_jugados
            FROM turnusage
            WHERE DATE(usedAt) BETWEEN %s AND %s
        """, (fecha_inicio, fecha_fin))
        kpi_turnos = cursor.fetchone()

        # â”€â”€ KPI 4-5: Fallas ESP32 â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        cursor.execute("""
            SELECT
                COUNT(*)                          AS fallas_total,
                COALESCE(SUM(turnos_devueltos), 0) AS turnos_devueltos
            FROM machinefailures
            WHERE DATE(reported_at) BETWEEN %s AND %s
        """, (fecha_inicio, fecha_fin))
        kpi_fallas = cursor.fetchone()

        # â”€â”€ Ventas detalladas â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        cursor.execute("""
            SELECT
                qh.fecha_hora,
                qh.qr_code,
                COALESCE(qr.qr_name, qh.qr_code) AS qr_name,
                tp.name  AS paquete,
                tp.price AS precio,
                tp.turns AS turnos_paquete,
                qh.user_name AS cajero
            FROM qrhistory qh
            JOIN qrcode qr   ON qr.code = qh.qr_code
            JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
              AND qr.turnPackageId IS NOT NULL
              AND qr.turnPackageId != 1
              AND qh.es_venta_real = TRUE
            ORDER BY qh.fecha_hora DESC
            LIMIT 200
        """, (fecha_inicio, fecha_fin))
        ventas = []
        for v in cursor.fetchall():
            row = dict(v)
            row['precio'] = float(row['precio']) if row['precio'] else 0
            if row.get('fecha_hora') and hasattr(row['fecha_hora'], 'isoformat'):
                row['fecha_hora'] = row['fecha_hora'].isoformat()
            ventas.append(row)

        # â”€â”€ Top paquetes â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        cursor.execute("""
            SELECT
                tp.name AS paquete,
                COUNT(DISTINCT qh.qr_code) AS cantidad,
                COALESCE(SUM(tp.price), 0) AS total
            FROM qrhistory qh
            JOIN qrcode qr   ON qr.code = qh.qr_code
            JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
              AND qr.turnPackageId IS NOT NULL
              AND qr.turnPackageId != 1
              AND qh.es_venta_real = TRUE
            GROUP BY tp.id, tp.name
            ORDER BY cantidad DESC
            LIMIT 5
        """, (fecha_inicio, fecha_fin))
        top_paquetes = []
        for p in cursor.fetchall():
            row = dict(p)
            row['total'] = float(row['total']) if row['total'] else 0
            top_paquetes.append(row)

        # â”€â”€ Fallas ESP32 detalladas â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        cursor.execute("""
            SELECT
                mf.id,
                mf.reported_at,
                mf.machine_id,
                COALESCE(mf.machine_name, 'Desconocida') AS machine_name,
                mf.station_index,
                COALESCE(qr.code, '')                    AS qr_code,
                COALESCE(qr.qr_name, '')                 AS qr_name,
                mf.turnos_devueltos,
                COALESCE(mf.notes, '')                   AS notes,
                COALESCE(mf.is_forced, 0)                AS is_forced,
                COALESCE(mf.forced_by, '')               AS forced_by
            FROM machinefailures mf
            LEFT JOIN qrcode qr ON mf.qr_code_id = qr.id
            WHERE DATE(mf.reported_at) BETWEEN %s AND %s
            ORDER BY mf.reported_at DESC
            LIMIT 300
        """, (fecha_inicio, fecha_fin))
        fallas_esp32 = []
        for f in cursor.fetchall():
            row = dict(f)
            if row.get('reported_at') and hasattr(row['reported_at'], 'isoformat'):
                row['reported_at'] = row['reported_at'].isoformat()
            fallas_esp32.append(row)

        # â”€â”€ Por mÃ¡quina â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        cursor.execute("""
            SELECT
                m.id,
                m.name                                          AS nombre,
                m.status                                        AS estado,
                COUNT(DISTINCT tu.id)                           AS turnos_periodo,
                COUNT(DISTINCT mf.id)                           AS fallas_periodo,
                COALESCE(SUM(mf.turnos_devueltos), 0)           AS turnos_devueltos_periodo,
                MAX(tu.usedAt)                                  AS ultimo_uso
            FROM machine m
            LEFT JOIN turnusage tu
                   ON tu.machineId = m.id
                  AND DATE(tu.usedAt) BETWEEN %s AND %s
            LEFT JOIN machinefailures mf
                   ON mf.machine_id = m.id
                  AND DATE(mf.reported_at) BETWEEN %s AND %s
            GROUP BY m.id, m.name, m.status
            ORDER BY turnos_periodo DESC
        """, (fecha_inicio, fecha_fin, fecha_inicio, fecha_fin))
        por_maquina = []
        for m in cursor.fetchall():
            row = dict(m)
            row['turnos_devueltos_periodo'] = float(row['turnos_devueltos_periodo']) if row['turnos_devueltos_periodo'] else 0
            if row.get('ultimo_uso') and hasattr(row['ultimo_uso'], 'isoformat'):
                row['ultimo_uso'] = row['ultimo_uso'].isoformat()
            por_maquina.append(row)

        # â”€â”€ GrÃ¡fica: evoluciÃ³n por hora o por dÃ­a â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        es_mismo_dia = (fecha_inicio == fecha_fin)
        if es_mismo_dia:
            cursor.execute("""
                SELECT
                    HOUR(qh.fecha_hora)                              AS periodo,
                    COUNT(DISTINCT qh.qr_code)                       AS ventas_count,
                    COALESCE(SUM(tp.price), 0)                       AS ventas_monto
                FROM qrhistory qh
                JOIN qrcode qr   ON qr.code = qh.qr_code
                JOIN turnpackage tp ON qr.turnPackageId = tp.id
                WHERE DATE(qh.fecha_hora) = %s
                  AND qr.turnPackageId IS NOT NULL
                  AND qr.turnPackageId != 1
                  AND qh.es_venta_real = TRUE
                GROUP BY HOUR(qh.fecha_hora)
                ORDER BY periodo
            """, (fecha_inicio,))
            grafica_ventas = cursor.fetchall()

            cursor.execute("""
                SELECT HOUR(usedAt) AS periodo, COUNT(*) AS turnos
                FROM turnusage
                WHERE DATE(usedAt) = %s
                GROUP BY HOUR(usedAt)
                ORDER BY periodo
            """, (fecha_inicio,))
            grafica_turnos = cursor.fetchall()
            tipo_grafica = 'horas'
        else:
            cursor.execute("""
                SELECT
                    DATE(qh.fecha_hora)                              AS periodo,
                    COUNT(DISTINCT qh.qr_code)                       AS ventas_count,
                    COALESCE(SUM(tp.price), 0)                       AS ventas_monto
                FROM qrhistory qh
                JOIN qrcode qr   ON qr.code = qh.qr_code
                JOIN turnpackage tp ON qr.turnPackageId = tp.id
                WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
                  AND qr.turnPackageId IS NOT NULL
                  AND qr.turnPackageId != 1
                  AND qh.es_venta_real = TRUE
                GROUP BY DATE(qh.fecha_hora)
                ORDER BY periodo
            """, (fecha_inicio, fecha_fin))
            grafica_ventas = cursor.fetchall()

            cursor.execute("""
                SELECT DATE(usedAt) AS periodo, COUNT(*) AS turnos
                FROM turnusage
                WHERE DATE(usedAt) BETWEEN %s AND %s
                GROUP BY DATE(usedAt)
                ORDER BY periodo
            """, (fecha_inicio, fecha_fin))
            grafica_turnos = cursor.fetchall()
            tipo_grafica = 'dias'

        grafica_ventas_fmt = []
        for g in grafica_ventas:
            row = dict(g)
            row['ventas_monto'] = float(row['ventas_monto']) if row['ventas_monto'] else 0
            if hasattr(row.get('periodo'), 'isoformat'):
                row['periodo'] = row['periodo'].isoformat()
            grafica_ventas_fmt.append(row)

        grafica_turnos_fmt = []
        for g in grafica_turnos:
            row = dict(g)
            if hasattr(row.get('periodo'), 'isoformat'):
                row['periodo'] = row['periodo'].isoformat()
            grafica_turnos_fmt.append(row)

        # â”€â”€ Feed de actividad (transaction_logs) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        cursor.execute("""
            SELECT
                tl.id, tl.tipo, tl.categoria, tl.descripcion,
                tl.usuario, tl.maquina_nombre, tl.maquina_id,
                tl.entidad, tl.entidad_id, tl.monto,
                tl.datos_extra, tl.ip_address, tl.estado, tl.created_at
            FROM transaction_logs tl
            WHERE DATE(tl.created_at) BETWEEN %s AND %s
            ORDER BY tl.created_at DESC
            LIMIT %s
        """, (fecha_inicio, fecha_fin, limit_feed))
        feed = []
        for row in cursor.fetchall():
            r = dict(row)
            if r.get('monto') is not None:
                r['monto'] = float(r['monto'])
            if r.get('created_at') and hasattr(r['created_at'], 'isoformat'):
                r['created_at'] = r['created_at'].isoformat()
            if isinstance(r.get('datos_extra'), str):
                try:
                    r['datos_extra'] = json.loads(r['datos_extra'])
                except Exception:
                    r['datos_extra'] = {}
            feed.append(r)

        cursor.close()
        connection.close()

        return jsonify({
            'periodo': {'fecha_inicio': fecha_inicio, 'fecha_fin': fecha_fin, 'tipo': tipo_grafica},
            'kpis': {
                'ingresos_ventas':   float(kpi_ventas['ingresos_ventas'] or 0),
                'paquetes_vendidos': int(kpi_ventas['paquetes_vendidos'] or 0),
                'turnos_jugados':    int(kpi_turnos['turnos_jugados'] or 0),
                'fallas_total':      int(kpi_fallas['fallas_total'] or 0),
                'turnos_devueltos':  int(kpi_fallas['turnos_devueltos'] or 0),
            },
            'ventas':       ventas,
            'top_paquetes': top_paquetes,
            'fallas_esp32': fallas_esp32,
            'por_maquina':  por_maquina,
            'grafica': {
                'tipo':   tipo_grafica,
                'ventas': grafica_ventas_fmt,
                'turnos': grafica_turnos_fmt,
            },
            'feed':      feed,
            'timestamp': get_colombia_time().isoformat(),
        })

    except Exception as e:
        app.logger.error(f"Error en transaccional-consolidado: {e}", exc_info=True)
        if cursor:
            try: cursor.close()
            except Exception: pass
        if connection:
            try: connection.close()
            except Exception: pass
        return api_response('E001', http_status=500)

# Migrado a blueprints/logs/routes.py
# @app.route('/api/logs/consola-completa', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_logs_consola():
    """Obtener logs de mÃºltiples fuentes - VERSIÃ“N CORREGIDA"""
    try:
        limit = int(request.args.get('limit', 200))
        nivel = request.args.get('nivel', 'todos')
        buscar = request.args.get('buscar', '').strip()
        fuente = request.args.get('fuente', 'todos')
        orden = request.args.get('orden', 'desc')
        fecha_inicio = request.args.get('fecha_inicio')
        fecha_fin = request.args.get('fecha_fin')
        tail = request.args.get('tail', 'false').lower() == 'true'
        
        logs_data = []
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        # Construir consultas dinÃ¡micas para cada fuente
        all_logs = []
        
        # 1. Logs de aplicaciÃ³n
        if fuente in ['todos', 'app']:
            try:
                app_query = """
                    SELECT 
                        'app' as fuente,
                        level as nivel,
                        message as mensaje,
                        module as modulo,
                        ip_address,
                        user_id,
                        created_at,
                        NULL as metodo,
                        NULL as path,
                        NULL as status_code,
                        NULL as response_time_ms
                    FROM app_logs 
                    WHERE 1=1
                """
                params = []
                
                if nivel != 'todos':
                    app_query += " AND level = %s"
                    params.append(nivel)
                
                if buscar:
                    app_query += " AND (message LIKE %s OR module LIKE %s)"
                    params.extend([f'%{buscar}%', f'%{buscar}%'])
                
                if fecha_inicio:
                    app_query += " AND DATE(created_at) >= %s"
                    params.append(fecha_inicio)
                
                if fecha_fin:
                    app_query += " AND DATE(created_at) <= %s"
                    params.append(fecha_fin)
                
                app_query += f" ORDER BY created_at {orden.upper()} LIMIT %s"
                params.append(limit)
                
                cursor.execute(app_query, params)
                results = cursor.fetchall()
                all_logs.extend(results)
                app.logger.info(f"App logs obtenidos: {len(results)} registros")
                
            except Exception as e:
                app.logger.error(f"Error ejecutando consulta app logs: {e}")
        
        # 2. Logs de acceso HTTP
        if fuente in ['todos', 'access']:
            try:
                access_query = """
                    SELECT 
                        'access' as fuente,
                        CASE 
                            WHEN status_code >= 500 THEN 'ERROR'
                            WHEN status_code >= 400 THEN 'WARNING'
                            ELSE 'INFO'
                        END as nivel,
                        CONCAT(method, ' ', path, ' -> ', status_code) as mensaje,
                        'http' as modulo,
                        ip_address,
                        user_id,
                        created_at,
                        method,
                        path,
                        status_code,
                        response_time_ms
                    FROM access_logs 
                    WHERE 1=1
                """
                params = []
                
                if nivel != 'todos':
                    if nivel == 'ERROR':
                        access_query += " AND status_code >= 500"
                    elif nivel == 'WARNING':
                        access_query += " AND status_code BETWEEN 400 AND 499"
                    elif nivel == 'INFO':
                        access_query += " AND status_code < 400"
                
                if buscar:
                    access_query += " AND (path LIKE %s OR method LIKE %s OR ip_address LIKE %s)"
                    params.extend([f'%{buscar}%', f'%{buscar}%', f'%{buscar}%'])
                
                if fecha_inicio:
                    access_query += " AND DATE(created_at) >= %s"
                    params.append(fecha_inicio)
                
                if fecha_fin:
                    access_query += " AND DATE(created_at) <= %s"
                    params.append(fecha_fin)
                
                access_query += f" ORDER BY created_at {orden.upper()} LIMIT %s"
                params.append(limit)
                
                cursor.execute(access_query, params)
                results = cursor.fetchall()
                all_logs.extend(results)
                app.logger.info(f"Access logs obtenidos: {len(results)} registros")
                
            except Exception as e:
                app.logger.error(f"Error ejecutando consulta access logs: {e}")
        
        # 3. Logs de sesiÃ³n (CORREGIDO: sin columna 'action')
        if fuente in ['todos', 'session']:
            try:
                session_query = """
                    SELECT 
                        'session' as fuente,
                        'INFO' as nivel,
                        CONCAT('SesiÃ³n usuario: ', COALESCE(u.name, 'Desconocido'), 
                               ' - Login: ', DATE_FORMAT(s.loginTime, '%%H:%%i:%%s')) as mensaje,
                        'session' as modulo,
                        NULL as ip_address,
                        s.userId as user_id,
                        s.loginTime as created_at,
                        NULL as metodo,
                        NULL as path,
                        NULL as status_code,
                        NULL as response_time_ms
                    FROM sessionlog s
                    LEFT JOIN users u ON s.userId = u.id
                    WHERE 1=1
                """
                params = []
                
                if buscar:
                    session_query += " AND u.name LIKE %s"
                    params.append(f'%{buscar}%')
                
                if fecha_inicio:
                    session_query += " AND DATE(s.loginTime) >= %s"
                    params.append(fecha_inicio)
                
                if fecha_fin:
                    session_query += " AND DATE(s.loginTime) <= %s"
                    params.append(fecha_fin)
                
                session_query += f" ORDER BY s.loginTime {orden.upper()} LIMIT %s"
                params.append(limit)
                
                cursor.execute(session_query, params)
                results = cursor.fetchall()
                all_logs.extend(results)
                app.logger.info(f"Session logs obtenidos: {len(results)} registros")
                
            except Exception as e:
                app.logger.error(f"Error ejecutando consulta session logs: {e}")
        
        # 4. Logs de errores
        if fuente in ['todos', 'error']:
            try:
                error_query = """
                    SELECT
                        'error' as fuente,
                        level as nivel,
                        SUBSTRING(message, 1, 300) as mensaje,
                        module as modulo,
                        ip_address,
                        user_id,
                        created_at,
                        NULL as metodo,
                        endpoint as path,
                        NULL as status_code,
                        NULL as response_time_ms
                    FROM error_logs
                    WHERE 1=1
                """
                params = []

                if buscar:
                    error_query += " AND (message LIKE %s OR module LIKE %s)"
                    params.extend([f'%{buscar}%', f'%{buscar}%'])

                if fecha_inicio:
                    error_query += " AND DATE(created_at) >= %s"
                    params.append(fecha_inicio)

                if fecha_fin:
                    error_query += " AND DATE(created_at) <= %s"
                    params.append(fecha_fin)

                error_query += f" ORDER BY created_at {orden.upper()} LIMIT %s"
                params.append(limit)

                cursor.execute(error_query, params)
                results = cursor.fetchall()
                all_logs.extend(results)

            except Exception as e:
                app.logger.error(f"Error ejecutando consulta error logs: {e}")

        # 5. Log transaccional (financiero / operacional)
        if fuente in ['todos', 'transacciones']:
            try:
                txn_query = """
                    SELECT
                        'transaccion' as fuente,
                        CASE
                            WHEN estado = 'error' THEN 'ERROR'
                            WHEN estado = 'advertencia' THEN 'WARNING'
                            ELSE 'INFO'
                        END as nivel,
                        CONCAT('[', UPPER(tipo), '] ', descripcion) as mensaje,
                        tipo as modulo,
                        ip_address,
                        usuario_id as user_id,
                        created_at,
                        NULL as metodo,
                        NULL as path,
                        NULL as status_code,
                        NULL as response_time_ms,
                        tipo,
                        categoria,
                        usuario,
                        maquina_id,
                        maquina_nombre,
                        entidad,
                        entidad_id,
                        monto,
                        moneda,
                        datos_extra,
                        estado
                    FROM transaction_logs
                    WHERE 1=1
                """
                params = []

                if buscar:
                    txn_query += " AND (descripcion LIKE %s OR tipo LIKE %s OR usuario LIKE %s OR maquina_nombre LIKE %s)"
                    params.extend([f'%{buscar}%'] * 4)

                if fecha_inicio:
                    txn_query += " AND DATE(created_at) >= %s"
                    params.append(fecha_inicio)

                if fecha_fin:
                    txn_query += " AND DATE(created_at) <= %s"
                    params.append(fecha_fin)

                txn_query += f" ORDER BY created_at {orden.upper()} LIMIT %s"
                params.append(limit)

                cursor.execute(txn_query, params)
                results = cursor.fetchall()
                all_logs.extend(results)

            except Exception as e:
                app.logger.error(f"Error ejecutando consulta transaction logs: {e}")

        cursor.close()
        connection.close()
        
        # Ordenar combinado
        try:
            all_logs.sort(key=lambda x: x['created_at'] if x['created_at'] else datetime.min, 
                         reverse=(orden.lower() == 'desc'))
        except Exception as e:
            app.logger.warning(f"Error ordenando logs: {e}")
        
        # Limitar resultados finales
        all_logs = all_logs[:limit]
        
        # Formatear logs para la respuesta
        for log in all_logs:
            try:
                log_entry = {
                    'fuente': log.get('fuente', 'unknown'),
                    'nivel': log.get('nivel', 'INFO'),
                    'mensaje': log.get('mensaje', '') or '',
                    'modulo': log.get('modulo', '') or '',
                    'timestamp': log.get('created_at').isoformat() if log.get('created_at') else '',
                    'ip': log.get('ip_address', '') or '',
                    'user_id': log.get('user_id'),
                }
                
                # Agregar informaciÃ³n especÃ­fica por fuente
                if log.get('fuente') == 'access':
                    log_entry.update({
                        'metodo': log.get('metodo', ''),
                        'path': log.get('path', ''),
                        'status_code': log.get('status_code'),
                        'response_time': log.get('response_time_ms')
                    })
                elif log.get('fuente') == 'transaccion':
                    datos_extra = log.get('datos_extra')
                    if isinstance(datos_extra, str):
                        try:
                            datos_extra = json.loads(datos_extra)
                        except Exception:
                            datos_extra = {}
                    log_entry.update({
                        'tipo': log.get('tipo', ''),
                        'categoria': log.get('categoria', ''),
                        'usuario': log.get('usuario', ''),
                        'maquina_id': log.get('maquina_id'),
                        'maquina_nombre': log.get('maquina_nombre', ''),
                        'entidad': log.get('entidad', ''),
                        'entidad_id': log.get('entidad_id'),
                        'monto': float(log['monto']) if log.get('monto') is not None else None,
                        'moneda': log.get('moneda', 'COP'),
                        'datos_extra': datos_extra,
                        'estado': log.get('estado', 'ok')
                    })
                
                logs_data.append(log_entry)
            except Exception as e:
                app.logger.error(f"Error formateando log: {e}")
                continue
        
        app.logger.info(f"Total logs preparados: {len(logs_data)}")
        
        return jsonify({
            'logs': logs_data,
            'total': len(logs_data),
            'filtros': {
                'limit': limit,
                'nivel': nivel,
                'buscar': buscar,
                'fuente': fuente,
                'orden': orden,
                'fecha_inicio': fecha_inicio,
                'fecha_fin': fecha_fin,
                'tail': tail
            }
        })
        
    except Exception as e:
        app.logger.error(f"Error obteniendo logs consola: {e}", exc_info=True)
        return api_response('E001', http_status=500)

# Migrado a blueprints/logs/routes.py
# @app.route('/api/logs/estadisticas', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_estadisticas_logs():
    """Obtener estadÃ­sticas de logs - VERSIÃ“N CORREGIDA"""
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        hoy = get_colombia_time().date()
        
        # EstadÃ­sticas del dÃ­a desde las tablas reales
        # 1. Total logs hoy
        cursor.execute("""
            SELECT COUNT(*) as total_logs_hoy
            FROM app_logs 
            WHERE DATE(created_at) = %s
        """, (hoy,))
        
        total_logs = cursor.fetchone()
        
        # 2. Errores hoy
        cursor.execute("""
            SELECT COUNT(*) as errores_hoy
            FROM error_logs 
            WHERE DATE(created_at) = %s
        """, (hoy,))
        
        errores = cursor.fetchone()
        
        # 3. Accesos hoy
        cursor.execute("""
            SELECT COUNT(*) as accesos_hoy
            FROM access_logs 
            WHERE DATE(created_at) = %s
        """, (hoy,))
        
        accesos = cursor.fetchone()
        
        # 4. Usuarios activos hoy (distintos que han iniciado sesiÃ³n)
        cursor.execute("""
            SELECT COUNT(DISTINCT user_id) as usuarios_activos_hoy
            FROM access_logs 
            WHERE DATE(created_at) = %s
            AND user_id IS NOT NULL
        """, (hoy,))
        
        usuarios_activos = cursor.fetchone()
        
        # 5. Top endpoints del dÃ­a
        cursor.execute("""
            SELECT 
                CONCAT(method, ' ', path) as endpoint,
                COUNT(*) as total,
                AVG(response_time_ms) as avg_time,
                COUNT(DISTINCT ip_address) as ips_unicas
            FROM access_logs 
            WHERE DATE(created_at) = %s
            GROUP BY method, path
            ORDER BY total DESC
            LIMIT 5
        """, (hoy,))
        
        top_endpoints = cursor.fetchall()
        
        # 6. Errores por tipo hoy
        cursor.execute("""
            SELECT 
                error_type,
                COUNT(*) as total,
                GROUP_CONCAT(DISTINCT module) as modulos
            FROM error_logs 
            WHERE DATE(created_at) = %s
            GROUP BY error_type
            ORDER BY total DESC
            LIMIT 5
        """, (hoy,))
        
        errores_por_tipo = cursor.fetchall()
        
        cursor.close()
        connection.close()
        
        return jsonify({
            'total_logs_hoy': total_logs['total_logs_hoy'] or 0,
            'errores_hoy': errores['errores_hoy'] or 0,
            'accesos_hoy': accesos['accesos_hoy'] or 0,
            'usuarios_activos_hoy': usuarios_activos['usuarios_activos_hoy'] or 0,
            'top_endpoints': top_endpoints,
            'errores_por_tipo': errores_por_tipo,
            'fecha': hoy.isoformat(),
            'timestamp': get_colombia_time().isoformat()
        })
        
    except Exception as e:
        app.logger.error(f"Error obteniendo estadÃ­sticas: {e}", exc_info=True)
        return api_response('E001', http_status=500)

# Migrado a blueprints/logs/routes.py
# @app.route('/api/logs/config', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_config_logs():
    """Obtener configuraciÃ³n de logs"""
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("SELECT * FROM log_config ORDER BY config_key")
        config = cursor.fetchall()
        
        cursor.execute("SELECT * FROM log_alerts WHERE is_active = TRUE ORDER BY severity")
        alertas = cursor.fetchall()
        
        cursor.close()
        connection.close()
        
        return jsonify({
            'config': config,
            'alertas': alertas,
            'timestamp': get_colombia_time().isoformat()
        })
        
    except Exception as e:
        app.logger.error(f"Error obteniendo configuraciÃ³n: {e}")
        return api_response('E001', http_status=500)

# Migrado a blueprints/logs/routes.py
# @app.route('/api/logs/config', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
@validate_required_fields(['config_key', 'config_value'])
def actualizar_config_logs():
    """Actualizar configuraciÃ³n de logs"""
    try:
        data = request.get_json()
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("""
            INSERT INTO log_config (config_key, config_value, config_type, description, updated_by)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
            config_value = VALUES(config_value),
            config_type = VALUES(config_type),
            description = VALUES(description),
            updated_by = VALUES(updated_by),
            updated_at = NOW()
        """, (
            data['config_key'],
            data['config_value'],
            data.get('config_type', 'string'),
            data.get('description', ''),
            session.get('user_id')
        ))
        
        connection.commit()
        
        log_app_event('INFO', f'ConfiguraciÃ³n actualizada: {data["config_key"]}', 
                     'logs', data, session.get('user_id'))
        
        cursor.close()
        connection.close()
        
        return api_response('S003', status='success')
        
    except Exception as e:
        app.logger.error(f"Error actualizando configuraciÃ³n: {e}")
        return api_response('E001', http_status=500)

# Migrado a blueprints/logs/routes.py
# @app.route('/api/logs/alertas', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
@validate_required_fields(['alert_type', 'alert_message', 'condition'])
def crear_alerta_logs():
    """Crear nueva alerta"""
    try:
        data = request.get_json()
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("""
            INSERT INTO log_alerts 
            (alert_type, alert_message, severity, condition, notification_method)
            VALUES (%s, %s, %s, %s, %s)
        """, (
            data['alert_type'],
            data['alert_message'],
            data.get('severity', 'medium'),
            data['condition'],
            data.get('notification_method', 'console')
        ))
        
        connection.commit()
        
        log_app_event('INFO', f'Alerta creada: {data["alert_type"]}', 
                     'logs', data, session.get('user_id'))
        
        cursor.close()
        connection.close()
        
        return api_response('S002', status='success', data={'alerta_id': cursor.lastrowid})
        
    except Exception as e:
        app.logger.error(f"Error creando alerta: {e}")
        return api_response('E001', http_status=500)

# Migrado a blueprints/logs/routes.py
# @app.route('/api/logs/alertas/<int:alerta_id>/toggle', methods=['PUT'])
@handle_api_errors
@require_login(['admin'])
def toggle_alerta_logs(alerta_id):
    """Activar/desactivar alerta"""
    try:
        data = request.get_json()
        activa = data.get('activa', True)
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("""
            UPDATE log_alerts 
            SET is_active = %s,
                updated_at = NOW()
            WHERE id = %s
        """, (activa, alerta_id))
        
        connection.commit()
        
        estado = 'activada' if activa else 'desactivada'
        log_app_event('INFO', f'Alerta {alerta_id} {estado}', 
                     'logs', {'alerta_id': alerta_id, 'estado': estado}, session.get('user_id'))
        
        cursor.close()
        connection.close()
        
        return api_response('S003', status='success')
        
    except Exception as e:
        app.logger.error(f"Error toggle alerta: {e}")
        return api_response('E001', http_status=500)

# Migrado a blueprints/logs/routes.py
# @app.route('/api/logs/limpiar', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
def limpiar_logs_sistema():
    """Limpiar logs antiguos"""
    try:
        data = request.get_json()
        dias = int(data.get('dias', 30))
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        fecha_limite = (datetime.now() - timedelta(days=dias)).strftime('%Y-%m-%d')
        
        # Limpiar logs antiguos
        tablas = ['app_logs', 'access_logs', 'error_logs', 'sessionlog']
        total_eliminados = 0
        
        for tabla in tablas:
            cursor.execute(f"""
                DELETE FROM {tabla} 
                WHERE DATE(created_at) < %s
            """, (fecha_limite,))
            total_eliminados += cursor.rowcount
        
        # Crear backup de estadÃ­sticas
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS log_statistics_backup_%s 
            SELECT * FROM log_statistics 
            WHERE date < %s
        """, (datetime.now().strftime('%Y%m%d'), fecha_limite))
        
        cursor.execute("""
            DELETE FROM log_statistics 
            WHERE date < %s
        """, (fecha_limite,))
        
        connection.commit()
        
        log_app_event('INFO', f'Logs limpiados: {total_eliminados} registros eliminados (>{dias} dÃ­as)', 
                     'logs', {'dias': dias, 'eliminados': total_eliminados}, session.get('user_id'))
        
        cursor.close()
        connection.close()
        
        return api_response('S001', status='success', data={
            'eliminados': total_eliminados,
            'dias': dias,
            'fecha_limite': fecha_limite
        })
        
    except Exception as e:
        app.logger.error(f"Error limpiando logs: {e}")
        return api_response('E001', http_status=500)

# Migrado a blueprints/logs/routes.py
# @app.route('/api/logs/exportar', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
def exportar_logs_sistema():
    """Exportar logs a archivo"""
    try:
        data = request.get_json()
        fecha_inicio = data.get('fecha_inicio')
        fecha_fin = data.get('fecha_fin') or datetime.now().strftime('%Y-%m-%d')
        formatos = data.get('formatos', ['json', 'csv'])
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        # Crear registro de exportaciÃ³n
        cursor.execute("""
            INSERT INTO log_exports 
            (export_name, start_date, end_date, filters, exported_by)
            VALUES (%s, %s, %s, %s, %s)
        """, (
            f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            fecha_inicio,
            fecha_fin,
            json.dumps(data),
            session.get('user_id')
        ))
        
        export_id = cursor.lastrowid
        
        # Obtener logs para exportar
        logs_data = []
        
        # App logs
        cursor.execute("""
            SELECT * FROM app_logs 
            WHERE DATE(created_at) BETWEEN %s AND %s
            ORDER BY created_at
        """, (fecha_inicio, fecha_fin))
        app_logs = cursor.fetchall()
        
        # Access logs
        cursor.execute("""
            SELECT * FROM access_logs 
            WHERE DATE(created_at) BETWEEN %s AND %s
            ORDER BY created_at
        """, (fecha_inicio, fecha_fin))
        access_logs = cursor.fetchall()
        
        # Error logs
        cursor.execute("""
            SELECT * FROM error_logs 
            WHERE DATE(created_at) BETWEEN %s AND %s
            ORDER BY created_at
        """, (fecha_inicio, fecha_fin))
        error_logs = cursor.fetchall()
        
        cursor.close()
        connection.close()
        
        # Crear archivo temporal
        import tempfile
        import zipfile
        import csv
        
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
        temp_path = temp_file.name
        temp_file.close()
        
        with zipfile.ZipFile(temp_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            # Exportar en JSON
            if 'json' in formatos:
                export_json = {
                    'app_logs': app_logs,
                    'access_logs': access_logs,
                    'error_logs': error_logs,
                    'metadata': {
                        'fecha_inicio': fecha_inicio,
                        'fecha_fin': fecha_fin,
                        'exportado_el': datetime.now().isoformat(),
                        'exportado_por': session.get('user_name')
                    }
                }
                
                json_str = json.dumps(export_json, default=str, indent=2)
                zipf.writestr('logs.json', json_str)
            
            # Exportar en CSV
            if 'csv' in formatos:
                # App logs CSV
                if app_logs:
                    csv_str = io.StringIO()
                    csv_writer = csv.DictWriter(csv_str, fieldnames=app_logs[0].keys())
                    csv_writer.writeheader()
                    csv_writer.writerows(app_logs)
                    zipf.writestr('app_logs.csv', csv_str.getvalue())
                
                # Access logs CSV
                if access_logs:
                    csv_str = io.StringIO()
                    csv_writer = csv.DictWriter(csv_str, fieldnames=access_logs[0].keys())
                    csv_writer.writeheader()
                    csv_writer.writerows(access_logs)
                    zipf.writestr('access_logs.csv', csv_str.getvalue())
                
                # Error logs CSV
                if error_logs:
                    csv_str = io.StringIO()
                    csv_writer = csv.DictWriter(csv_str, fieldnames=error_logs[0].keys())
                    csv_writer.writeheader()
                    csv_writer.writerows(error_logs)
                    zipf.writestr('error_logs.csv', csv_str.getvalue())
        
        # Actualizar registro de exportaciÃ³n
        file_size = os.path.getsize(temp_path)
        
        connection = get_db_connection()
        if connection:
            cursor = get_db_cursor(connection)
            
            cursor.execute("""
                UPDATE log_exports 
                SET file_path = %s,
                    file_size = %s,
                    status = 'completed',
                    completed_at = NOW()
                WHERE id = %s
            """, (temp_path, file_size, export_id))
            
            connection.commit()
            cursor.close()
            connection.close()
        
        log_app_event('INFO', f'ExportaciÃ³n de logs completada: {export_id}', 
                     'logs', {'export_id': export_id, 'tamano': file_size}, session.get('user_id'))
        
        return send_file(
            temp_path,
            as_attachment=True,
            download_name=f'logs_export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.zip',
            mimetype='application/zip'
        )
        
    except Exception as e:
        app.logger.error(f"Error exportando logs: {e}")
        return api_response('E001', http_status=500)

# Migrado a blueprints/logs/routes.py
# @app.route('/api/logs/errores/<int:error_id>/resolver', methods=['PUT'])
@handle_api_errors
@require_login(['admin'])
def resolver_error_log(error_id):
    """Marcar error como resuelto"""
    try:
        data = request.get_json()
        comentarios = data.get('comentarios', '')
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("""
            UPDATE error_logs 
            SET resolved = TRUE,
                resolved_at = NOW(),
                resolved_by = %s
            WHERE id = %s
        """, (session.get('user_id'), error_id))
        
        connection.commit()
        
        log_app_event('INFO', f'Error {error_id} marcado como resuelto', 
                     'logs', {'error_id': error_id, 'comentarios': comentarios}, session.get('user_id'))
        
        cursor.close()
        connection.close()
        
        return api_response('S003', status='success')
        
    except Exception as e:
        app.logger.error(f"Error resolviendo error: {e}")
        return api_response('E001', http_status=500)

# Migrado a blueprints/logs/routes.py
# @app.route('/api/logs/dashboard', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_dashboard_logs():
    """Obtener datos para dashboard de logs"""
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        hoy = datetime.now().date()
        ayer = (datetime.now() - timedelta(days=1)).date()
        
        # Resumen rÃ¡pido
        cursor.execute("""
            SELECT 
                (SELECT COUNT(*) FROM app_logs WHERE DATE(created_at) = %s) as total_logs_hoy,
                (SELECT COUNT(*) FROM app_logs WHERE DATE(created_at) = %s AND level = 'ERROR') as errores_hoy,
                (SELECT COUNT(*) FROM access_logs WHERE DATE(created_at) = %s) as accesos_hoy,
                (SELECT COUNT(*) FROM error_logs WHERE DATE(created_at) = %s AND resolved = FALSE) as errores_pendientes
        """, (hoy, hoy, hoy, hoy))
        
        resumen = cursor.fetchone()
        
        # EvoluciÃ³n Ãºltimos 7 dÃ­as
        cursor.execute("""
            SELECT 
                date,
                total_logs,
                error_logs,
                access_logs
            FROM log_statistics 
            WHERE date >= DATE_SUB(%s, INTERVAL 7 DAY)
            ORDER BY date
        """, (hoy,))
        
        evolucion = cursor.fetchall()
        
        # Top errores no resueltos
        cursor.execute("""
            SELECT 
                error_type,
                COUNT(*) as total,
                MIN(created_at) as primer_error,
                MAX(created_at) as ultimo_error
            FROM error_logs 
            WHERE resolved = FALSE
            GROUP BY error_type
            ORDER BY total DESC
            LIMIT 5
        """)
        
        top_errores = cursor.fetchall()
        
        # Actividad por hora hoy
        cursor.execute("""
            SELECT 
                HOUR(created_at) as hora,
                COUNT(*) as total
            FROM access_logs 
            WHERE DATE(created_at) = %s
            GROUP BY HOUR(created_at)
            ORDER BY hora
        """, (hoy,))
        
        actividad_hora = cursor.fetchall()
        
        # MÃ©tricas de rendimiento
        cursor.execute("""
            SELECT 
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY response_time_ms) as p50,
                PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY response_time_ms) as p95,
                AVG(response_time_ms) as promedio,
                MAX(response_time_ms) as maximo
            FROM access_logs 
            WHERE DATE(created_at) = %s
        """, (hoy,))
        
        rendimiento = cursor.fetchone()
        
        # Alertas recientes
        cursor.execute("""
            SELECT 
                alert_type,
                alert_message,
                severity,
                last_triggered
            FROM log_alerts 
            WHERE last_triggered >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
            ORDER BY last_triggered DESC
            LIMIT 5
        """)
        
        alertas_recientes = cursor.fetchall()
        
        cursor.close()
        connection.close()
        
        return jsonify({
            'resumen': resumen,
            'evolucion': evolucion,
            'top_errores': top_errores,
            'actividad_hora': actividad_hora,
            'rendimiento': rendimiento,
            'alertas_recientes': alertas_recientes,
            'timestamp': get_colombia_time().isoformat()
        })
        
    except Exception as e:
        app.logger.error(f"Error obteniendo dashboard: {e}")
        return api_response('E001', http_status=500)

# ==================== APIS PARA HISTORIAL PACKFAILURE ====================

# Migrado a blueprints/devoluciones/routes.py
# @app.route('/api/qr/historial-completo/<qr_code>', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero'])
def obtener_historial_completo_qr(qr_code):
    """
    ENDPOINT PRINCIPAL para packfailure.html
    SIN LÃMITE de devoluciones - AHORA ILIMITADO
    """
    connection = None
    cursor = None
    try:
        app.logger.info(f"ðŸ” Historial completo solicitado para QR: {qr_code}")
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        # 1. INFORMACIÃ“N BÃSICA DEL QR
        cursor.execute("""
            SELECT 
                qr.id as qr_id,
                qr.code as qr_code,
                qr.remainingTurns,
                qr.isActive,
                qr.turnPackageId,
                qr.qr_name,
                tp.name as package_name,
                tp.turns as package_total_turns,
                tp.price as package_price,
                ut.turns_remaining,
                ut.total_turns
            FROM qrcode qr
            LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
            LEFT JOIN userturns ut ON qr.id = ut.qr_code_id
            WHERE qr.code = %s
        """, (qr_code,))
        
        qr_data = cursor.fetchone()
        
        if not qr_data:
            return api_response('Q001', http_status=404, data={'qr_code': qr_code})
        
        # 2. CONTAR DEVOLUCIONES (SOLO INFORMATIVO, SIN BLOQUEO)
        cursor.execute("""
            SELECT 
                COUNT(*) as total_devoluciones,
                SUM(turnos_devueltos) as turnos_devueltos_total
            FROM machinefailures
            WHERE qr_code_id = %s
        """, (qr_data['qr_id'],))
        
        devolucion_data = cursor.fetchone()
        
        # 3. HISTORIAL DE JUEGOS
        cursor.execute("""
            SELECT 
                tu.id as usage_id,
                tu.usedAt as fecha_hora,
                tu.machineId as machine_id,
                m.name as machine_name,
                m.status as machine_status
            FROM turnusage tu
            JOIN machine m ON tu.machineId = m.id
            WHERE tu.qrCodeId = %s
            ORDER BY tu.usedAt DESC
        """, (qr_data['qr_id'],))
        
        juegos = cursor.fetchall()
        
        # 4. PROCESAR CADA JUEGO
        historial_juegos = []
        
        for juego in juegos:
            juego_id = juego['usage_id']
            machine_id = juego['machine_id']
            fecha_juego = juego['fecha_hora']
            
            # A) TURNOS ANTES DEL JUEGO
            cursor.execute("""
                SELECT COUNT(*) as usos_previos
                FROM turnusage
                WHERE qrCodeId = %s AND usedAt < %s
            """, (qr_data['qr_id'], fecha_juego))
            
            usos_previos = cursor.fetchone()['usos_previos']
            turnos_antes = (qr_data['total_turns'] or 0) - usos_previos
            turnos_despues = turnos_antes - 1
            if turnos_despues < 0:
                turnos_despues = 0
            
            # B) Â¿HUBO FALLA REPORTADA?
            cursor.execute("""
                SELECT 
                    id,
                    turnos_devueltos,
                    is_forced,
                    forced_by,
                    reported_at,
                    notes
                FROM machinefailures
                WHERE qr_code_id = %s 
                AND machine_id = %s
                AND ABS(TIMESTAMPDIFF(MINUTE, reported_at, %s)) < 30
                ORDER BY reported_at DESC
                LIMIT 1
            """, (qr_data['qr_id'], machine_id, fecha_juego))
            
            falla = cursor.fetchone()
            
            hubo_falla = falla is not None
            falla_forzada = falla['is_forced'] if falla else False
            falla_id = falla['id'] if falla else None
            
            # C) Â¿ALGUIEN JUGÃ“ DESPUÃ‰S?
            cursor.execute("""
                SELECT 
                    tu.id,
                    tu.usedAt,
                    m.name as machine_name
                FROM turnusage tu
                JOIN machine m ON tu.machineId = m.id
                WHERE tu.machineId = %s
                AND tu.usedAt > %s
                AND tu.qrCodeId != %s
                AND NOT EXISTS (
                    SELECT 1 
                    FROM machinefailures mf 
                    WHERE mf.qr_code_id = tu.qrCodeId
                    AND mf.machine_id = tu.machineId
                    AND ABS(TIMESTAMPDIFF(MINUTE, mf.reported_at, tu.usedAt)) < 30
                )
                ORDER BY tu.usedAt ASC
                LIMIT 1
            """, (machine_id, fecha_juego, qr_data['qr_id']))
            
            uso_posterior = cursor.fetchone()
            
            alguien_jugo_despues = uso_posterior is not None
            fecha_uso_posterior = uso_posterior['usedAt'] if uso_posterior else None
            
            # D) ESTADO DE VALIDACIÃ“N
            if hubo_falla and not alguien_jugo_despues:
                estado_validacion = 'APTO'
                color_estado = 'green'
                mensaje_estado = 'âœ… Apto para devoluciÃ³n - Falla confirmada'
            elif hubo_falla and alguien_jugo_despues:
                estado_validacion = 'NO_APTO'
                color_estado = 'red'
                mensaje_estado = 'âŒ No apto - Alguien jugÃ³ exitosamente despuÃ©s'
            elif not hubo_falla:
                estado_validacion = 'SIN_REPORTE'
                color_estado = 'yellow'
                mensaje_estado = 'âš ï¸ Sin reporte de falla - Verificar con cliente'
            else:
                estado_validacion = 'REVISAR'
                color_estado = 'orange'
                mensaje_estado = 'ðŸ” Revisar caso'
            
            historial_juegos.append({
                'usage_id': juego_id,
                'fecha_hora': fecha_juego.isoformat() if fecha_juego else None,
                'fecha_formateada': fecha_juego.strftime('%d/%m/%Y %H:%M') if fecha_juego else None,
                'machine': {
                    'id': machine_id,
                    'nombre': juego['machine_name'],
                    'estado': juego['machine_status']
                },
                'turnos': {
                    'antes': turnos_antes,
                    'despues': turnos_despues
                },
                'falla': {
                    'hubo': hubo_falla,
                    'forzada': falla_forzada,
                    'id': falla_id
                },
                'uso_posterior': {
                    'hubo': alguien_jugo_despues,
                    'fecha': fecha_uso_posterior.isoformat() if fecha_uso_posterior else None,
                    'fecha_formateada': fecha_uso_posterior.strftime('%d/%m/%Y %H:%M') if fecha_uso_posterior else None
                },
                'validacion': {
                    'estado': estado_validacion,
                    'color': color_estado,
                    'mensaje': mensaje_estado
                }
            })
        
        response_data = {
            'qr': {
                'id': qr_data['qr_id'],
                'codigo': qr_data['qr_code'],
                'nombre': qr_data['qr_name'],
                'activo': bool(qr_data['isActive'])
            },
            'paquete': {
                'id': qr_data['turnPackageId'],
                'nombre': qr_data['package_name'] or 'Sin paquete',
                'turnos_totales': qr_data['package_total_turns'] or 0,
                'precio': float(qr_data['package_price'] or 0)
            },
            'turnos': {
                'totales': qr_data['total_turns'] or 0,
                'restantes': qr_data['turns_remaining'] or 0,
                'usados': (qr_data['total_turns'] or 0) - (qr_data['turns_remaining'] or 0)
            },
            'devoluciones': {
                'total': devolucion_data['total_devoluciones'] or 0,
                'turnos_devueltos': devolucion_data['turnos_devueltos_total'] or 0
            },
            'historial': historial_juegos,
            'timestamp': get_colombia_time().isoformat()
        }
        
        app.logger.info(f"âœ… Historial generado para {qr_code}: {len(historial_juegos)} juegos")
        return jsonify(response_data)
        
    except Exception as e:
        app.logger.error(f"âŒ Error: {e}", exc_info=True)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# Migrado a blueprints/devoluciones/routes.py
# @app.route('/api/qr/estado-devolucion/<qr_code>', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero'])
def obtener_estado_devolucion_qr(qr_code):
    """
    Endpoint rÃ¡pido para saber si un QR ya tuvo devoluciÃ³n
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        # Obtener ID del QR
        cursor.execute("SELECT id FROM qrcode WHERE code = %s", (qr_code,))
        qr_data = cursor.fetchone()
        
        if not qr_data:
            return api_response('Q001', http_status=404)
        
        qr_id = qr_data['id']
        
        # Verificar devoluciones
        cursor.execute("""
            SELECT 
                COUNT(*) as total,
                MAX(reported_at) as ultima,
                SUM(turnos_devueltos) as turnos_devueltos
            FROM machinefailures
            WHERE qr_code_id = %s
        """, (qr_id,))
        
        data = cursor.fetchone()
        
        return jsonify({
            'qr_code': qr_code,
            'qr_id': qr_id,
            'ya_tuvo_devolucion': data['total'] > 0,
            'total_devoluciones': data['total'] or 0,
            'ultima_devolucion': data['ultima'].isoformat() if data['ultima'] else None,
            'turnos_devueltos_total': data['turnos_devueltos'] or 0,
            'limite': 1,
            'puede_devolver': data['total'] == 0
        })
        
    except Exception as e:
        app.logger.error(f"Error obteniendo estado devoluciÃ³n: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


# Migrado a blueprints/devoluciones/routes.py
# @app.route('/api/qr/procesar-devolucion', methods=['POST'])
@handle_api_errors
@require_login(['admin', 'cajero'])
@validate_required_fields(['qr_code', 'machine_id', 'usage_id'])
def procesar_devolucion_unica():
    """
    Procesa UNA devoluciÃ³n de EXACTAMENTE 1 turno
    - Siempre devuelve 1 turno
    - Solo se puede una vez por QR en toda su vida
    - Valida el lÃ­mite antes de procesar
    """
    connection = None
    cursor = None
    try:
        data = request.get_json()
        qr_code = data['qr_code']
        machine_id = data['machine_id']
        usage_id = data['usage_id']
        is_forced = data.get('is_forced', False)
        forced_by = session.get('user_name', 'Cajero')
        notes = data.get('notes', f'DevoluciÃ³n desde packfailure - Usage ID: {usage_id}')
        
        app.logger.info(f"ðŸ”„ Procesando devoluciÃ³n - QR: {qr_code}, MÃ¡quina: {machine_id}, Uso: {usage_id}, Forzado: {is_forced}")
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        # ==========================================
        # 1. VERIFICAR QUE EL QR EXISTE
        # ==========================================
        cursor.execute("SELECT id, remainingTurns FROM qrcode WHERE code = %s", (qr_code,))
        qr_data = cursor.fetchone()
        
        if not qr_data:
            return api_response('Q001', http_status=404)
        
        qr_id = qr_data['id']
        
        # ==========================================
        # 2. VALIDAR LÃMITE DE UNA SOLA DEVOLUCIÃ“N
        # ==========================================
        cursor.execute("""
            SELECT COUNT(*) as total
            FROM machinefailures
            WHERE qr_code_id = %s
        """, (qr_id,))
        
        devoluciones_existentes = cursor.fetchone()['total']
        
        if devoluciones_existentes >= 1:
            app.logger.warning(f"â›” DevoluciÃ³n rechazada - QR {qr_code} ya tuvo devoluciÃ³n")
            return api_response(
                'D001',
                status='error',
                http_status=400,
                data={
                    'qr_code': qr_code,
                    'motivo': 'Este QR ya ha recibido una devoluciÃ³n anteriormente',
                    'limite': 1,
                    'actual': devoluciones_existentes
                }
            )
        
        # ==========================================
        # 3. VERIFICAR QUE EL USO EXISTE
        # ==========================================
        cursor.execute("""
            SELECT tu.*, m.name as machine_name
            FROM turnusage tu
            JOIN machine m ON tu.machineId = m.id
            WHERE tu.id = %s AND tu.qrCodeId = %s
        """, (usage_id, qr_id))
        
        uso_data = cursor.fetchone()
        
        if not uso_data:
            return api_response('E002', http_status=404, data={'message': 'Registro de uso no encontrado'})
        
        # ==========================================
        # 4. REGISTRAR LA DEVOLUCIÃ“N
        # ==========================================
        cursor.execute("""
            INSERT INTO machinefailures 
            (qr_code_id, machine_id, machine_name, turnos_devueltos, notes, is_forced, forced_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            qr_id,
            machine_id,
            uso_data['machine_name'],
            1,  # SIEMPRE 1 turno
            notes,
            1 if is_forced else 0,
            forced_by if is_forced else None
        ))
        
        failure_id = cursor.lastrowid
        
        # ==========================================
        # 5. DEVOLVER EL TURNO
        # ==========================================
        cursor.execute("""
            UPDATE userturns 
            SET turns_remaining = turns_remaining + 1
            WHERE qr_code_id = %s
        """, (qr_id,))
        
        # ==========================================
        # 6. ACTUALIZAR TURNOS EN QRCODE
        # ==========================================
        cursor.execute("""
            UPDATE qrcode 
            SET remainingTurns = remainingTurns + 1
            WHERE id = %s
        """, (qr_id,))
        
        connection.commit()
        
        # ==========================================
        # 7. OBTENER NUEVOS TURNOS
        # ==========================================
        cursor.execute("SELECT turns_remaining FROM userturns WHERE qr_code_id = %s", (qr_id,))
        nuevos_turnos = cursor.fetchone()['turns_remaining']
        
        app.logger.info(f"âœ… DevoluciÃ³n exitosa - ID: {failure_id}, QR: {qr_code}, Nuevos turnos: {nuevos_turnos}")
        
        return api_response(
            'S003',
            status='success',
            data={
                'devolucion_id': failure_id,
                'qr_code': qr_code,
                'machine_id': machine_id,
                'usage_id': usage_id,
                'turnos_devueltos': 1,
                'turnos_restantes': nuevos_turnos,
                'is_forced': is_forced,
                'limite': 1,
                'devoluciones_restantes': 0  # Ya no puede mÃ¡s
            }
        )
        
    except Exception as e:
        app.logger.error(f"âŒ Error procesando devoluciÃ³n: {e}", exc_info=True)
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# ==================== FUNCIONES DE LOGGING MEJORADAS ====================

def log_info(message, module=None, user_id=None):
    """Log de nivel INFO."""
    return shared_log_info(message, module, user_id)

def log_warning(message, module=None,  user_id=None):
    """Log de nivel WARNING."""
    return shared_log_warning(message, module, user_id)

def log_error_system(error, module=None, user_id=None):
    """Log de error del sistema."""
    return shared_log_error_system(error, module, user_id)

def log_user_action(action, user_id=None):
    """Log de acción de usuario."""
    return shared_log_user_action(action, user_id)

def log_system_event(event):
    """Log de evento del sistema."""
    return shared_log_system_event(event)

# Backup manual y devolución de turnos migrados a sus blueprints correspondientes.


if __name__ == '__main__':
    app.logger.info("ðŸš€ Iniciando servidor Flask en http://127.0.0.1:5000")
    app.run(debug=True, port=5000, host='0.0.0.0')



