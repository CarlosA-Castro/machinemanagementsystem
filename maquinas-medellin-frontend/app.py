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

# â”€â”€ Blueprints migrados (Fase 2) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

# Los blueprints activos ahora se registran exclusivamente desde factory.py.

# RUTAS PENDIENTES DE MIGRAR (Fase 3+)

# auth routes â†’ blueprints/auth/routes.py

# APIS PARA QR Y PAQUETES 

def generar_codigo_qr():
    """Generar cÃ³digo QR con formato QR0001, QR0002, etc. usando contador global con reinicio en 9999"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return None
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("""
            SELECT counter_value FROM globalcounter 
            WHERE counter_type = 'QR_CODE' 
            FOR UPDATE
        """)
        
        resultado = cursor.fetchone()
        
        if not resultado:
           
            cursor.execute("""
                INSERT INTO globalcounter (counter_type, counter_value, description) 
                VALUES ('QR_CODE', 1, 'Contador para cÃ³digos QR (formato QR0001, QR0002, etc.)')
            """)
            nuevo_numero = 1
        else:
            
            nuevo_numero = resultado['counter_value'] + 1
            
            if nuevo_numero > 9999:
                nuevo_numero = 1
                app.logger.warning("Contador QR reiniciado a 1 (llegÃ³ al lÃ­mite de 9999)")
            
            cursor.execute("""
                UPDATE globalcounter 
                SET counter_value = %s 
                WHERE counter_type = 'QR_CODE'
            """, (nuevo_numero,))
        
        # Formatear con 4 dÃ­gitos (reinicia en 1 despuÃ©s de 9999)
        nuevo_codigo = f"QR{nuevo_numero:04d}"  
        
        user_id = session.get('user_id')
        user_name = session.get('user_name', 'Sistema')
        local = session.get('user_local', 'El Mekatiadero')
        hora_colombia = get_colombia_time()
        fecha_hora_str = format_datetime_for_db(hora_colombia)
        
        cursor.execute("""
            INSERT INTO qrcode (code, remainingTurns, isActive, turnPackageId, qr_name)
            VALUES (%s, %s, %s, %s, %s)
        """, (nuevo_codigo, 0, 1, 1, ''))
        
        # Registrar automÃ¡ticamente en el historial
        cursor.execute("""
            INSERT INTO qrhistory (qr_code, user_id, user_name, local, fecha_hora, qr_name)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (nuevo_codigo, user_id, user_name, local, fecha_hora_str, ''))
        
        connection.commit()
        
        app.logger.info(f"Generado cÃ³digo QR: {nuevo_codigo} (contador: {nuevo_numero}) por {user_name}")
        
        return nuevo_codigo
        
    except Exception as e:
        app.logger.error(f"Error generando cÃ³digo QR: {e}")
        if connection:
            connection.rollback()
        return None
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/debug-generar-qr', methods=['POST'])
def debug_generar_qr():
    """Endpoint temporal para debug del generador QR"""
    try:
        data = request.get_json()
        cantidad = int(data.get('cantidad', 1))
        nombre = data.get('nombre', '')
        
        print(f"DEBUG: Intentando generar {cantidad} QR")
        
        codigos = generar_codigos_qr_lote(cantidad, nombre)
        
        if not codigos:
            return jsonify({
                'error': 'La funciÃ³n retornÃ³ lista vacÃ­a',
                'cantidad': cantidad,
                'nombre': nombre
            }), 500
            
        return jsonify({
            'success': True,
            'codigos': codigos,
            'cantidad': len(codigos)
        })
        
    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        print(f"ERROR DETALLADO: {error_detail}")
        return jsonify({
            'error': str(e),
            'traceback': error_detail
        }), 500
    
    # funciÃ³n para generar mÃºltiples QR con 4 cifras
def generar_codigos_qr_lote(cantidad_qr, nombre=""):
    """Generar mÃºltiples cÃ³digos QR con 4 cifras usando contador global con manejo de reinicio"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return []
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("""
            SELECT counter_value FROM globalcounter 
            WHERE counter_type = 'QR_CODE' 
            FOR UPDATE
        """)
        
        resultado = cursor.fetchone()
        
        if not resultado:
            
            cursor.execute("""
                INSERT INTO globalcounter (counter_type, counter_value, description) 
                VALUES ('QR_CODE', %s, 'Contador para cÃ³digos QR')
            """, (cantidad_qr,))
            numero_inicial = 1
            numero_final = cantidad_qr
        else:
            
            numero_inicial = resultado['counter_value'] + 1
            numero_final = resultado['counter_value'] + cantidad_qr
            
           
            if numero_final > 9999:
               
                numeros_antes_reinicio = 9999 - numero_inicial + 1
                
                numeros_despues_reinicio = cantidad_qr - numeros_antes_reinicio
                
              
                rango1_inicio = numero_inicial
                rango1_final = 9999
                rango2_inicio = 1
                rango2_final = numeros_despues_reinicio
                
                nuevo_valor_contador = numeros_despues_reinicio
                
               
                cursor.execute("""
                    UPDATE globalcounter 
                    SET counter_value = %s 
                    WHERE counter_type = 'QR_CODE'
                """, (nuevo_valor_contador,))
                
                codigos_generados = []
                
                user_id = session.get('user_id')
                user_name = session.get('user_name', 'Sistema')
                local = session.get('user_local', 'El Mekatiadero')
                hora_colombia = get_colombia_time()
                fecha_hora_str = format_datetime_for_db(hora_colombia)
                
               
                for i in range(rango1_inicio, rango1_final + 1):
                    nuevo_codigo = f"QR{i:04d}"
                    codigos_generados.append(nuevo_codigo)
                    
                    # Insertar en la tabla qrcode
                    cursor.execute("""
                        INSERT INTO qrcode (code, remainingTurns, isActive, turnPackageId, qr_name)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (nuevo_codigo, 0, 1, 1, nombre))
                    
                    # Registrar automÃ¡ticamente en el historial
                    cursor.execute("""
                        INSERT INTO qrhistory (qr_code, user_id, user_name, local, fecha_hora, qr_name)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (nuevo_codigo, user_id, user_name, local, fecha_hora_str, nombre))
                
                # Generar cÃ³digos del segundo rango (despuÃ©s del reinicio)
                for i in range(rango2_inicio, rango2_final + 1):
                    nuevo_codigo = f"QR{i:04d}"
                    codigos_generados.append(nuevo_codigo)
                    
                    # Insertar en la tabla qrcode
                    cursor.execute("""
                        INSERT INTO qrcode (code, remainingTurns, isActive, turnPackageId, qr_name)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (nuevo_codigo, 0, 1, 1, nombre))
                    
                    # Registrar automÃ¡ticamente en el historial
                    cursor.execute("""
                        INSERT INTO qrhistory (qr_code, user_id, user_name, local, fecha_hora, qr_name)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (nuevo_codigo, user_id, user_name, local, fecha_hora_str, nombre))
                
                connection.commit()
                
                app.logger.warning(f"Contador QR reiniciado automÃ¡ticamente al generar lote grande. Generados {cantidad_qr} cÃ³digos")
                app.logger.info(f"Generados {cantidad_qr} cÃ³digos QR: desde QR{rango1_inicio:04d} hasta QR{rango1_final:04d} y desde QR{rango2_inicio:04d} hasta QR{rango2_final:04d} por {user_name}")
                
                return codigos_generados
            else:
                # Actualizar el contador normalmente
                cursor.execute("""
                    UPDATE globalcounter 
                    SET counter_value = %s 
                    WHERE counter_type = 'QR_CODE'
                """, (numero_final,))
        
        codigos_generados = []
        
        user_id = session.get('user_id')
        user_name = session.get('user_name', 'Sistema')
        local = session.get('user_local', 'El Mekatiadero')
        hora_colombia = get_colombia_time()
        fecha_hora_str = format_datetime_for_db(hora_colombia)
        
        for i in range(numero_inicial, numero_final + 1):
            nuevo_codigo = f"QR{i:04d}"
            codigos_generados.append(nuevo_codigo)
            
            cursor.execute("""
                INSERT INTO qrcode (code, remainingTurns, isActive, turnPackageId, qr_name)
                VALUES (%s, %s, %s, %s, %s)
            """, (nuevo_codigo, 0, 1, 1, nombre))
            
            cursor.execute("""
                INSERT INTO qrhistory (qr_code, user_id, user_name, local, fecha_hora, qr_name)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (nuevo_codigo, user_id, user_name, local, fecha_hora_str, nombre))
        
        connection.commit()
        
        app.logger.info(f"Generados {cantidad_qr} cÃ³digos QR: desde QR{numero_inicial:04d} hasta QR{numero_final:04d} por {user_name}")
        
        return codigos_generados
        
    except Exception as e:
        app.logger.error(f"Error generando cÃ³digos QR en lote: {e}")
        if connection:
            connection.rollback()
        return []
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

def generar_codigos_qr_lote_con_paquete(cantidad_qr, nombre="", paquete_id=1):
    """Generar mÃºltiples cÃ³digos QR y asignar paquete desde el inicio (blindado contra duplicados)"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return []

        cursor = get_db_cursor(connection)

        cursor.execute(
            "SELECT turns, price, name FROM turnpackage WHERE id = %s",
            (paquete_id,)
        )
        paquete = cursor.fetchone()
        if not paquete:
            app.logger.error(f"Paquete {paquete_id} no encontrado")
            return []

        turns_paquete = paquete['turns']
        nombre_paquete = paquete['name']

        cursor.execute("""
            SELECT counter_value FROM globalcounter
            WHERE counter_type = 'QR_CODE'
            FOR UPDATE
        """)
        resultado = cursor.fetchone()

        if not resultado:
            cursor.execute("""
                INSERT INTO globalcounter (counter_type, counter_value, description)
                VALUES ('QR_CODE', 0, 'Contador para cÃ³digos QR')
            """)
            contador_bd = 0
        else:
            contador_bd = resultado['counter_value']

        cursor.execute("""
            SELECT MAX(CAST(SUBSTRING(code, 3) AS UNSIGNED)) AS max_real
            FROM qrcode
        """)
        max_real = cursor.fetchone()['max_real'] or 0

        contador_actual = max(contador_bd, max_real)

        numero_inicial = contador_actual + 1
        numero_final = contador_actual + cantidad_qr

        cursor.execute("""
            UPDATE globalcounter
            SET counter_value = %s
            WHERE counter_type = 'QR_CODE'
        """, (numero_final,))

        user_id = session.get('user_id')
        user_name = session.get('user_name', 'Sistema')
        local = session.get('user_local', 'El Mekatiadero')
        hora_colombia = get_colombia_time()
        fecha_hora_str = format_datetime_for_db(hora_colombia)

        codigos_generados = []

        for i in range(numero_inicial, numero_final + 1):
            nuevo_codigo = f"QR{i:04d}"
            codigos_generados.append(nuevo_codigo)

            cursor.execute("""
                INSERT INTO qrcode (code, remainingTurns, isActive, turnPackageId, qr_name)
                VALUES (%s, %s, %s, %s, %s)
            """, (nuevo_codigo, turns_paquete, 1, paquete_id, nombre))

            cursor.execute("""
                INSERT INTO userturns (qr_code_id, turns_remaining, total_turns, package_id)
                VALUES (LAST_INSERT_ID(), %s, %s, %s)
            """, (turns_paquete, turns_paquete, paquete_id))

            cursor.execute("""
                INSERT INTO qrhistory
                (qr_code, user_id, user_name, local, fecha_hora, qr_name, es_venta_real)
                VALUES (%s, %s, %s, %s, %s, %s, TRUE)
            """, (nuevo_codigo, user_id, user_name, local, fecha_hora_str, nombre))

        connection.commit()

        app.logger.info(
            f"Generados {cantidad_qr} QRs: QR{numero_inicial:04d} a QR{numero_final:04d} "
            f"con paquete {nombre_paquete} por {user_name}"
        )

        return codigos_generados

    except Exception as e:
        app.logger.error(f"Error generando cÃ³digos QR en lote con paquete: {e}")
        import traceback
        app.logger.error(traceback.format_exc())
        if connection:
            connection.rollback()
        return []

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/contador-qr/estado', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero'])
def obtener_estado_contador():
    """Obtener el estado actual del contador de QR con informaciÃ³n de reinicio"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("""
            SELECT 
                gc.counter_value,
                gc.description,
                gc.last_updated,
                COUNT(qc.id) as total_qr_registrados
            FROM globalcounter gc
            LEFT JOIN qrcode qc ON qc.code REGEXP '^QR[0-9]+$'
            WHERE gc.counter_type = 'QR_CODE'
        """)
        
        resultado = cursor.fetchone()
        
        if not resultado:
            return api_response('E002', http_status=404, data={'message': 'Contador no encontrado'})
        
        proximo_numero = resultado['counter_value'] + 1
        if proximo_numero > 9999:
            proximo_numero = 1
            proximo_codigo = f"QR{proximo_numero:04d}"
            reinicio_pendiente = True
        else:
            proximo_codigo = f"QR{proximo_numero:04d}"
            reinicio_pendiente = False
        
        codigos_disponibles = 9999 - resultado['counter_value']
        porcentaje_restante = (codigos_disponibles / 9999) * 100
        
        return api_response(
            'S001',
            status='success',
            data={
                'contador_actual': resultado['counter_value'],
                'proximo_codigo': proximo_codigo,
                'descripcion': resultado['description'],
                'ultima_actualizacion': resultado['last_updated'].isoformat() if resultado['last_updated'] else None,
                'total_qr_registrados': resultado['total_qr_registrados'],
                'limite_superior': 9999,
                'codigos_disponibles': codigos_disponibles,
                'porcentaje_restante': round(porcentaje_restante, 2),
                'reinicio_pendiente': reinicio_pendiente,
                'advertencia': reinicio_pendiente and 'Â¡El contador se reiniciarÃ¡ en el prÃ³ximo QR generado!'
            }
        )
        
    except Exception as e:
        app.logger.error(f"Error obteniendo estado del contador: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/contador-qr/reiniciar', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
@validate_required_fields(['nuevo_valor'])
def reiniciar_contador():
    """Reiniciar el contador de QR (solo administradores)"""
    try:
        data = request.get_json()
        nuevo_valor = int(data['nuevo_valor'])
        
        if nuevo_valor < 0 or nuevo_valor > 9999:
            return api_response('E005', http_status=400, data={'message': 'El valor debe estar entre 0 y 9999'})
        
        connection = None
        cursor = None
        try:
            connection = get_db_connection()
            if not connection:
                return api_response('E006', http_status=500)
                
            cursor = get_db_cursor(connection)
            
            cursor.execute("""
                UPDATE globalcounter 
                SET counter_value = %s 
                WHERE counter_type = 'QR_CODE'
            """, (nuevo_valor,))
            
            connection.commit()
            
            cursor.execute("SELECT counter_value FROM globalcounter WHERE counter_type = 'QR_CODE'")
            resultado = cursor.fetchone()
            
            app.logger.warning(f"Contador QR reiniciado manualmente a {nuevo_valor} por usuario {session.get('user_name')}")
            
            return api_response(
                'S003',
                status='success',
                data={
                    'nuevo_valor': resultado['counter_value'] if resultado else nuevo_valor,
                    'proximo_codigo': f"QR{(resultado['counter_value'] + 1 if resultado else nuevo_valor + 1):04d}",
                    'limite_superior': 9999,
                    'timestamp': get_colombia_time().isoformat()
                }
            )
            
        except Exception as e:
            app.logger.error(f"Error reiniciando contador: {e}")
            if connection:
                connection.rollback()
            return api_response('E001', http_status=500)
        finally:
            if cursor:
                cursor.close()
            if connection:
                connection.close()
                
    except ValueError:
        return api_response('E005', http_status=400, data={'message': 'Valor invÃ¡lido'})

def get_next_qr_number():
    """Obtener el prÃ³ximo nÃºmero de QR disponible"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return None
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("SELECT counter_value FROM globalcounter WHERE counter_type = 'QR_CODE'")
        resultado = cursor.fetchone()
        
        if resultado:
            return resultado['counter_value'] + 1
        else:
            
            return 1
            
    except Exception as e:
        app.logger.error(f"Error obteniendo prÃ³ximo nÃºmero QR: {e}")
        return None
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/generar-qr', methods=['POST'])
@handle_api_errors
@require_login(['admin', 'cajero'])
@validate_required_fields(['cantidad'])
def generar_qr():
    """Generar nuevos cÃ³digos QR con 4 cifras"""
    try:
        app.logger.info(f"SESSION EN generar-qr: {dict(session)}")
        data = request.get_json()
        cantidad = int(data['cantidad'])
        nombre = data.get('nombre', '')
        paquete_id = data.get('paquete_id')
        
        if cantidad <= 0 or cantidad > 1000:
            return api_response(
                'E005', 
                http_status=400, 
                data={'message': 'Cantidad debe estar entre 1 y 1000'}
            )
        
        if cantidad > 9999:
            return api_response(
                'E005',
                http_status=400,
                data={'message': 'No se pueden generar mÃ¡s de 9999 cÃ³digos a la vez'}
            )
       
        if paquete_id:
            
            codigos_generados = generar_codigos_qr_lote_con_paquete(cantidad, nombre, paquete_id)
            
            if not codigos_generados:
                return api_response('E001', http_status=500)
            
            connection = get_db_connection()
            if not connection:
                return api_response('E006', http_status=500)
                
            cursor = get_db_cursor(connection)
            cursor.execute("SELECT * FROM turnpackage WHERE id = %s", (paquete_id,))
            paquete = cursor.fetchone()
            cursor.close()
            connection.close()
            
            if not paquete:
                return api_response('Q004', http_status=404, data={'paquete_id': paquete_id})
            
            response_data = {
                'codigos': codigos_generados,
                'cantidad': len(codigos_generados),
                'nombre': nombre,
                'paquete_id': paquete_id,
                'paquete_nombre': paquete['name'],
                'paquete_precio': float(paquete['price']),
                'paquete_turnos': paquete['turns'],
                'formato': 'QRXXXX (4 dÃ­gitos, de QR0001 a QR9999)',
                'nota': 'El contador se reiniciarÃ¡ automÃ¡ticamente al llegar a QR9999'
            }
            
            app.logger.info(f"Generados {len(codigos_generados)} cÃ³digos QR con paquete {paquete['name']}")
            
            return api_response(
                'S002',
                status='success',
                data=response_data
            )
        else:
            
            codigos_generados = generar_codigos_qr_lote(cantidad, nombre)
            
            if not codigos_generados:
                return api_response('E001', http_status=500)
            
            app.logger.info(f"Generados {len(codigos_generados)} cÃ³digos QR sin paquete")
            
            return api_response(
                'S002',
                status='success',
                data={
                    'codigos': codigos_generados,
                    'cantidad': len(codigos_generados),
                    'nombre': nombre,
                    'formato': 'QRXXXX (4 dÃ­gitos, de QR0001 a QR9999)',
                    'nota': 'El contador se reiniciarÃ¡ automÃ¡ticamente al llegar a QR9999'
                }
            )
        
    except Exception as e:
        app.logger.error(f"Error generando QR: {e}")
        return api_response('E001', http_status=500)

@app.route('/api/obtener-siguiente-qr', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero'])
def obtener_siguiente_qr():
    """Obtener el siguiente cÃ³digo QR disponible con manejo de reinicio"""
    siguiente_codigo = generar_codigo_qr()
    
    if not siguiente_codigo:
        return api_response('E001', http_status=500)
    
    numero_qr = int(siguiente_codigo[2:])
    
    return api_response(
        'S001',
        status='success',
        data={
            'siguiente_codigo': siguiente_codigo,
            'numero_qr': numero_qr,
            'es_reinicio': numero_qr == 1,
            'mensaje': 'Â¡Contador reiniciado!' if numero_qr == 1 else None
        }
    )

@app.route('/api/paquetes', methods=['GET'])
@handle_api_errors
def obtener_paquetes():
    """Obtener todos los paquetes disponibles"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        cursor.execute("SELECT * FROM turnpackage ORDER BY id")
        return jsonify(cursor.fetchall())
    except Exception as e:
        app.logger.error(f"Error obteniendo paquetes: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/asignar-paquete', methods=['POST'])
@handle_api_errors
@require_login(['admin', 'cajero'])
@validate_required_fields(['codigo_qr', 'paquete_id'])
def asignar_paquete():
    """Asignar un paquete a un cÃ³digo QR"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        codigo_qr = data['codigo_qr']
        paquete_id = data['paquete_id']
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("SELECT turnPackageId FROM qrcode WHERE code = %s", (codigo_qr,))
        qr_existente = cursor.fetchone()
        
        if qr_existente and qr_existente['turnPackageId'] is not None and qr_existente['turnPackageId'] != 1:
            cursor.execute("SELECT name FROM turnpackage WHERE id = %s", (qr_existente['turnPackageId'],))
            paquete_actual = cursor.fetchone()
            paquete_nombre = paquete_actual['name'] if paquete_actual else 'Desconocido'
            
            return api_response(
                'Q002',
                http_status=400,
                data={
                    'paquete_actual': paquete_nombre,
                    'qr_code': codigo_qr
                }
            )

        cursor.execute("SELECT turns, price FROM turnpackage WHERE id = %s", (paquete_id,))
        paquete = cursor.fetchone()
        if not paquete:
            return api_response('Q004', http_status=404)

        turns, price = paquete['turns'], paquete['price']

        cursor.execute("SELECT id FROM qrcode WHERE code = %s", (codigo_qr,))
        qr_existente = cursor.fetchone()
        
        if not qr_existente:
            cursor.execute("""
                INSERT INTO qrcode (code, remainingTurns, isActive, turnPackageId)
                VALUES (%s, %s, 1, %s)
            """, (codigo_qr, turns, paquete_id))
            connection.commit()
            qr_id = cursor.lastrowid
        else:
            qr_id = qr_existente['id']
            cursor.execute("""
                UPDATE qrcode
                SET remainingTurns = remainingTurns + %s,
                    turnPackageId = %s
                WHERE id = %s
            """, (turns, paquete_id, qr_id))
            connection.commit()
        
        cursor.execute("""
            INSERT INTO userturns (qr_code_id, turns_remaining, total_turns, package_id)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE 
                turns_remaining = turns_remaining + %s,
                total_turns = total_turns + %s,
                package_id = %s
        """, (qr_id, turns, turns, paquete_id, turns, turns, paquete_id))
        
        connection.commit()
        
        app.logger.info(f"Paquete {paquete_id} asignado a QR {codigo_qr}")
        
        return api_response(
            'S002',
            status='success',
            data={
                'turns': turns,
                'price': price,
                'qr_id': qr_id,
                'paquete_id': paquete_id
            }
        )
        
    except Exception as e:
        app.logger.error(f"Error asignando paquete: {e}")
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/verificar-qr/<qr_code>', methods=['GET'])
@handle_api_errors
def verificar_qr(qr_code):
    """Verificar informaciÃ³n de un cÃ³digo QR"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        cursor.execute("SELECT id, code, remainingTurns, isActive, turnPackageId FROM qrcode WHERE code = %s", (qr_code,))
        qr_data = cursor.fetchone()
        
        if not qr_data:
            return api_response('Q001', http_status=404, data={'qr_code': qr_code})
        
        qr_id = qr_data['id']
        tiene_paquete = qr_data['turnPackageId'] is not None and qr_data['turnPackageId'] != 1
        
        cursor.execute("""
            SELECT ut.*, tp.name as package_name, tp.turns, tp.price
            FROM userturns ut
            LEFT JOIN turnpackage tp ON ut.package_id = tp.id
            WHERE ut.qr_code_id = %s
        """, (qr_id,))
        resultado = cursor.fetchone()
        
        response_data = {
            'existe': True,
            'tiene_paquete': tiene_paquete,
            'qr_code': qr_code,
            'turnPackageId': qr_data['turnPackageId']
        }
        
        if resultado:
            response_data.update({
                'turns_remaining': resultado['turns_remaining'],
                'total_turns': resultado['total_turns'],
                'package_name': resultado['package_name'],
                'package_turns': resultado['turns'],
                'package_price': resultado['price']
            })
        else:
            response_data.update({
                'turns_remaining': 0,
                'total_turns': 0,
                'package_name': 'Sin paquete',
                'package_turns': 0,
                'package_price': 0
            })
        
        return jsonify(response_data)
            
    except Exception as e:
        app.logger.error(f"Error verificando QR: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/registrar-uso', methods=['POST'])
@handle_api_errors
@validate_required_fields(['qr_code', 'machine_id'])
def registrar_uso():
    """Registrar uso de un turno"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        qr_code = data['qr_code']
        machine_id = data['machine_id']
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("SELECT id FROM qrcode WHERE code = %s", (qr_code,))
        qr_data = cursor.fetchone()
        if not qr_data:
            return api_response('Q001', http_status=404)
        
        qr_id = qr_data['id']
        cursor.execute("SELECT turns_remaining FROM userturns WHERE qr_code_id = %s", (qr_id,))
        turnos_data = cursor.fetchone()
        
        if not turnos_data or turnos_data['turns_remaining'] <= 0:
            return api_response('Q003', http_status=400)
        
        station_index = data.get('station_index', None)

        turns_after = turnos_data['turns_remaining'] - 1

        # Insertar turno usado (con station_index y turns_remaining_after si las columnas existen)
        try:
            cursor.execute(
                "INSERT INTO turnusage (qrCodeId, machineId, station_index, turns_remaining_after) VALUES (%s, %s, %s, %s)",
                (qr_id, machine_id, station_index, turns_after)
            )
        except Exception:
            try:
                cursor.execute(
                    "INSERT INTO turnusage (qrCodeId, machineId, station_index) VALUES (%s, %s, %s)",
                    (qr_id, machine_id, station_index)
                )
            except Exception:
                cursor.execute("INSERT INTO turnusage (qrCodeId, machineId) VALUES (%s, %s)", (qr_id, machine_id))

        cursor.execute("UPDATE userturns SET turns_remaining = turns_remaining - 1 WHERE qr_code_id = %s", (qr_id,))

        # Resetear contador de fallas consecutivas para esta estaciÃ³n (juego exitoso = contador a 0)
        station_key = str(station_index) if station_index is not None else 'all'
        try:
            cursor.execute("SELECT consecutive_failures FROM machine WHERE id = %s", (machine_id,))
            maq = cursor.fetchone()
            if maq:
                contadores = json.loads(maq['consecutive_failures'] or '{}')
                if contadores.get(station_key, 0) > 0:
                    contadores[station_key] = 0
                    cursor.execute(
                        "UPDATE machine SET consecutive_failures = %s WHERE id = %s",
                        (json.dumps(contadores), machine_id)
                    )
        except Exception as e:
            app.logger.warning(f"No se pudo resetear consecutive_failures: {e}")

        connection.commit()

        app.logger.info(f"Turno usado - QR: {qr_code}, MÃ¡quina: {machine_id}, EstaciÃ³n: {station_index}")
        
        return api_response(
            'S010',
            status='success',
            data={
                'turns_remaining': turnos_data['turns_remaining'] - 1
            }
        )
        
    except Exception as e:
        app.logger.error(f"Error registrando uso: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/reportar-falla', methods=['POST'])
@handle_api_errors
@validate_required_fields(['qr_code', 'turnos_devueltos'])
def reportar_falla():
    """Reportar falla desde ESP32: devuelve turnos, cuenta fallas consecutivas y actualiza estado."""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        qr_code          = data['qr_code']
        machine_id       = data.get('machine_id', 0)
        machine_name     = data.get('machine_name', 'Sistema')
        turnos_devueltos = data['turnos_devueltos']
        is_forced        = data.get('is_forced', False)
        forced_by        = data.get('forced_by', '')
        notes            = data.get('notes', '')
        station_index    = data.get('station_index', None)   # nuevo: Ã­ndice de estaciÃ³n

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)

        # â”€â”€ Verificar QR â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        cursor.execute("SELECT id FROM qrcode WHERE code = %s", (qr_code,))
        qr_data = cursor.fetchone()
        if not qr_data:
            return api_response('Q001', http_status=404)
        qr_id = qr_data['id']

        cursor.execute("SELECT turns_remaining FROM userturns WHERE qr_code_id = %s", (qr_id,))
        turnos_data = cursor.fetchone()
        if not turnos_data:
            return api_response('Q003', http_status=400)

        turnos_originales = turnos_data['turns_remaining']
        nuevos_turnos     = turnos_originales + turnos_devueltos
        actual_machine_id   = None if machine_id == 0 else machine_id
        actual_machine_name = 'DevoluciÃ³n Manual' if machine_id == 0 else machine_name

        # â”€â”€ Insertar en machinefailures â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        cursor.execute("DESCRIBE machinefailures")
        columnas_existentes = [col['Field'] for col in cursor.fetchall()]

        campos = ['qr_code_id', 'machine_name', 'turnos_devueltos']
        valores = [qr_id, actual_machine_name, turnos_devueltos]
        if 'machine_id'    in columnas_existentes: campos.append('machine_id');    valores.append(actual_machine_id)
        if 'notes'         in columnas_existentes: campos.append('notes');         valores.append(notes or None)
        if 'is_forced'     in columnas_existentes: campos.append('is_forced');     valores.append(1 if is_forced else 0)
        if 'forced_by'     in columnas_existentes: campos.append('forced_by');     valores.append(forced_by or None)
        if 'station_index' in columnas_existentes: campos.append('station_index'); valores.append(station_index)

        placeholders = ', '.join(['%s'] * len(campos))
        cursor.execute(f"INSERT INTO machinefailures ({', '.join(campos)}) VALUES ({placeholders})", valores)

        # â”€â”€ Devolver turnos (SIEMPRE, incluyendo la 3Âª falla) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        cursor.execute("UPDATE userturns SET turns_remaining = %s WHERE qr_code_id = %s",
                       (nuevos_turnos, qr_id))

        # â”€â”€ Contar fallas consecutivas y actualizar estado de mÃ¡quina â”€â”€â”€â”€â”€â”€â”€â”€â”€
        fallas_consecutivas = 0
        station_en_mantenimiento = False
        if actual_machine_id:
            # Clave de estaciÃ³n en el JSON de la mÃ¡quina
            station_key = str(station_index) if station_index is not None else 'all'

            # Leer contadores actuales
            cursor.execute(
                "SELECT consecutive_failures, stations_in_maintenance, machine_subtype "
                "FROM machine WHERE id = %s",
                (actual_machine_id,)
            )
            maq = cursor.fetchone()
            if maq:
                try:
                    contadores = json.loads(maq['consecutive_failures'] or '{}')
                except Exception:
                    contadores = {}
                try:
                    en_mant = json.loads(maq['stations_in_maintenance'] or '[]')
                except Exception:
                    en_mant = []
                machine_subtype = maq.get('machine_subtype', 'simple') or 'simple'

                # Incrementar contador de esta estaciÃ³n
                contadores[station_key] = contadores.get(station_key, 0) + 1
                fallas_consecutivas = contadores[station_key]

                updates = {"consecutive_failures": json.dumps(contadores)}

                if fallas_consecutivas >= 3:
                    # Marcar estaciÃ³n como en mantenimiento
                    station_en_mantenimiento = True
                    if station_key not in [str(s) for s in en_mant]:
                        en_mant.append(station_index if station_index is not None else 'all')
                    updates["stations_in_maintenance"] = json.dumps(en_mant)
                    updates["errorNote"] = f"Falla estaciÃ³n {station_key} â€” 3 fallos consecutivos"

                    # Determinar si toda la mÃ¡quina queda en mantenimiento
                    if machine_subtype == 'multi_station':
                        # Obtener cuÃ¡ntas estaciones tiene la mÃ¡quina
                        cursor.execute(
                            "SELECT JSON_LENGTH(station_names) as n_stations FROM machine WHERE id = %s",
                            (actual_machine_id,)
                        )
                        row = cursor.fetchone()
                        n_stations = row['n_stations'] if row and row['n_stations'] else 2
                        # Verificar cuÃ¡ntas estaciones distintas estÃ¡n en mantenimiento
                        stations_bloqueadas = set()
                        for s in en_mant:
                            stations_bloqueadas.add(str(s))
                        if len(stations_bloqueadas) >= n_stations:
                            updates["status"] = "mantenimiento"
                        # Si solo una estÃ¡ en mantenimiento, la mÃ¡quina sigue activa
                        # pero la estaciÃ³n individual ya estÃ¡ marcada
                    else:
                        # MÃ¡quina simple â†’ toda la mÃ¡quina pasa a mantenimiento
                        updates["status"] = "mantenimiento"

                    # Encolar MAINTENANCE al ESP32
                    try:
                        cursor.execute("""
                            INSERT INTO esp32_commands
                            (machine_id, command, parameters, triggered_by, status, triggered_at)
                            VALUES (%s, 'MAINTENANCE', %s, 'auto_falla_esp32', 'queued', NOW())
                        """, (actual_machine_id, json.dumps({
                            'station_index': station_index,
                            'station_key': station_key,
                            'fallas_consecutivas': fallas_consecutivas
                        })))
                    except Exception as cmd_err:
                        app.logger.error(f"No se pudo encolar MAINTENANCE: {cmd_err}")

                # Aplicar updates a machine
                set_parts  = [f"{k} = %s" for k in updates]
                set_values = list(updates.values()) + [actual_machine_id]
                cursor.execute(
                    f"UPDATE machine SET {', '.join(set_parts)} WHERE id = %s",
                    set_values
                )

        if is_forced:
            try:
                cursor.execute("""
                    INSERT INTO error_logs
                    (error_type, error_message, module, user_id, request_path)
                    VALUES (%s, %s, %s, %s, %s)
                """, (
                    'REFUND_FORCED',
                    f'DevoluciÃ³n forzada: QR={qr_code}, Turnos={turnos_devueltos}, Por={forced_by}',
                    'packfailure', session.get('user_id'), '/api/reportar-falla'
                ))
            except Exception as e:
                app.logger.error(f"Error registrando en error_logs: {e}")

        connection.commit()

        app.logger.info(
            f"âœ… Falla reportada â€” QR={qr_code} maquina={actual_machine_id} "
            f"estacion={station_index} consecutivas={fallas_consecutivas} "
            f"turnos_devueltos={turnos_devueltos}"
        )

        return api_response(
            'S003',
            status='success',
            data={
                'nuevos_turnos': nuevos_turnos,
                'is_forced': is_forced,
                'machine_id': actual_machine_id,
                'qr_code': qr_code,
                'turnos_originales': turnos_originales,
                'turnos_devueltos': turnos_devueltos,
                'fallas_consecutivas': fallas_consecutivas,
                'station_en_mantenimiento': station_en_mantenimiento,
            }
        )
        
    except mysql.connector.Error as e:
        app.logger.error(f"Error MySQL reportando falla: {e}")
        if connection:
            connection.rollback()
        
        try:
            app.logger.info("Intentando inserciÃ³n mÃ­nima...")
            cursor.execute("""
                INSERT INTO machinefailures (qr_code_id, machine_name, turnos_devueltos)
                VALUES (%s, %s, %s)
            """, (qr_id, 'Sistema', turnos_devueltos))
            
            cursor.execute("UPDATE userturns SET turns_remaining = turns_remaining + %s WHERE qr_code_id = %s",
                           (turnos_devueltos, qr_id))
            
            connection.commit()
            
            return api_response(
                'S003',
                status='success',
                data={
                    'nuevos_turnos': turnos_originales + turnos_devueltos,
                    'is_forced': is_forced,
                    'machine_id': None,
                    'qr_code': qr_code,
                    'note': 'InserciÃ³n mÃ­nima exitosa'
                }
            )
        except Exception as retry_error:
            app.logger.error(f"Error en inserciÃ³n mÃ­nima: {retry_error}")
            return api_response('E001', http_status=500, data={'mysql_error': str(e)})
        
    except Exception as e:
        app.logger.error(f"Error reportando falla: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/historial-fallas', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero'])
def obtener_historial_fallas():
    """Obtener historial de fallas"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        cursor.execute("""
            SELECT mf.*, qr.code as qr_code, ut.turns_remaining, ut.total_turns
            FROM machinefailures mf
            JOIN qrcode qr ON mf.qr_code_id = qr.id
            JOIN userturns ut ON mf.qr_code_id = ut.qr_code_id
            ORDER BY mf.reported_at DESC
            LIMIT 50
        """)
        return jsonify(cursor.fetchall())
    except Exception as e:
        app.logger.error(f"Error obteniendo historial de fallas: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/guardar-qr', methods=['POST'])
@handle_api_errors
@require_login(['admin', 'cajero'])
@validate_required_fields(['qr_code'])
def guardar_qr():
    """Guardar QR en historial - CONSULTA o VENTA"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        qr_code = data['qr_code']
        user_id = session.get('user_id')
        user_name = session.get('user_name', 'Usuario')
        local = session.get('user_local', 'El Mekatiadero')
        es_venta_real = data.get('es_venta_real', False)
        es_consulta = data.get('es_consulta', False)

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)

        hora_colombia = get_colombia_time()
        fecha_hora_str = format_datetime_for_db(hora_colombia)
        
        cursor.execute("SELECT qr_name, turnPackageId FROM qrcode WHERE code = %s", (qr_code,))
        qr_data = cursor.fetchone()
        qr_name = qr_data['qr_name'] if qr_data and 'qr_name' in qr_data else None
        
  
        tiene_paquete = qr_data and qr_data['turnPackageId'] is not None and qr_data['turnPackageId'] != 1
        
        es_venta = False
        if es_venta_real and not es_consulta and tiene_paquete:
            es_venta = True
        
        cursor.execute("""
            INSERT INTO qrhistory (qr_code, user_id, user_name, local, fecha_hora, qr_name, es_venta_real)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (qr_code, user_id, user_name, local, fecha_hora_str, qr_name, es_venta_real))
        
        connection.commit()
        
        if es_venta:
            actualizar_contador_diario(hora_colombia.strftime('%Y-%m-%d'))
            app.logger.info(f"VENTA REAL registrada: {qr_code} por {user_name}")
            mensaje = "Venta registrada"
        else:
            
            app.logger.info(f"CONSULTA registrada: {qr_code} por {user_name}")
            mensaje = "Consulta registrada"
        
        return api_response(
            'S006',
            status='success',
            data={
                'qr_name': qr_name,
                'es_venta': es_venta,
                'es_venta_real': es_venta_real,
                'es_consulta': es_consulta,
                'tiene_paquete': tiene_paquete,
                'mensaje': mensaje,
                'timestamp': hora_colombia.strftime('%Y-%m-%d %H:%M:%S')
            }
        )
    except Exception as e:
        app.logger.error(f"Error guardando QR en historial: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/verificar-venta-existente/<qr_code>', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero'])
def verificar_venta_existente(qr_code):
    """Verificar si ya existe una venta real para este QR"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("""
            SELECT 
                COUNT(*) as existe_venta,
                MAX(fecha_hora) as ultima_venta_fecha,
                COUNT(*) as total_ventas
            FROM qrhistory 
            WHERE qr_code = %s 
            AND es_venta_real = TRUE
        """, (qr_code,))
        
        venta_info = cursor.fetchone()
        
        cursor.execute("SELECT qr_name, turnPackageId FROM qrcode WHERE code = %s", (qr_code,))
        qr_info = cursor.fetchone()
        
        existe_venta = venta_info['existe_venta'] > 0
        
        return jsonify({
            'existe_venta': existe_venta,
            'total_ventas': venta_info['total_ventas'] or 0,
            'ultima_venta_fecha': venta_info['ultima_venta_fecha'].isoformat() if venta_info['ultima_venta_fecha'] else None,
            'qr_tiene_paquete': qr_info and qr_info['turnPackageId'] is not None and qr_info['turnPackageId'] != 1,
            'qr_nombre': qr_info['qr_name'] if qr_info and 'qr_name' in qr_info else None
        })
        
    except Exception as e:
        app.logger.error(f"Error verificando venta existente: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/guardar-multiples-qr-con-paquete', methods=['POST'])
@handle_api_errors
@require_login(['admin', 'cajero'])
@validate_required_fields(['qr_codes', 'paquete_id'])
def guardar_multiples_qr_con_paquete():
    """Guardar mÃºltiples QR con paquete como VENTAS REALES"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        qr_codes = data['qr_codes']
        nombre = data.get('nombre', '')
        paquete_id = data['paquete_id']
        paquete_nombre = data.get('paquete_nombre', '')
        paquete_precio = data.get('paquete_precio', 0)
        paquete_turns = data.get('paquete_turns', 0)
        es_venta_real = data.get('es_venta_real', True)  
        
        user_id = session.get('user_id')
        user_name = session.get('user_name', 'Usuario')
        local = session.get('user_local', 'El Mekatiadero')

        if not qr_codes:
            return api_response('E005', http_status=400, data={'message': 'Lista de QR vacÃ­a'})

        app.logger.info(f"Guardando {len(qr_codes)} QR con paquete {paquete_nombre}")

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)

        hora_colombia = get_colombia_time()
        fecha_hora_str = format_datetime_for_db(hora_colombia)

        qrs_creados = 0
        qrs_actualizados = 0
        
        for qr_code in qr_codes:
            cursor.execute("SELECT id FROM qrcode WHERE code = %s", (qr_code,))
            qr_existente = cursor.fetchone()
            
            if not qr_existente:
                cursor.execute("""
                    INSERT INTO qrcode (code, remainingTurns, isActive, turnPackageId, qr_name)
                    VALUES (%s, %s, %s, %s, %s)
                """, (qr_code, paquete_turns, 1, paquete_id, nombre))
                
                qr_id = cursor.lastrowid
                
                cursor.execute("""
                    INSERT INTO userturns (qr_code_id, turns_remaining, total_turns, package_id)
                    VALUES (%s, %s, %s, %s)
                """, (qr_id, paquete_turns, paquete_turns, paquete_id))
                
                qrs_creados += 1
            else:
                qr_id = qr_existente['id']
                
                cursor.execute("SELECT turnPackageId FROM qrcode WHERE id = %s", (qr_id,))
                qr_info = cursor.fetchone()
                
                if qr_info['turnPackageId'] is not None and qr_info['turnPackageId'] != 1:
                    continue
                
                cursor.execute("""
                    UPDATE qrcode 
                    SET remainingTurns = %s, turnPackageId = %s, qr_name = %s
                    WHERE id = %s
                """, (paquete_turns, paquete_id, nombre, qr_id))
                
                cursor.execute("SELECT id FROM userturns WHERE qr_code_id = %s", (qr_id,))
                user_turns_existente = cursor.fetchone()
                
                if user_turns_existente:
                    cursor.execute("""
                        UPDATE userturns 
                        SET turns_remaining = %s, total_turns = %s, package_id = %s
                        WHERE qr_code_id = %s
                    """, (paquete_turns, paquete_turns, paquete_id, qr_id))
                else:
                    cursor.execute("""
                        INSERT INTO userturns (qr_code_id, turns_remaining, total_turns, package_id)
                        VALUES (%s, %s, %s, %s)
                    """, (qr_id, paquete_turns, paquete_turns, paquete_id))
                
                qrs_actualizados += 1
            
            cursor.execute("""
                INSERT INTO qrhistory (qr_code, user_id, user_name, local, fecha_hora, qr_name, es_venta_real)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (qr_code, user_id, user_name, local, fecha_hora_str, nombre, es_venta_real))

        connection.commit()
        
        if es_venta_real and qr_codes:
            actualizar_contador_diario(hora_colombia.strftime('%Y-%m-%d'))
        
        total_qrs = qrs_creados + qrs_actualizados
        
        app.logger.info(f"{total_qrs} QR generados como VENTAS REALES con paquete {paquete_nombre}")

        return api_response(
            'S002',
            status='success',
            data={
                'count': total_qrs,
                'nombre': nombre,
                'paquete': paquete_nombre,
                'precio': paquete_precio,
                'turns': paquete_turns,
                'creados': qrs_creados,
                'actualizados': qrs_actualizados,
                'es_venta_real': es_venta_real,
                'mensaje': f'{total_qrs} QR registrados como VENTAS REALES'
            }
        )
        
    except Exception as e:
        app.logger.error(f"Error guardando mÃºltiples QR con paquete: {e}")
        sentry_sdk.capture_exception(e)
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/guardar-multiples-qr', methods=['POST'])
@handle_api_errors
@require_login(['admin', 'cajero'])
@validate_required_fields(['qr_codes'])
def guardar_multiples_qr():
    """Agregar QR generados en lote al historial"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        qr_codes = data['qr_codes']
        nombre = data.get('nombre', '')
        es_venta_real = data.get('es_venta_real', False)
        
        user_id = session.get('user_id')
        user_name = session.get('user_name', 'Usuario')
        local = session.get('user_local', 'El Mekatiadero')

        app.logger.info(f"Guardando {len(qr_codes)} QR con nombre: {nombre}")

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)

        hora_colombia = get_colombia_time()
        fecha_hora_str = format_datetime_for_db(hora_colombia)

        for qr_code in qr_codes:
            cursor.execute("SELECT id FROM qrcode WHERE code = %s", (qr_code,))
            qr_existente = cursor.fetchone()
            
            if not qr_existente:
                cursor.execute("""
                    INSERT INTO qrcode (code, remainingTurns, isActive, turnPackageId, qr_name)
                    VALUES (%s, %s, %s, %s, %s)
                """, (qr_code, 0, 1, 1, nombre))
            else:
                cursor.execute("""
                    UPDATE qrcode SET qr_name = %s WHERE code = %s
                """, (nombre, qr_code))
            
            cursor.execute("""
                INSERT INTO qrhistory (qr_code, user_id, user_name, local, fecha_hora, qr_name, es_venta_real)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (qr_code, user_id, user_name, local, fecha_hora_str, nombre, es_venta_real))

        connection.commit()
        
        if es_venta_real and qr_codes:
            actualizar_contador_diario(hora_colombia.strftime('%Y-%m-%d'))
        
        app.logger.info(f"{len(qr_codes)} QR guardados con nombre: {nombre}")

        return api_response(
            'S002',
            status='success',
            data={
                'count': len(qr_codes),
                'nombre': nombre,
                'es_venta_real': es_venta_real
            }
        )
        
    except Exception as e:
        app.logger.error(f"Error guardando mÃºltiples QR: {e}")
        sentry_sdk.capture_exception(e)
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/estadisticas/tiempo-real', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
def obtener_estadisticas_tiempo_real():
    """Obtener estadÃ­sticas en tiempo real (sin cache)"""
    connection = None
    cursor = None
    try:
        fecha = request.args.get('fecha', get_colombia_time().strftime('%Y-%m-%d'))
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("""
            SELECT COUNT(DISTINCT qh.qr_code) as vendidos_hoy,
                   COALESCE(SUM(tp.price), 0) as valor_hoy
            FROM qrhistory qh
            JOIN qrcode qr ON qr.code = qh.qr_code
            LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) = %s
            AND qr.turnPackageId IS NOT NULL
            AND qr.turnPackageId != 1
            AND qh.es_venta_real = TRUE
        """, (fecha,))
        
        ventas = cursor.fetchone()
        
        cursor.execute("""
            SELECT COUNT(*) as escaneados_hoy
            FROM qrhistory
            WHERE DATE(fecha_hora) = %s
        """, (fecha,))
        
        escaneados = cursor.fetchone()
        
        cursor.execute("""
            SELECT COUNT(*) as turnos_hoy
            FROM turnusage
            WHERE DATE(usedAt) = %s
        """, (fecha,))
        
        turnos = cursor.fetchone()
        
        cursor.execute("""
            SELECT COUNT(*) as qr_generados_hoy
            FROM qrcode
            WHERE DATE(createdAt) = %s
        """, (fecha,))
        
        generados = cursor.fetchone()
        
        cursor.execute("SELECT counter_value FROM globalcounter WHERE counter_type = 'QR_CODE'")
        contador_qr = cursor.fetchone()
        
        return jsonify({
            'fecha': fecha,
            'ventas': {
                'vendidos': ventas['vendidos_hoy'] or 0,
                'valor': float(ventas['valor_hoy'] or 0)
            },
            'escaneados': escaneados['escaneados_hoy'] or 0,
            'turnos': turnos['turnos_hoy'] or 0,
            'generados': generados['qr_generados_hoy'] or 0,
            'contador_qr_actual': contador_qr['counter_value'] if contador_qr else 0,
            'proximo_qr': f"QR{(contador_qr['counter_value'] + 1 if contador_qr else 1):04d}",
            'timestamp': get_colombia_time().isoformat()
        })
        
    except Exception as e:
        app.logger.error(f"Error obteniendo estadÃ­sticas tiempo real: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# ==================== APIS PARA HISTORIAL ====================

# Migrado a blueprints/historial/routes.py
# @app.route('/api/historial-completo', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
def obtener_historial_completo():
    """Obtener historial completo de QR escaneados"""
    connection = None
    cursor = None
    try:
        user_id = session.get('user_id')
        local = session.get('user_local', 'El Mekatiadero')
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        if session.get('user_role') == 'admin':
            cursor.execute("""
                SELECT 
                    h.id,
                    h.qr_code,
                    h.user_name,
                    h.qr_name,
                    h.fecha_hora,
                    qr.turnPackageId,
                    tp.name as package_name,
                    tp.price as precio_paquete,
                    ut.turns_remaining
                FROM qrhistory h
                LEFT JOIN qrcode qr ON qr.code = h.qr_code
                LEFT JOIN userturns ut ON ut.qr_code_id = qr.id
                LEFT JOIN turnpackage tp ON tp.id = qr.turnPackageId
               WHERE h.local = %s
               AND h.es_venta_real = TRUE
               ORDER BY h.fecha_hora DESC
            LIMIT 100
            """, (local,))
        else:
            cursor.execute("""
                SELECT 
                    h.id,
                    h.qr_code,
                    h.user_name,
                    h.qr_name,
                    h.fecha_hora,
                    qr.turnPackageId,
                    tp.name as package_name,
                    tp.price as precio_paquete,
                    ut.turns_remaining
                FROM qrhistory h
                LEFT JOIN qrcode qr ON qr.code = h.qr_code
                LEFT JOIN userturns ut ON ut.qr_code_id = qr.id
                LEFT JOIN turnpackage tp ON tp.id = qr.turnPackageId
                WHERE (h.user_id = %s OR h.local = %s)
                AND h.es_venta_real = TRUE
                ORDER BY h.fecha_hora DESC
                LIMIT 50
            """, (user_id, local))
        
        historial = cursor.fetchall()
        
        for item in historial:
            if item['fecha_hora']:
                try:
                    fecha_colombia = parse_db_datetime(item['fecha_hora'])
                    item['fecha_hora'] = fecha_colombia.strftime('%Y-%m-%d %H:%M:%S')
                except Exception as e:
                    app.logger.warning(f"Error formateando fecha: {e}")
                    item['fecha_hora'] = str(item['fecha_hora'])
           
            item['es_venta'] = item['turnPackageId'] is not None and item['turnPackageId'] != 1
        
        app.logger.info(f"Historial obtenido: {len(historial)} registros")
        return jsonify(historial)
        
    except Exception as e:
        app.logger.error(f"Error obteniendo historial completo: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# Migrado a blueprints/historial/routes.py
# @app.route('/api/historial-qr/<qr_code>', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
def obtener_historial_qr(qr_code):
    """Obtener historial especÃ­fico de un cÃ³digo QR"""
    connection = None
    cursor = None
    try:
        app.logger.info(f"Obteniendo historial para QR: {qr_code}")
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("""
            SELECT 
                h.id,
                h.qr_code,
                h.user_name,
                h.qr_name,
                h.fecha_hora,
                qr.turnPackageId,
                tp.name as package_name,
                tp.price as precio_paquete,
                ut.turns_remaining
            FROM qrhistory h
            LEFT JOIN qrcode qr ON qr.code = h.qr_code
            LEFT JOIN userturns ut ON ut.qr_code_id = qr.id
            LEFT JOIN turnpackage tp ON tp.id = qr.turnPackageId
            WHERE h.qr_code = %s
            ORDER BY h.fecha_hora DESC
            LIMIT 20
        """, (qr_code,))
        
        historial = cursor.fetchall()
        
        for item in historial:
            if item['fecha_hora']:
                try:
                    fecha_colombia = parse_db_datetime(item['fecha_hora'])
                    item['fecha_hora'] = fecha_colombia.strftime('%Y-%m-%d %H:%M:%S')
                except Exception as e:
                    app.logger.warning(f"Error formateando fecha: {e}")
                    item['fecha_hora'] = str(item['fecha_hora'])
           
            item['es_venta'] = item['turnPackageId'] is not None and item['turnPackageId'] != 1
        
        app.logger.info(f"Historial obtenido para {qr_code}: {len(historial)} registros")
        
        if not historial:
            return api_response('I001', status='info', data={
                'message': 'No hay historial para este QR',
                'qr_code': qr_code
            })
        
        return jsonify(historial)
        
    except Exception as e:
        app.logger.error(f"Error obteniendo historial del QR: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# ==================== APIS PARA VENTAS ====================

@app.route('/api/registrar-venta', methods=['POST'])
@handle_api_errors
@require_login(['admin', 'cajero'])
@validate_required_fields(['qr_code', 'paquete_id'])
def registrar_venta():
    """Registrar una venta REAL"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        qr_code = data['qr_code']
        paquete_id = data['paquete_id']
        precio = data.get('precio')
        
        user_id = session.get('user_id')
        user_name = session.get('user_name', 'Usuario')
        local = session.get('user_local', 'El Mekatiadero')
        
        app.logger.info(f"REGISTRANDO VENTA REAL: QR={qr_code}, Paquete={paquete_id}")
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        hora_colombia = get_colombia_time()
        
        cursor.execute("""
            INSERT INTO qrhistory (qr_code, user_id, user_name, local, fecha_hora, qr_name, es_venta_real)
            VALUES (%s, %s, %s, %s, %s, 
                    (SELECT qr_name FROM qrcode WHERE code = %s LIMIT 1),
                    TRUE)
        """, (qr_code, user_id, user_name, local, format_datetime_for_db(hora_colombia), qr_code))
        
        connection.commit()
        
        return api_response(
            'S007',
            status='success',
            data={
                'timestamp': hora_colombia.strftime('%Y-%m-%d %H:%M:%S')
            }
        )
        
    except Exception as e:
        app.logger.error(f"Error registrando venta: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/ventas-dia', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero'])
def ventas_dia():
    """Obtener VENTAS REALES del dÃ­a (solo donde es_venta_real = TRUE)"""
    connection = None
    cursor = None
    try:
        fecha = request.args.get('fecha', get_colombia_time().strftime('%Y-%m-%d'))
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("""
            SELECT 
                COUNT(DISTINCT qh.qr_code) as total_ventas,
                COALESCE(SUM(tp.price), 0) as valor_total
            FROM qrhistory qh
            JOIN qrcode qr ON qr.code = qh.qr_code
            JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) = %s
            AND qr.turnPackageId IS NOT NULL
            AND qr.turnPackageId != 1
            AND qh.es_venta_real = TRUE  -- SOLO VENTAS REALES
        """, (fecha,))
        
        resultado = cursor.fetchone()
        
        app.logger.info(f"Ventas REALES del dÃ­a {fecha}: {resultado['total_ventas']} ventas")
        
        return jsonify({
            'total_ventas': resultado['total_ventas'] or 0,
            'valor_total': float(resultado['valor_total'] or 0),
            'fecha': fecha
        })
    except Exception as e:
        app.logger.error(f"Error obteniendo ventas del dÃ­a: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/ventas', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
def obtener_ventas():
    """Obtener ventas con datos completos para el panel de ventas"""
    connection = None
    cursor = None
    try:
        fecha_inicio = request.args.get('fecha_inicio', get_colombia_time().strftime('%Y-%m-%d'))
        fecha_fin = request.args.get('fecha_fin', get_colombia_time().strftime('%Y-%m-%d'))
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("""
            SELECT 
                DATE(qh.fecha_hora) as fecha,
                TIME(qh.fecha_hora) as hora,
                qh.qr_code,
                qh.qr_name,
                tp.name as paquete,
                tp.price as precio,
                tp.turns as turnos,
                qh.user_name as vendedor,
                'Completada' as estado
            FROM qrhistory qh
            JOIN qrcode qr ON qr.code = qh.qr_code
            JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
            AND qr.turnPackageId IS NOT NULL
            AND qr.turnPackageId != 1
            AND qh.es_venta_real = TRUE
            ORDER BY qh.fecha_hora DESC
        """, (fecha_inicio, fecha_fin))
        
        ventas = cursor.fetchall()
        
        cursor.execute("""
            SELECT 
                COUNT(DISTINCT qh.qr_code) as total_paquetes,
                COALESCE(SUM(tp.price), 0) as total_ventas,
                CASE 
                    WHEN COUNT(DISTINCT qh.qr_code) > 0 THEN 
                        COALESCE(SUM(tp.price), 0) / COUNT(DISTINCT qh.qr_code)
                    ELSE 0 
                END as ticket_promedio
            FROM qrhistory qh
            JOIN qrcode qr ON qr.code = qh.qr_code
            JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
            AND qr.turnPackageId IS NOT NULL
            AND qr.turnPackageId != 1
            AND qh.es_venta_real = TRUE
        """, (fecha_inicio, fecha_fin))
        
        estadisticas_data = cursor.fetchone()
        
        cursor.execute("""
            SELECT 
                tp.name as paquete,
                COUNT(DISTINCT qh.qr_code) as cantidad
            FROM qrhistory qh
            JOIN qrcode qr ON qr.code = qh.qr_code
            JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
            AND qr.turnPackageId IS NOT NULL
            AND qr.turnPackageId != 1
            AND qh.es_venta_real = TRUE
            GROUP BY tp.id, tp.name
            ORDER BY cantidad DESC
        """, (fecha_inicio, fecha_fin))
        
        ventas_por_paquete = cursor.fetchall()
        
        # Si es el mismo dÃ­a: agrupar por hora. Si es rango: agrupar por dÃ­a
        es_mismo_dia = fecha_inicio == fecha_fin

        if es_mismo_dia:
            cursor.execute("""
                SELECT 
                    HOUR(qh.fecha_hora) as periodo,
                    COUNT(DISTINCT qh.qr_code) as cantidad
                FROM qrhistory qh
                JOIN qrcode qr ON qr.code = qh.qr_code
                WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
                AND qr.turnPackageId IS NOT NULL
                AND qr.turnPackageId != 1
                AND qh.es_venta_real = TRUE
                GROUP BY HOUR(qh.fecha_hora)
                ORDER BY periodo
            """, (fecha_inicio, fecha_fin))
            ventas_evolucion = cursor.fetchall()
            tipo_evolucion = 'horas'
            labels_evolucion = [f"{item['periodo']}:00" for item in ventas_evolucion]
        else:
            cursor.execute("""
                SELECT 
                    DATE(qh.fecha_hora) as periodo,
                    COUNT(DISTINCT qh.qr_code) as cantidad
                FROM qrhistory qh
                JOIN qrcode qr ON qr.code = qh.qr_code
                WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
                AND qr.turnPackageId IS NOT NULL
                AND qr.turnPackageId != 1
                AND qh.es_venta_real = TRUE
                GROUP BY DATE(qh.fecha_hora)
                ORDER BY periodo
            """, (fecha_inicio, fecha_fin))
            ventas_evolucion = cursor.fetchall()
            tipo_evolucion = 'dias'
            labels_evolucion = [str(item['periodo']) for item in ventas_evolucion]
        
        fecha_inicio_ayer = (datetime.strptime(fecha_inicio, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
        fecha_fin_ayer = (datetime.strptime(fecha_fin, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
        
        cursor.execute("""
            SELECT 
                COUNT(DISTINCT qh.qr_code) as paquetes_ayer,
                COALESCE(SUM(tp.price), 0) as ventas_ayer
            FROM qrhistory qh
            JOIN qrcode qr ON qr.code = qh.qr_code
            JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
            AND qr.turnPackageId IS NOT NULL
            AND qr.turnPackageId != 1
            AND qh.es_venta_real = TRUE
        """, (fecha_inicio_ayer, fecha_fin_ayer))
        
        ayer_data = cursor.fetchone()
        
        total_ventas_hoy = float(estadisticas_data['total_ventas'] or 0)
        total_ventas_ayer = float(ayer_data['ventas_ayer'] or 0)
        
        total_paquetes_hoy = estadisticas_data['total_paquetes'] or 0
        total_paquetes_ayer = ayer_data['paquetes_ayer'] or 0
        
        tendencia_ventas = 0
        if total_ventas_ayer > 0:
            tendencia_ventas = ((total_ventas_hoy - total_ventas_ayer) / total_ventas_ayer) * 100
        
        tendencia_paquetes = 0
        if total_paquetes_ayer > 0:
            tendencia_paquetes = ((total_paquetes_hoy - total_paquetes_ayer) / total_paquetes_ayer) * 100
        
        eficiencia = 85  
        
        graficos = {
    'paquetes': {
        'labels': [item['paquete'] for item in ventas_por_paquete],
        'data': [item['cantidad'] for item in ventas_por_paquete]
    },
    'evolucion': {
        'labels': labels_evolucion,
        'data': [item['cantidad'] for item in ventas_evolucion],
        'tipo': tipo_evolucion
    }
}
        
        ventas_formateadas = []
        for venta in ventas:
            ventas_formateadas.append({
                'fecha': str(venta['fecha']),
                'hora': str(venta['hora'])[:5] if venta['hora'] else '00:00',
                'paquete': venta['paquete'],
                'qr_nombre': venta['qr_name'] or 'Sin nombre',
                'precio': float(venta['precio']),
                'turnos': venta['turnos'],
                'vendedor': venta['vendedor'],
                'estado': venta['estado']
            })
        
        app.logger.info(f"Ventas obtenidas: {len(ventas_formateadas)} registros")
        
        return jsonify({
            'ventas': ventas_formateadas,
            'estadisticas': {
                'total_ventas': total_ventas_hoy,
                'total_paquetes': total_paquetes_hoy,
                'ticket_promedio': float(estadisticas_data['ticket_promedio'] or 0),
                'tendencia_ventas': round(tendencia_ventas, 1),
                'tendencia_paquetes': round(tendencia_paquetes, 1),
                'eficiencia': eficiencia
            },
            'graficos': graficos,
            'rango_fechas': {
                'inicio': fecha_inicio,
                'fin': fecha_fin
            },
            'timestamp': get_colombia_time().isoformat()
        })
        
    except Exception as e:
        app.logger.error(f"Error obteniendo ventas: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/exportar-ventas-pdf', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
def exportar_ventas_pdf():
    """Exportar ventas como PDF"""
    try:
        
        fecha_inicio = request.args.get('fecha_inicio', get_colombia_time().strftime('%Y-%m-%d'))
        fecha_fin = request.args.get('fecha_fin', get_colombia_time().strftime('%Y-%m-%d'))
        
        app.logger.info(f"Exportando ventas a PDF: {fecha_inicio} - {fecha_fin}")
        
        return jsonify({
            'status': 'success',
            'message': 'FunciÃ³n de exportaciÃ³n PDF en desarrollo',
            'rango_fechas': f'{fecha_inicio} a {fecha_fin}',
            'sugerencia': 'Implementar con reportlab o weasyprint'
        })
        
    except Exception as e:
        app.logger.error(f"Error exportando PDF: {e}")
        return api_response('E001', http_status=500)

# ==================== APIS PARA REPORTES DE FALLAS ====================

@app.route('/api/reportar-falla-maquina', methods=['POST'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
@validate_required_fields(['machine_id', 'description'])
def reportar_falla_maquina():
    """Reportar falla en una mÃ¡quina"""
    connection = None
    cursor = None

    try:
        data = request.get_json()
        machine_id = data['machine_id']
        description = data['description'].strip()
        problem_type = data.get('problem_type', 'mantenimiento')
        station_index = data.get('station_index', None)
        user_id = session.get('user_id', 1)

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)

        cursor.execute("SELECT id, name FROM machine WHERE id = %s", (machine_id,))
        maquina = cursor.fetchone()

        if not maquina:
            return api_response('M001', http_status=404, data={'machine_id': machine_id})

        # Insertar reporte con station_index
        cursor.execute("""
            INSERT INTO errorreport
            (machineId, userId, description, problem_type, reportedAt, isResolved, station_index)
            VALUES (%s, %s, %s, %s, NOW(), FALSE, %s)
        """, (machine_id, user_id, description, problem_type, station_index))

        error_report_id = cursor.lastrowid

        # Determinar el nuevo estado global de la mÃ¡quina
        # Para mÃ¡quinas multi-estaciÃ³n: solo ir a 'mantenimiento' si TODAS las estaciones
        # tienen al menos un errorreport activo (no resuelto)
        cursor.execute("""
            SELECT mt.machine_subtype, JSON_LENGTH(mt.station_names) as n_stations
            FROM machinetechnical mt
            WHERE mt.machine_id = %s
        """, (machine_id,))
        maq_info = cursor.fetchone() or {}
        machine_subtype = maq_info.get('machine_subtype', 'simple') or 'simple'
        n_stations      = maq_info.get('n_stations') or 1

        if machine_subtype == 'multi_station' and n_stations > 1:
            # Contar cuÃ¡ntas estaciones distintas tienen fallas activas
            cursor.execute("""
                SELECT COUNT(DISTINCT station_index) as estaciones_con_falla
                FROM errorreport
                WHERE machineId = %s AND isResolved = 0 AND station_index IS NOT NULL
            """, (machine_id,))
            row = cursor.fetchone() or {}
            estaciones_con_falla = row.get('estaciones_con_falla', 0)
            nuevo_estado = 'mantenimiento' if estaciones_con_falla >= n_stations else 'activa'
        else:
            nuevo_estado = 'mantenimiento' if problem_type == 'mantenimiento' else 'inactiva'

        # Actualizar stations_in_maintenance
        try:
            cursor.execute("SELECT stations_in_maintenance FROM machine WHERE id = %s", (machine_id,))
            maq_row = cursor.fetchone() or {}
            en_mant = _parse_json_col(maq_row.get('stations_in_maintenance'), [])
            if station_index is not None and station_index not in en_mant:
                en_mant.append(station_index)
            cursor.execute(
                "UPDATE machine SET stations_in_maintenance = %s WHERE id = %s",
                (json.dumps(en_mant), machine_id)
            )
        except Exception as e:
            app.logger.warning(f"No se pudo actualizar stations_in_maintenance: {e}")

        cursor.execute("""
            UPDATE machine
            SET status = %s,
                errorNote = %s,
                dailyFailedTurns = COALESCE(dailyFailedTurns, 0) + 1
            WHERE id = %s
        """, (nuevo_estado, description, machine_id))

        # Contar fallas activas para esta mÃ¡quina/estaciÃ³n
        if station_index is not None:
            cursor.execute("""
                SELECT COUNT(*) as cnt FROM errorreport
                WHERE machineId = %s AND station_index = %s AND isResolved = 0
            """, (machine_id, station_index))
        else:
            cursor.execute("""
                SELECT COUNT(*) as cnt FROM errorreport
                WHERE machineId = %s AND isResolved = 0
            """, (machine_id,))
        fallas_activas = (cursor.fetchone() or {}).get('cnt', 0)

        # Cualquier falla desde la web â†’ encolar MAINTENANCE al ESP32 para mostrar pantalla de mantenimiento
        try:
            cursor.execute("""
                INSERT INTO esp32_commands
                (machine_id, command, parameters, triggered_by, status, triggered_at)
                VALUES (%s, 'MAINTENANCE', %s, 'sistema_auto', 'queued', NOW())
            """, (machine_id, json.dumps({
                'machine_name': maquina['name'],
                'station_index': station_index,
                'failure_count': fallas_activas,
                'reason': 'Falla reportada desde web â€” activar pantalla mantenimiento'
            })))
            app.logger.warning(
                f"âš  MAINTENANCE encolado â€” {maquina['name']} "
                f"(station={station_index}) fallas_activas={fallas_activas}"
            )
        except Exception as cmd_err:
            app.logger.error(f"No se pudo encolar MAINTENANCE: {cmd_err}")

        connection.commit()

        app.logger.info(
            f"Falla reportada â€” {maquina['name']} reporte#{error_report_id} "
            f"estaciÃ³n={station_index} fallas_activas={fallas_activas}"
        )

        return api_response(
            'S002',
            status='success',
            data={
                'machine_id': machine_id,
                'machine_name': maquina['name'],
                'new_status': nuevo_estado,
                'error_report_id': error_report_id,
                'fallas_activas': fallas_activas,
                'maintenance_triggered': fallas_activas >= 3
            }
        )

    except Exception as e:
        app.logger.error(f"Error reportando falla: {e}")
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/reportes/<int:reporte_id>/resolver', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
def resolver_reporte(reporte_id):
    """Marcar un reporte como resuelto"""
    connection = None
    cursor = None
    try:
        app.logger.info(f"=== INICIANDO RESOLUCIÃ“N DE REPORTE {reporte_id} ===")
        
        data = request.get_json()
        comentarios = data.get('comentarios', '')
        user_id = session.get('user_id')
        user_name = session.get('user_name')
        user_role = session.get('user_role')
        
        app.logger.info(f"DEPURACIÃ“N - user_id: {user_id}, user_name: {user_name}, user_role: {user_role}")
        app.logger.info(f"Datos recibidos: {data}")
        app.logger.info(f"Comentarios: '{comentarios}'")
        
        if not user_id:
            app.logger.error("Usuario no autenticado - SesiÃ³n invÃ¡lida")
            return api_response('E003', http_status=401, data={'message': 'Usuario no autenticado'})
        
        if user_role != 'admin':
            app.logger.error(f"Usuario {user_name} no es admin, es {user_role}")
            return api_response('E004', http_status=403, data={'message': 'Solo administradores pueden resolver reportes'})
        
        connection = get_db_connection()
        if not connection:
            app.logger.error("No se pudo conectar a la BD")
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("SELECT 1 as test")
        test_result = cursor.fetchone()
        app.logger.info(f"ConexiÃ³n BD test: {test_result}")
     
        cursor.execute("SELECT id FROM errorreport WHERE id = %s", (reporte_id,))
        reporte_existe = cursor.fetchone()
        
        if not reporte_existe:
            app.logger.error(f"Reporte {reporte_id} no encontrado")
            return api_response('M007', http_status=404, data={'message': 'Reporte no encontrado'})
        
        cursor.execute("""
            SELECT er.*, m.name as machine_name, m.id as machine_id
            FROM errorreport er
            LEFT JOIN machine m ON er.machineId = m.id
            WHERE er.id = %s
        """, (reporte_id,))
        
        reporte = cursor.fetchone()
        app.logger.info(f"Reporte encontrado: {reporte}")
        
        if not reporte:
            app.logger.error(f"Error al obtener datos del reporte {reporte_id}")
            return api_response('M007', http_status=404, data={'message': 'Reporte no encontrado'})
        
        machine_id = reporte['machineId']
        machine_name = reporte['machine_name']
        
        app.logger.info(f"MÃ¡quina asociada: id={machine_id}, nombre={machine_name}")
        
        try:
            
            app.logger.info("Actualizando ErrorReport...")
            
            query_update_er = """
                UPDATE errorreport 
                SET isResolved = TRUE, resolved_at = NOW()
                WHERE id = %s
            """
            
            cursor.execute(query_update_er, (reporte_id,))
            app.logger.info(f"ErrorReport actualizado: {cursor.rowcount} filas afectadas")
            
            
            app.logger.info("Insertando en confirmation_logs...")
            
            try:
                insert_query = """
                    INSERT INTO confirmation_logs  
                    VALUES (%s, %s, %s, %s)
                """
                app.logger.info(f"Query: {insert_query}")
                app.logger.info(f"Valores: {reporte_id}, {user_id}, 'resuelta', '{comentarios}'")
                
                cursor.execute(insert_query, (reporte_id, user_id, 'resuelta', comentarios))
                confirmation_id = cursor.lastrowid
                app.logger.info(f"Registro creado en confirmation_logs con ID: {confirmation_id}")
            except Exception as insert_error:
                app.logger.error(f"Error insertando en confirmation_logs: {insert_error}")
                
                cursor.execute("""
                    INSERT INTO confirmation_logs 
                    (fault_report_id, admin_id, confirmation_status)
                    VALUES (%s, %s, %s)
                """, (reporte_id, user_id, 'resuelta'))
                confirmation_id = cursor.lastrowid
                app.logger.info(f"Registro creado (sin comments) con ID: {confirmation_id}")
            
            if machine_id:
                app.logger.info(f"Actualizando estado de mÃ¡quina {machine_id}...")
                
               
                cursor.execute("""
                    SELECT COUNT(*) as reportes_pendientes
                    FROM errorreport 
                    WHERE machineId = %s AND isResolved = FALSE
                """, (machine_id,))
                
                otros_reportes = cursor.fetchone()
                reportes_pendientes = otros_reportes['reportes_pendientes'] if otros_reportes else 0
                
                app.logger.info(f"MÃ¡quina {machine_id} tiene {reportes_pendientes} reportes pendientes adicionales")
                
                if reportes_pendientes == 0:
                   
                    cursor.execute("""
                        UPDATE machine 
                        SET status = 'activa', 
                            errorNote = NULL  -- IMPORTANTE: Limpiar el mensaje de error
                        WHERE id = %s AND status IN ('mantenimiento', 'inactiva')
                    """, (machine_id,))
                    
                    if cursor.rowcount > 0:
                        app.logger.info(f"MÃ¡quina {machine_id} cambiada a estado 'activa' y errorNote limpiado")
                    else:
                        app.logger.info(f"MÃ¡quina {machine_id} no cambiÃ³ de estado (ya estaba activa o no aplica)")
                else:
                    
                    cursor.execute("""
                        UPDATE machine 
                        SET status = 'activa'
                        WHERE id = %s AND status IN ('mantenimiento', 'inactiva')
                    """, (machine_id,))
                    
                    if cursor.rowcount > 0:
                        app.logger.info(f"MÃ¡quina {machine_id} cambiada a estado 'activa' (aÃºn tiene {reportes_pendientes} reportes pendientes)")
                    else:
                        app.logger.info(f"MÃ¡quina {machine_id} no cambiÃ³ de estado")
            
            connection.commit()
            app.logger.info(f"=== REPORTE {reporte_id} RESUELTO EXITOSAMENTE ===")
            
            return api_response(
                'S009',
                status='success',
                data={
                    'machine_id': machine_id,
                    'reporte_id': reporte_id,
                    'machine_name': machine_name,
                    'confirmation_id': confirmation_id,
                    'resolved_by': user_name,
                    'errorNote_cleared': True if machine_id and reportes_pendientes == 0 else False
                }
            )
            
        except Exception as trans_error:
            app.logger.error(f"Error en transacciÃ³n: {trans_error}", exc_info=True)
            connection.rollback()
            
            error_msg = str(trans_error)
            
            if "confirmation_logs" in error_msg:
                
                app.logger.info("Verificando estructura de confirmation_logs...")
                try:
                    cursor.execute("DESCRIBE confirmation_logs")
                    estructura = cursor.fetchall()
                    app.logger.info(f"Estructura: {estructura}")
                except Exception as e:
                    app.logger.error(f"Error verificando estructura: {e}")
            
            raise Exception(f"Error en transacciÃ³n: {error_msg}")
            
    except Exception as e:
        app.logger.error(f"Error resolviendo reporte: {e}", exc_info=True)
        return api_response('E001', http_status=500, data={'error_detail': str(e)})
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/debug/tabla-confirmation-logs', methods=['GET'])
def debug_confirmation_logs():
    """Debug: Ver estructura exacta de confirmation_logs"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = get_db_cursor(connection)
        
        cursor.execute("DESCRIBE confirmation_logs")
        estructura = cursor.fetchall()
        
        cursor.execute("SHOW COLUMNS FROM confirmation_logs LIKE 'confirmation_status'")
        enum_info = cursor.fetchone()
        
        test_data = {
            'fault_report_id': 5,
            'admin_id': session.get('user_id', 1),
            'confirmation_status': 'resuelta',
            'comments': 'test desde API'
        }
        
        try:
            cursor.execute("""
                INSERT INTO confirmation_logs 
                (fault_report_id, admin_id, confirmation_status, comments)
                VALUES (%s, %s, %s, %s)
            """, (test_data['fault_report_id'], test_data['admin_id'], 
                  test_data['confirmation_status'], test_data['comments']))
            
            test_id = cursor.lastrowid
            connection.commit()
            
            cursor.execute("SELECT * FROM confirmation_logs WHERE id = %s", (test_id,))
            registro_insertado = cursor.fetchone()
            
            return jsonify({
                'estructura': estructura,
                'enum_info': enum_info,
                'test_insert': {
                    'success': True,
                    'id': test_id,
                    'registro': registro_insertado
                }
            })
            
        except Exception as insert_error:
            connection.rollback()
            return jsonify({
                'estructura': estructura,
                'enum_info': enum_info,
                'test_insert': {
                    'success': False,
                    'error': str(insert_error),
                    'error_type': type(insert_error).__name__
                }
            })
        
    except Exception as e:
        return jsonify({'error': str(e)})
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/debug/reporte-5', methods=['GET'])
def debug_reporte_5():
    """Debug: Verificar reporte con ID 5"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = get_db_cursor(connection)
        
        cursor.execute("""
            SELECT 
                er.*, 
                m.name as machine_name,
                u.name as user_name
            FROM errorreport er
            LEFT JOIN machine m ON er.machineId = m.id
            LEFT JOIN users u ON er.userId = u.id
            WHERE er.id = 5
        """)
        
        reporte = cursor.fetchone()
        
        return jsonify({
            'reporte_5': reporte,
            'exists': reporte is not None
        })
        
    except Exception as e:
        return jsonify({'error': str(e)})
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/debug/errorreport-estructura', methods=['GET'])
def debug_errorreport_estructura():
    """Verificar estructura de ErrorReport"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = get_db_cursor(connection)
        
        cursor.execute("DESCRIBE errorreport")
        estructura = cursor.fetchall()
        
        cursor.execute("DESCRIBE confirmation_logs")
        estructura_logs = cursor.fetchall()
        
        return jsonify({
            'ErrorReport': estructura,
            'confirmation_logs': estructura_logs
        })
    except Exception as e:
        return jsonify({'error': str(e)})
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# admin routes â†’ blueprints/admin/routes.py

# users routes â†’ blueprints/users/routes.py

# ==================== APIS PARA GESTIÃ“N DE PAQUETES ====================

@app.route('/api/paquetes/<int:paquete_id>', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_paquete(paquete_id):
    """Obtener un paquete especÃ­fico"""
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
        app.logger.error(f"Error obteniendo paquete: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/paquetes', methods=['POST'])
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
        
        cursor.execute("SELECT id FROM turnpackage WHERE name = %s", (name,))
        if cursor.fetchone():
            return api_response('E007', http_status=400, data={'message': 'Paquete ya existe'})
        
        cursor.execute("""
            INSERT INTO turnpackage (name, turns, price, isActive)
            VALUES (%s, %s, %s, %s)
        """, (name, turns, price, isActive))
        
        connection.commit()
        
        app.logger.info(f"Paquete creado: {name} (Turnos: {turns}, Precio: {price})")
        
        return api_response(
            'S002',
            status='success',
            data={'paquete_id': cursor.lastrowid}
        )
        
    except Exception as e:
        app.logger.error(f"Error creando paquete: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/paquetes/<int:paquete_id>', methods=['PUT'])
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
        
        app.logger.info(f"Paquete actualizado: {name} (ID: {paquete_id})")
        
        return api_response('S003', status='success')
        
    except Exception as e:
        app.logger.error(f"Error actualizando paquete: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/paquetes/<int:paquete_id>', methods=['DELETE'])
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
            SELECT COUNT(*) as uso_count 
            FROM qrcode 
            WHERE turnPackageId = %s
        """, (paquete_id,))
        uso_count = cursor.fetchone()['uso_count']
        
        if uso_count > 0:
            return api_response(
                'W004',
                status='warning',
                http_status=400,
                data={
                    'message': f'Paquete en uso por {uso_count} cÃ³digos QR',
                    'uso_count': uso_count
                }
            )
        
        cursor.execute("DELETE FROM turnpackage WHERE id = %s", (paquete_id,))
        connection.commit()
        
        app.logger.info(f"Paquete eliminado: {paquete['name']} (ID: {paquete_id})")
        
        return api_response('S004', status='success')
        
    except Exception as e:
        app.logger.error(f"Error eliminando paquete: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# ==================== APIS PARA GESTIÃ“N DE LOCALES ====================

@app.route('/api/locales', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_locales():
    """Obtener todos los locales con estadÃ­sticas - VERSIÃ“N CORREGIDA"""
    connection = None
    cursor = None
    try:
        app.logger.info("=== OBTENIENDO LOCALES ===")
        
        connection = get_db_connection()
        if not connection:
            app.logger.error("No se pudo conectar a la BD")
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("""
            SELECT 
                l.id,
                l.name,
                l.address,
                l.city,
                l.status,
                l.telefono,
                l.horario,
                l.notas
            FROM location l
            ORDER BY l.name
        """)
        
        locales = cursor.fetchall()
        app.logger.info(f"Locales encontrados: {len(locales)}")
        
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
                'maquinas_activas': stats['maquinas_activas'] if stats else 0
            })
        
        app.logger.info("Locales procesados exitosamente")
        return jsonify(locales_con_estadisticas)
        
    except Exception as e:
        app.logger.error(f"Error obteniendo locales: {e}", exc_info=True)
        import traceback
        app.logger.error(f"Traceback completo: {traceback.format_exc()}")
        return api_response('E001', http_status=500, data={'error_detail': str(e)})
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/locales/<int:local_id>', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_local(local_id):
    """Obtener un local especÃ­fico"""
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
        
        local_completo = {
            'id': local['id'],
            'name': local['name'],
            'address': local.get('address', ''),
            'city': local.get('city', ''),
            'status': local.get('status', 'activo'),
            'telefono': local.get('telefono', ''),
            'horario': local.get('horario', ''),
            'notas': local.get('notas', '')
        }
        
        return jsonify(local_completo)
        
    except Exception as e:
        app.logger.error(f"Error obteniendo local: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/locales', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
@validate_required_fields(['name', 'address', 'city'])
def crear_local():
    """Crear un nuevo local"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        name = data['name']
        address = data['address']
        city = data['city']
        status = data.get('status', 'activo')
        telefono = data.get('telefono', '')
        horario = data.get('horario', '')
        notas = data.get('notas', '')
        
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
        
        app.logger.info(f"Local creado: {name} en {city}")
        
        return api_response(
            'S002',
            status='success',
            data={'local_id': cursor.lastrowid}
        )
        
    except Exception as e:
        app.logger.error(f"Error creando local: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/locales/<int:local_id>', methods=['PUT'])
@handle_api_errors
@require_login(['admin'])
@validate_required_fields(['name', 'address', 'city'])
def actualizar_local(local_id):
    """Actualizar un local existente"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        name = data['name']
        address = data['address']
        city = data['city']
        status = data.get('status')
        telefono = data.get('telefono', '')
        horario = data.get('horario', '')
        notas = data.get('notas', '')
        
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
        
        app.logger.info(f"Local actualizado: {name} (ID: {local_id})")
        
        return api_response('S003', status='success')
        
    except Exception as e:
        app.logger.error(f"Error actualizando local: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/locales/<int:local_id>', methods=['DELETE'])
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
  
        cursor.execute("SELECT COUNT(*) as maquinas_count FROM machine WHERE location_id = %s", (local_id,))
        maquinas_count = cursor.fetchone()['maquinas_count']
        
        if maquinas_count > 0:
            return api_response(
                'W005',
                status='warning',
                http_status=400,
                data={
                    'message': f'Local tiene {maquinas_count} mÃ¡quinas asignadas',
                    'maquinas_count': maquinas_count
                }
            )

        cursor.execute("DELETE FROM location WHERE id = %s", (local_id,))
        connection.commit()
        
        app.logger.info(f"Local eliminado: {local['name']} (ID: {local_id})")
        
        return api_response('S004', status='success')
        
    except Exception as e:
        app.logger.error(f"Error eliminando local: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# ==================== APIS PARA GESTIÃ“N DE MÃQUINAS ====================

@app.route('/api/maquinas', methods=['GET'])
@handle_api_errors
def obtener_maquinas():
    """Obtener todas las mÃ¡quinas con informaciÃ³n completa"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        # Intentar query completo (requiere migraciÃ³n V32).
        # Si las columnas aÃºn no existen, caer al query bÃ¡sico.
        try:
            cursor.execute("""
                SELECT
                    m.id,
                    m.name,
                    m.type,
                    m.status,
                    m.location_id,
                    m.dailyFailedTurns,
                    m.dateLastQRUsed,
                    m.errorNote,
                    m.stations_in_maintenance,
                    m.consecutive_failures,
                    l.name as location_name,
                    COALESCE(mpr.porcentaje_restaurante, 35.00) as porcentaje_restaurante,
                    mt.machine_subtype,
                    mt.station_names
                FROM machine m
                LEFT JOIN location l ON m.location_id = l.id
                LEFT JOIN maquinaporcentajerestaurante mpr ON m.id = mpr.maquina_id
                LEFT JOIN machinetechnical mt ON m.id = mt.machine_id
                ORDER BY m.name
            """)
        except Exception:
            # MigraciÃ³n V32 pendiente: columnas de fallas por estaciÃ³n aÃºn no existen
            cursor.execute("""
                SELECT
                    m.id,
                    m.name,
                    m.type,
                    m.status,
                    m.location_id,
                    m.dailyFailedTurns,
                    m.dateLastQRUsed,
                    m.errorNote,
                    NULL AS stations_in_maintenance,
                    NULL AS consecutive_failures,
                    l.name as location_name,
                    COALESCE(mpr.porcentaje_restaurante, 35.00) as porcentaje_restaurante,
                    mt.machine_subtype,
                    mt.station_names
                FROM machine m
                LEFT JOIN location l ON m.location_id = l.id
                LEFT JOIN maquinaporcentajerestaurante mpr ON m.id = mpr.maquina_id
                LEFT JOIN machinetechnical mt ON m.id = mt.machine_id
                ORDER BY m.name
            """)
        
        maquinas = cursor.fetchall()
        
        maquinas_formateadas = []
        for maquina in maquinas:
            cursor.execute("""
                SELECT 
                    p.id,
                    p.nombre,
                    mp.porcentaje_propiedad
                FROM maquinapropietario mp
                JOIN propietarios p ON mp.propietario_id = p.id
                WHERE mp.maquina_id = %s
            """, (maquina['id'],))
            
            propietarios = cursor.fetchall()
            
            info_propietarios = ", ".join([
                f"{p['nombre']} ({p['porcentaje_propiedad']}%)" for p in propietarios
            ]) if propietarios else "Sin propietarios"
            
            maquinas_formateadas.append({
                'id': maquina['id'],
                'name': maquina['name'],
                'type': maquina['type'],
                'status': maquina['status'],
                'location_id': maquina['location_id'],
                'location_name': maquina['location_name'],
                'dailyFailedTurns': maquina['dailyFailedTurns'],
                'dateLastQRUsed': maquina['dateLastQRUsed'].isoformat() if maquina['dateLastQRUsed'] else None,
                'errorNote': maquina['errorNote'],
                'porcentaje_restaurante': float(maquina['porcentaje_restaurante']),
                'propietarios': propietarios,
                'info_propietarios': info_propietarios,
                'machine_subtype': maquina.get('machine_subtype', 'simple') or 'simple',
                'station_names': _parse_json_col(maquina.get('station_names'), []),
                'stations_in_maintenance': _parse_json_col(maquina.get('stations_in_maintenance'), []),
                'consecutive_failures': _parse_json_col(maquina.get('consecutive_failures'), {}),
                **_esp32_heartbeat_fields(maquina['id']),
            })
        
        return jsonify(maquinas_formateadas)
        
    except Exception as e:
        app.logger.error(f"Error obteniendo mÃ¡quinas: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/turnusage/recientes', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero'])
def obtener_turnusage_recientes():
    """Obtener historial reciente de juegos (turnusage) - CON SOPORTE PARA ESTACIONES"""
    connection = None
    cursor = None
    try:
        limit = request.args.get('limit', '100')
        machine_id = request.args.get('machine_id')
        station = request.args.get('station')
        
        try:
            limit = int(limit)
        except:
            limit = 100
            
        connection = get_db_connection()
        if not connection:
            return jsonify([])
            
        cursor = get_db_cursor(connection)
        if not cursor:
            return jsonify([])
        
        query = """
            SELECT 
                tu.id,
                tu.qrCodeId,
                tu.machineId,
                tu.station_index,
                tu.usedAt,
                COALESCE(m.name, 'MÃ¡quina desconocida') as machine_name,
                COALESCE(qr.code, '') as qr_code,
                COALESCE(qr.qr_name, '') as qr_name,
                COALESCE(tp.name, 'Sin paquete') as package_name,
                tu.turns_remaining_after as turns_remaining
            FROM turnusage tu
            LEFT JOIN machine m ON tu.machineId = m.id
            LEFT JOIN qrcode qr ON tu.qrCodeId = qr.id
            LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE 1=1
        """
        
        params = []
        
        if machine_id:
            query += " AND tu.machineId = %s"
            params.append(machine_id)
        
        if station is not None:
            query += " AND tu.station_index = %s"
            params.append(station)
        
        query += " ORDER BY tu.usedAt DESC LIMIT %s"
        params.append(limit)
        
        cursor.execute(query, params)
        
        juegos = cursor.fetchall()
        resultado = []
        
        for juego in juegos:
            juego_dict = dict(juego)
            if juego_dict.get('usedAt'):
                try:
                    if hasattr(juego_dict['usedAt'], 'isoformat'):
                        juego_dict['usedAt'] = juego_dict['usedAt'].isoformat()
                except:
                    juego_dict['usedAt'] = str(juego_dict['usedAt'])
            resultado.append(juego_dict)
        
        return jsonify(resultado)
        
    except Exception as e:
        app.logger.error(f"Error obteniendo turnusage recientes: {e}")
        return jsonify([])
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/machinefailures/recientes', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero'])
def obtener_machinefailures_recientes():
    """Obtener historial reciente de fallas - CON SOPORTE PARA ESTACIONES"""
    connection = None
    cursor = None
    try:
        limit = request.args.get('limit', '100')
        machine_id = request.args.get('machine_id')
        station = request.args.get('station')
        
        try:
            limit = int(limit)
        except:
            limit = 100
            
        connection = get_db_connection()
        if not connection:
            return jsonify([])
            
        cursor = get_db_cursor(connection)
        if not cursor:
            return jsonify([])
        
        query = """
            SELECT 
                mf.id,
                mf.qr_code_id,
                COALESCE(mf.machine_id, 0) as machine_id,
                mf.station_index,
                COALESCE(mf.machine_name, 'MÃ¡quina desconocida') as machine_name,
                COALESCE(mf.turnos_devueltos, 0) as turnos_devueltos,
                mf.reported_at,
                COALESCE(mf.notes, '') as notes,
                COALESCE(mf.is_forced, 0) as is_forced,
                COALESCE(mf.forced_by, '') as forced_by,
                COALESCE(qr.code, '') as qr_code,
                COALESCE(qr.qr_name, '') as qr_name
            FROM machinefailures mf
            LEFT JOIN qrcode qr ON mf.qr_code_id = qr.id
            WHERE 1=1
        """
        
        params = []
        
        if machine_id:
            query += " AND mf.machine_id = %s"
            params.append(machine_id)
        
        if station is not None:
            query += " AND mf.station_index = %s"
            params.append(station)
        
        query += " ORDER BY mf.reported_at DESC LIMIT %s"
        params.append(limit)
        
        cursor.execute(query, params)
        
        fallas = cursor.fetchall()
        resultado = []
        
        for falla in fallas:
            falla_dict = dict(falla)
            if falla_dict.get('reported_at'):
                try:
                    if hasattr(falla_dict['reported_at'], 'isoformat'):
                        falla_dict['reported_at'] = falla_dict['reported_at'].isoformat()
                except:
                    falla_dict['reported_at'] = str(falla_dict['reported_at'])
            resultado.append(falla_dict)
        
        return jsonify(resultado)
        
    except Exception as e:
        app.logger.error(f"Error obteniendo fallas recientes: {e}")
        return jsonify([])
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@app.route('/api/maquinas/<int:maquina_id>', methods=['GET'])
@handle_api_errors
def obtener_maquina(maquina_id):
    """Obtener una mÃ¡quina especÃ­fica con informaciÃ³n completa"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("""
    SELECT 
        m.*,
        l.name as location_name,
        COALESCE(mpr.porcentaje_restaurante, 35.00) as porcentaje_restaurante,
        mt.machine_subtype,
        mt.station_names,
        mt.has_failure_report,
        mt.show_station_selection
    FROM machine m
    LEFT JOIN location l ON m.location_id = l.id
    LEFT JOIN maquinaporcentajerestaurante mpr ON m.id = mpr.maquina_id
    LEFT JOIN machinetechnical mt ON m.id = mt.machine_id
    WHERE m.id = %s
""", (maquina_id,))
        
        maquina = cursor.fetchone()
        
        if not maquina:
            return api_response('M001', http_status=404, data={'machine_id': maquina_id})

        # Obtener propietarios
        cursor.execute("""
            SELECT 
                p.id,
                p.nombre,
                mp.porcentaje_propiedad
            FROM maquinapropietario mp
            JOIN propietarios p ON mp.propietario_id = p.id
            WHERE mp.maquina_id = %s
        """, (maquina_id,))
        
        propietarios = cursor.fetchall()

        # Fallas activas por estaciÃ³n (desde errorreport â€” reportes cajero/web)
        cursor.execute("""
            SELECT station_index, COUNT(*) as count
            FROM errorreport
            WHERE machineId = %s AND isResolved = 0
            GROUP BY station_index
        """, (maquina_id,))
        failure_rows = cursor.fetchall()
        active_failure_stations = []
        machine_level_failures = 0
        for row in failure_rows:
            if row['station_index'] is None:
                machine_level_failures += row['count']
            else:
                active_failure_stations.append({
                    'station_index': row['station_index'],
                    'count': row['count'],
                    'cajero_count': row['count'],
                    'esp32_count': 0
                })

        # Calcular tiempo desde Ãºltimo uso
        ultimo_uso_texto = "Nunca"
        if maquina['dateLastQRUsed']:
            try:
                fecha_ultimo = parse_db_datetime(maquina['dateLastQRUsed'])
                ahora = get_colombia_time()
                diferencia = ahora - fecha_ultimo
                
                if diferencia.days > 0:
                    ultimo_uso_texto = f"Hace {diferencia.days} dÃ­as"
                elif diferencia.seconds > 3600:
                    horas = diferencia.seconds // 3600
                    ultimo_uso_texto = f"Hace {horas} horas"
                elif diferencia.seconds > 60:
                    minutos = diferencia.seconds // 60
                    ultimo_uso_texto = f"Hace {minutos} minutos"
                else:
                    ultimo_uso_texto = "Hace unos segundos"
            except:
                ultimo_uso_texto = maquina['dateLastQRUsed'].strftime('%Y-%m-%d %H:%M')
        
        return jsonify({
            'id': maquina['id'],
            'name': maquina['name'],
            'type': maquina['type'],
            'status': maquina['status'],
            'location_id': maquina['location_id'],
            'location_name': maquina['location_name'],
            'dailyFailedTurns': maquina['dailyFailedTurns'] or 0,
            'dateLastQRUsed': maquina['dateLastQRUsed'].isoformat() if maquina['dateLastQRUsed'] else None,
            'ultimo_uso_texto': ultimo_uso_texto,
            'errorNote': maquina['errorNote'],
            'porcentaje_restaurante': float(maquina['porcentaje_restaurante']),
            'propietarios': propietarios,
            'info_propietarios': ", ".join([
                f"{p['nombre']} ({p['porcentaje_propiedad']}%)" for p in propietarios
            ]) if propietarios else "Sin propietarios",
            'valor_por_turno': float(maquina['valor_por_turno'] or 3000.00),
            'machine_subtype': maquina.get('machine_subtype', 'simple') or 'simple',
            'station_names': _parse_json_col(maquina.get('station_names'), []),
            'show_station_selection': bool(maquina.get('show_station_selection', False)),
            'active_failure_stations': active_failure_stations,
            'machine_level_failures': machine_level_failures,
        })
        
    except Exception as e:
        app.logger.error(f"Error obteniendo mÃ¡quina: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/maquinas/<int:maquina_id>/ultima-actividad', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
def obtener_ultima_actividad_maquina(maquina_id):
    """Obtener informaciÃ³n sobre la Ãºltima actividad de una mÃ¡quina"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        # Obtener Ãºltimo juego
        cursor.execute("""
            SELECT 
                tu.usedAt,
                qr.code as qr_code,
                qr.qr_name,
                tp.name as package_name
            FROM turnusage tu
            JOIN qrcode qr ON tu.qrCodeId = qr.id
            LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE tu.machineId = %s
            ORDER BY tu.usedAt DESC
            LIMIT 1
        """, (maquina_id,))
        
        ultimo_juego = cursor.fetchone()
        
        # Obtener Ãºltima falla
        cursor.execute("""
            SELECT 
                reported_at,
                notes,
                is_forced,
                turnos_devueltos
            FROM machinefailures
            WHERE machine_id = %s
            ORDER BY reported_at DESC
            LIMIT 1
        """, (maquina_id,))
        
        ultima_falla = cursor.fetchone()
        
        resultado = {
            'ultimo_juego': None,
            'ultima_falla': None
        }
        
        if ultimo_juego:
            resultado['ultimo_juego'] = {
                'fecha': ultimo_juego['usedAt'].isoformat() if ultimo_juego['usedAt'] else None,
                'qr_code': ultimo_juego['qr_code'],
                'qr_name': ultimo_juego['qr_name'],
                'package': ultimo_juego['package_name']
            }
        
        if ultima_falla:
            resultado['ultima_falla'] = {
                'fecha': ultima_falla['reported_at'].isoformat() if ultima_falla['reported_at'] else None,
                'descripcion': ultima_falla['notes'],
                'forzada': bool(ultima_falla['is_forced']),
                'turnos_devueltos': ultima_falla['turnos_devueltos']
            }
        
        return jsonify(resultado)
        
    except Exception as e:
        app.logger.error(f"Error obteniendo Ãºltima actividad: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# ==================== APIS PARA GESTIÃ“N DE IMÃGENES ====================

@app.route('/api/imagenes/maquinas', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def listar_imagenes_maquinas():
    """Listar todas las imÃ¡genes disponibles para mÃ¡quinas"""
    try:
        import os
        static_dir = os.path.join(os.path.dirname(__file__), 'static', 'img')
        imagenes = []
        
        if os.path.exists(static_dir):
            for file in os.listdir(static_dir):
                if file.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
                    imagenes.append(file)
        
        return jsonify(imagenes)
        
    except Exception as e:
        app.logger.error(f"Error listando imÃ¡genes: {e}")
        return api_response('E001', http_status=500)

# ==================== APIS PARA DATOS TÃ‰CNICOS ====================

@app.route('/api/maquinas/<int:maquina_id>/technical', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
def guardar_technical_maquina(maquina_id):
    """Guardar configuraciÃ³n tÃ©cnica de la mÃ¡quina"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        
        connection = get_db_connection()
        cursor = get_db_cursor(connection)
        
        # Verificar si ya existe
        cursor.execute("SELECT id FROM machinetechnical WHERE machine_id = %s", (maquina_id,))
        existe = cursor.fetchone()
        
        if existe:
            # Actualizar
            cursor.execute("""
                UPDATE machinetechnical 
                SET credits_virtual = %s,
                    credits_machine = %s,
                    game_duration_seconds = %s,
                    reset_time_seconds = %s,
                    machine_subtype = %s,
                    station_names = %s,
                    game_type = %s,
                    has_failure_report = %s,
                    show_station_selection = %s,
                    updated_at = NOW()
                WHERE machine_id = %s
            """, (
                data.get('credits_virtual', 1),
                data.get('credits_machine', 1),
                data.get('game_duration_seconds', 180),
                data.get('reset_time_seconds', 5),
                data.get('machine_subtype', 'simple'),
                json.dumps(data.get('stations', [])),
                data.get('game_type', 'time_based'),
                data.get('has_failure_report', True),
                data.get('show_station_selection', False),
                maquina_id
            ))
        else:
            # Insertar
            cursor.execute("""
                INSERT INTO machinetechnical 
                (machine_id, credits_virtual, credits_machine, game_duration_seconds, 
                 reset_time_seconds, machine_subtype, station_names, game_type, 
                 has_failure_report, show_station_selection)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                maquina_id,
                data.get('credits_virtual', 1),
                data.get('credits_machine', 1),
                data.get('game_duration_seconds', 180),
                data.get('reset_time_seconds', 5),
                data.get('machine_subtype', 'simple'),
                json.dumps(data.get('stations', [])),
                data.get('game_type', 'time_based'),
                data.get('has_failure_report', True),
                data.get('show_station_selection', False)
            ))
        
        connection.commit()

        # Si es multi-estaciÃ³n, enviar comando de actualizaciÃ³n de nombres al TFT del ESP32
        machine_subtype = data.get('machine_subtype', 'simple')
        stations = data.get('stations', [])
        if machine_subtype == 'multi_station' and stations:
            try:
                from datetime import datetime as _dt
                station_names_list = [s['name'] if isinstance(s, dict) else str(s) for s in stations]
                cursor.execute("""
                    INSERT INTO esp32_commands (machine_id, command, parameters, triggered_by, status, triggered_at)
                    VALUES (%s, %s, %s, %s, %s, NOW())
                """, (
                    maquina_id,
                    'UPDATE_STATION_NAMES',
                    json.dumps({
                        'station_names': station_names_list,
                        'station_count': len(station_names_list)
                    }),
                    'admin_config',
                    'queued'
                ))
                connection.commit()
                app.logger.info(f"âœ… Comando UPDATE_STATION_NAMES encolado para mÃ¡quina {maquina_id}: {station_names_list}")
            except Exception as cmd_err:
                app.logger.warning(f"No se pudo encolar UPDATE_STATION_NAMES: {cmd_err}")

        return api_response('S003', status='success')

    except Exception as e:
        app.logger.error(f"Error guardando datos tÃ©cnicos: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor: cursor.close()
        if connection: connection.close()

# ==================== APIS PARA PROPIETARIOS ====================

@app.route('/api/maquinas/<int:maquina_id>/propietarios', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
def guardar_propietarios_maquina(maquina_id):
    """Guardar propietarios de la mÃ¡quina"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        
        connection = get_db_connection()
        cursor = get_db_cursor(connection)
        
        # Eliminar propietarios actuales
        cursor.execute("DELETE FROM maquinapropietario WHERE maquina_id = %s", (maquina_id,))
        
        # Insertar nuevos
        for prop in data:
            cursor.execute("""
                INSERT INTO maquinapropietario (maquina_id, propietario_id, porcentaje_propiedad)
                VALUES (%s, %s, %s)
            """, (maquina_id, prop['propietario_id'], prop['porcentaje']))
        
        connection.commit()
        return api_response('S003', status='success')
        
    except Exception as e:
        app.logger.error(f"Error guardando propietarios: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor: cursor.close()
        if connection: connection.close()

@app.route('/api/maquinas', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
@validate_required_fields(['name', 'type', 'location_id'])
def crear_maquina():
    """Crear una nueva mÃ¡quina"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        name = data['name']
        type = data['type']
        status = data.get('status', 'activa')
        location_id = data['location_id']
        errorNote = data.get('errorNote', '')
        porcentaje_restaurante = data.get('porcentaje_restaurante', 35.00)
        
        if type not in ['simulador', 'arcade', 'peluchera']:
            return api_response('E005', http_status=400, data={'message': 'Tipo de mÃ¡quina invÃ¡lido'})
        
        if status not in ['activa', 'mantenimiento', 'inactiva']:
            return api_response('E005', http_status=400, data={'message': 'Estado invÃ¡lido'})
        
        if not (0 <= float(porcentaje_restaurante) <= 100):
            return api_response('E005', http_status=400, data={'message': 'Porcentaje debe estar entre 0 y 100'})
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)

        cursor.execute("SELECT id FROM machine WHERE name = %s", (name,))
        if cursor.fetchone():
            return api_response('E007', http_status=400, data={'message': 'MÃ¡quina ya existe'})

        cursor.execute("SELECT id FROM location WHERE id = %s", (location_id,))
        if not cursor.fetchone():
            return api_response('E002', http_status=404, data={'local_id': location_id})

        cursor.execute("""
            INSERT INTO machine (name, type, status, location_id, errorNote)
            VALUES (%s, %s, %s, %s, %s)
        """, (name, type, status, location_id, errorNote))
        
        maquina_id = cursor.lastrowid

        if float(porcentaje_restaurante) != 35.00:
           cursor.execute("""
             INSERT INTO maquinaporcentajerestaurante (maquina_id, porcentaje_restaurante)
             VALUES (%s, %s)
            """, (maquina_id, porcentaje_restaurante))
        
        connection.commit()
        
        app.logger.info(f"MÃ¡quina creada: {name} (ID: {maquina_id})")
        
        return api_response(
            'S002',
            status='success',
            data={'maquina_id': maquina_id}
        )
        
    except Exception as e:
        app.logger.error(f"Error creando mÃ¡quina: {e}")
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/maquinas-por-tipo', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
def obtener_maquinas_por_tipo():
    """
    Obtener todas las mÃ¡quinas organizadas por tipo
    Para usar en machinereport.html
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        # Obtener todas las mÃ¡quinas activas
        cursor.execute("""
            SELECT 
                m.id,
                m.name,
                m.type,
                m.status,
                m.location_id,
                l.name as location_name,
                COALESCE(m.dailyFailedTurns, 0) as dailyFailedTurns,
                m.dateLastQRUsed,
                COALESCE(m.valor_por_turno, 3000.00) as valor_por_turno
            FROM machine m
            LEFT JOIN location l ON m.location_id = l.id
            WHERE m.status IN ('activa', 'mantenimiento', 'inactiva')
            ORDER BY m.type, m.name
        """)
        
        maquinas = cursor.fetchall()
        
        # Organizar por tipo
        resultado = {
            'arcade': [],
            'simulador': [],
            'peluchera': [],
            'otros': []
        }
        
        for maquina in maquinas:
            maquina_info = {
                'id': maquina['id'],
                'name': maquina['name'],
                'type': maquina['type'],
                'status': maquina['status'],
                'location_id': maquina['location_id'],
                'location_name': maquina['location_name'],
                'dailyFailedTurns': maquina['dailyFailedTurns'],
                'dateLastQRUsed': maquina['dateLastQRUsed'].isoformat() if maquina['dateLastQRUsed'] else None,
                'valor_por_turno': float(maquina['valor_por_turno']),
                'imagen': obtener_nombre_imagen(maquina['name'])  # FunciÃ³n para mapear nombre a imagen
            }
            
            tipo = maquina['type'].lower() if maquina['type'] else 'otros'
            if tipo in resultado:
                resultado[tipo].append(maquina_info)
            else:
                resultado['otros'].append(maquina_info)
        
        return jsonify({
            'status': 'success',
            'data': resultado,
            'totales': {
                'arcade': len(resultado['arcade']),
                'simulador': len(resultado['simulador']),
                'peluchera': len(resultado['peluchera']),
                'otros': len(resultado['otros']),
                'total': len(maquinas)
            }
        })
        
    except Exception as e:
        app.logger.error(f"Error obteniendo mÃ¡quinas por tipo: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

def obtener_nombre_imagen(nombre_maquina):
    """
    FunciÃ³n auxiliar para mapear nombre de mÃ¡quina a nombre de archivo de imagen
    """
    # Diccionario de mapeo nombre -> archivo imagen
    mapa_imagenes = {
        'Simulador connection': 'simulador pk.jpg',
        'Simulador Cruisin 1': 'simulador1.jpg',
        'Simulador Cruisin 2': 'simulador2.jpg',
        'Peluches 1': 'peluches1.jpg',
        'Peluches 2': 'peluches2.jpg',
        'Basketball': 'basketball.jpg',
        'Pelea': 'pelea.jpg',
        'Disco hockey': 'disco hockey.jpg',
        'Sillas masajes': 'sillas de masajes.jpg',
        'Mcqueen': 'mcqueen.jpg',
        'Caballito': 'caballo.jpg',
        'Trencito': 'tren.jpg',
        'Basketball 2': 'basketball 2.jpg',
        'Disco Air Hockey': 'disco air hockey.jpg'
    }
    
    # Buscar coincidencia exacta o parcial
    for key, filename in mapa_imagenes.items():
        if key.lower() in nombre_maquina.lower() or nombre_maquina.lower() in key.lower():
            return filename
    
    # Imagen por defecto segÃºn el tipo (podrÃ­as tener una imagen genÃ©rica)
    return 'default.jpg'

@app.route('/api/maquinas/<int:maquina_id>', methods=['PUT'])
@handle_api_errors
@require_login(['admin'])
@validate_required_fields(['name', 'type', 'status', 'location_id'])
def actualizar_maquina(maquina_id):
    """Actualizar una mÃ¡quina existente"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        name = data['name']
        type = data['type']
        status = data['status']
        location_id = data['location_id']
        errorNote = data.get('errorNote', '')
        porcentaje_restaurante = data.get('porcentaje_restaurante', 35.00)

        if type not in ['simulador', 'arcade', 'peluchera']:
            return api_response('E005', http_status=400, data={'message': 'Tipo de mÃ¡quina invÃ¡lido'})
        
        if status not in ['activa', 'mantenimiento', 'inactiva']:
            return api_response('E005', http_status=400, data={'message': 'Estado invÃ¡lido'})
        
        if not (0 <= float(porcentaje_restaurante) <= 100):
            return api_response('E005', http_status=400, data={'message': 'Porcentaje debe estar entre 0 y 100'})
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)

        # Verificar que la mÃ¡quina existe
        cursor.execute("SELECT name FROM machine WHERE id = %s", (maquina_id,))
        maquina = cursor.fetchone()
        if not maquina:
            return api_response('M001', http_status=404, data={'machine_id': maquina_id})

        # Verificar que el nombre no estÃ© duplicado
        cursor.execute("SELECT id FROM machine WHERE name = %s AND id != %s", (name, maquina_id))
        if cursor.fetchone():
            return api_response('E007', http_status=400, data={'message': 'Nombre de mÃ¡quina ya existe'})

        # Verificar que el local existe
        cursor.execute("SELECT id FROM location WHERE id = %s", (location_id,))
        if not cursor.fetchone():
            return api_response('E002', http_status=404, data={'local_id': location_id})

        # Actualizar la mÃ¡quina
        cursor.execute("""
            UPDATE machine 
            SET name = %s, type = %s, status = %s, location_id = %s, errorNote = %s
            WHERE id = %s
        """, (name, type, status, location_id, errorNote, maquina_id))

        # Manejar el porcentaje del restaurante - CORREGIDO: usar minÃºsculas
        if float(porcentaje_restaurante) != 35.00:
            # Intentar insertar o actualizar
            cursor.execute("""
                INSERT INTO maquinaporcentajerestaurante (maquina_id, porcentaje_restaurante)
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE porcentaje_restaurante = %s
            """, (maquina_id, porcentaje_restaurante, porcentaje_restaurante))
        else:
            # Si es 35%, eliminar el registro si existe (es el valor por defecto)
            cursor.execute("DELETE FROM maquinaporcentajerestaurante WHERE maquina_id = %s", (maquina_id,))
        
        connection.commit()
        
        app.logger.info(f"MÃ¡quina actualizada: {name} (ID: {maquina_id})")
        
        return api_response('S003', status='success')
        
    except Exception as e:
        app.logger.error(f"Error actualizando mÃ¡quina: {e}")
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/maquinas/<int:maquina_id>', methods=['DELETE'])
@handle_api_errors
@require_login(['admin'])
def eliminar_maquina(maquina_id):
    """Eliminar una mÃ¡quina"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)

        cursor.execute("SELECT name FROM machine WHERE id = %s", (maquina_id,))
        maquina = cursor.fetchone()
        if not maquina:
            return api_response('M001', http_status=404, data={'machine_id': maquina_id})

        cursor.execute("SELECT COUNT(*) as uso_count FROM turnusage WHERE machineId = %s", (maquina_id,))
        uso_count = cursor.fetchone()['uso_count']
        
        if uso_count > 0:
            return api_response(
                'W004',
                status='warning',
                http_status=400,
                data={
                    'message': f'MÃ¡quina tiene {uso_count} usos registrados',
                    'uso_count': uso_count,
                    'machine_name': maquina['name']
                }
            )

        # Eliminar todas las tablas relacionadas (FK cleanup)
        tablas_fk = [
            ("machinetechnical",           "machine_id"),
            ("esp32_commands",             "machine_id"),
            ("machine_resets",             "machine_id"),
            ("machinefailures",            "machine_id"),
            ("maquinapropietario",         "maquina_id"),
            ("maquinaporcentajerestaurante","maquina_id"),
            ("errorreport",                "machineId"),
        ]
        for tabla, col in tablas_fk:
            try:
                cursor.execute(f"DELETE FROM {tabla} WHERE {col} = %s", (maquina_id,))
            except Exception as fk_err:
                app.logger.warning(f"FK cleanup {tabla}: {fk_err}")

        cursor.execute("DELETE FROM machine WHERE id = %s", (maquina_id,))
        
        connection.commit()
        
        app.logger.info(f"MÃ¡quina eliminada: {maquina['name']} (ID: {maquina_id})")
        
        return api_response('S004', status='success')
        
    except Exception as e:
        app.logger.error(f"Error eliminando mÃ¡quina: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# ==================== APIS PARA ACCIONES DE MÃQUINAS DESDE ADMIN ====================

# ==================== APIS PARA ACCIONES DE MÃQUINAS DESDE ADMIN ====================

@app.route('/api/maquinas/ingresar-turno', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
@validate_required_fields(['machine_id', 'machine_name'])
def ingresar_turno_manual():
    """
    Endpoint para que el administrador pueda INGRESAR UN TURNO MANUAL
    Ahora tambiÃ©n envÃ­a comando al ESP32 para activar el relÃ©
    """
    connection = None
    cursor = None
    try:
        data = request.get_json()
        machine_id = data['machine_id']
        machine_name = data['machine_name']
        user_id = session.get('user_id')
        user_name = session.get('user_name', 'Administrador')
        
        app.logger.info(f"ðŸ”„ [ADMIN] Ingresando turno manual - MÃ¡quina: {machine_name} (ID: {machine_id})")
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        # Verificar que la mÃ¡quina existe
        cursor.execute("SELECT id, name, status FROM machine WHERE id = %s", (machine_id,))
        maquina = cursor.fetchone()
        
        if not maquina:
            return api_response('M001', http_status=404, data={'machine_id': machine_id})
        
        # Verificar estado de la mÃ¡quina
        if maquina['status'] != 'activa':
            return api_response(
                'M003',
                http_status=400,
                data={
                    'machine_id': machine_id,
                    'current_status': maquina['status'],
                    'message': f'La mÃ¡quina estÃ¡ en estado "{maquina["status"]}". Solo se pueden ingresar turnos en mÃ¡quinas activas.'
                }
            )

        # Obtener estaciÃ³n (para mÃ¡quinas multi-estaciÃ³n)
        station_index = data.get('estacion', 0)
        estacion_nombre = data.get('estacion_nombre', f'EstaciÃ³n {station_index + 1}')

        hora_actual = get_colombia_time()

        # ENVIAR COMANDO AL ESP32 â€” activar relÃ© sin consumir ningÃºn QR
        cursor.execute("""
            INSERT INTO esp32_commands (machine_id, command, parameters, triggered_by, status, triggered_at)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            machine_id,
            'ACTIVATE_RELAY',
            json.dumps({
                'duration_ms': 500,
                'machine_name': machine_name,
                'station': station_index,        # ESP32 lee 'station'
                'station_index': station_index,  # alias compat
                'estacion_nombre': estacion_nombre,
                'origen': 'admin_manual'
            }),
            user_name,
            'queued',
            format_datetime_for_db(hora_actual)
        ))

        command_id = cursor.lastrowid
        app.logger.info(f"âœ… Comando ACTIVATE_RELAY encolado con ID: {command_id} (estaciÃ³n {station_index})")

        connection.commit()

        _log_transaccion(
            tipo='turno_manual',
            categoria='operacional',
            descripcion=f"Turno manual admin en {machine_name} â€” {estacion_nombre}",
            usuario=user_name,
            usuario_id=user_id,
            maquina_id=machine_id,
            maquina_nombre=machine_name,
            entidad='machine',
            entidad_id=machine_id,
            datos_extra={
                'command_id': command_id,
                'station_index': station_index,
                'estacion_nombre': estacion_nombre,
                'origen': 'admin_manual'
            }
        )

        app.logger.info(f"âœ… Turno manual admin â€” MÃ¡quina: {machine_name} ({machine_id}) | EstaciÃ³n: {estacion_nombre} | Command ID: {command_id} | Admin: {user_name}")

        return api_response(
            'S014',
            status='success',
            data={
                'machine_id': machine_id,
                'machine_name': machine_name,
                'command_id': command_id,
                'station_index': station_index,
                'estacion_nombre': estacion_nombre,
                'timestamp': hora_actual.isoformat(),
                'message': f'Comando enviado al ESP32 (ID: {command_id}). Sin uso de QR.'
            }
        )
        
    except Exception as e:
        app.logger.error(f"Error ingresando turno manual: {e}", exc_info=True)
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500, data={'error_detail': str(e)})
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/maquinas/reiniciar', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
@validate_required_fields(['machine_id', 'machine_name'])
def reiniciar_maquina_manual():
    """
    Endpoint para que el administrador pueda REINICIAR una mÃ¡quina
    EnvÃ­a comando de reinicio y registra el evento
    """
    connection = None
    cursor = None
    try:
        data = request.get_json()
        machine_id = data['machine_id']
        machine_name = data['machine_name']
        user_id = session.get('user_id')
        user_name = session.get('user_name', 'Administrador')
        
        app.logger.info(f"ðŸ”„ [ADMIN] Reiniciando mÃ¡quina - {machine_name} (ID: {machine_id})")
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        # Verificar que la mÃ¡quina existe
        cursor.execute("SELECT id, name, status FROM machine WHERE id = %s", (machine_id,))
        maquina = cursor.fetchone()
        
        if not maquina:
            return api_response('M001', http_status=404, data={'machine_id': machine_id})
        
        # Obtener datos tÃ©cnicos de la mÃ¡quina
        cursor.execute("""
            SELECT reset_time_seconds 
            FROM machinetechnical 
            WHERE machine_id = %s
        """, (machine_id,))
        
        tech_data = cursor.fetchone()
        reset_time = tech_data['reset_time_seconds'] if tech_data else 5  # Default 5 segundos
        
        # Obtener el Ãºltimo QR usado (si existe)
        cursor.execute("""
            SELECT qr.code, qr.qr_name, tu.id as usage_id
            FROM turnusage tu
            JOIN qrcode qr ON tu.qrCodeId = qr.id
            WHERE tu.machineId = %s
            ORDER BY tu.usedAt DESC
            LIMIT 1
        """, (machine_id,))
        
        ultimo_uso = cursor.fetchone()
        
        # Registrar el reinicio en machine_resets
        try:
            cursor.execute("""
                INSERT INTO machine_resets 
                (machine_id, machine_name, triggered_by, triggered_by_name, reset_time_seconds, 
                 qr_code, usage_id, status, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                machine_id,
                machine_name,
                user_id,
                user_name,
                reset_time,
                ultimo_uso['code'] if ultimo_uso else None,
                ultimo_uso['usage_id'] if ultimo_uso else None,
                'sent',
                f'Reinicio manual solicitado por {user_name}'
            ))
            
            reset_id = cursor.lastrowid
            
        except Exception as e:
            app.logger.warning(f"Error insertando en machine_resets: {e}")
            reset_id = None
        
        # Obtener estaciÃ³n desde el request (para multi-estaciÃ³n)
        station_index = data.get('estacion', 0)
        estacion_nombre = data.get('estacion_nombre', f'EstaciÃ³n {station_index + 1}')

        # Registrar en esp32_commands
        hora_actual = get_colombia_time()
        try:
            cursor.execute("""
                INSERT INTO esp32_commands (machine_id, command, parameters, triggered_by, status, triggered_at)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                machine_id,
                'RESET',
                json.dumps({
                    'reset_time': reset_time,
                    'machine_name': machine_name,
                    'station_index': station_index,
                    'estacion_nombre': estacion_nombre,
                    'restart_tft': True
                }),
                user_name,
                'queued',
                format_datetime_for_db(hora_actual)
            ))
            command_id = cursor.lastrowid
            app.logger.info(f"âœ… Comando RESET encolado con ID: {command_id} (estaciÃ³n {station_index})")
        except Exception as e:
            app.logger.error(f"Error insertando en esp32_commands: {e}")
            command_id = None
        
        # Registrar en logs de aplicaciÃ³n
        cursor.execute("""
            INSERT INTO app_logs (level, module, message, user_id, created_at)
            VALUES (%s, %s, %s, %s, %s)
        """, ('INFO', 'machine_action', 
              f"Admin {user_name} solicitÃ³ reinicio de mÃ¡quina {machine_name} (ID: {machine_id})", 
              user_id,
              format_datetime_for_db(hora_actual)))
        
        connection.commit()
        
        app.logger.info(f"âœ… Reinicio registrado para mÃ¡quina {machine_name} - Reset ID: {reset_id}, Command ID: {command_id}")
        
        return api_response(
            'S015',
            status='success',
            data={
                'machine_id': machine_id,
                'machine_name': machine_name,
                'reset_id': reset_id,
                'command_id': command_id,
                'reset_time_seconds': reset_time,
                'message': f'Comando de reinicio enviado a la mÃ¡quina. Tiempo estimado: {reset_time} segundos',
                'command': 'RESET'
            }
        )
        
    except Exception as e:
        app.logger.error(f"Error reiniciando mÃ¡quina: {e}", exc_info=True)
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500, data={'error_detail': str(e)})
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/logs/accion', methods=['POST'])
@handle_api_errors
@require_login(['admin', 'cajero'])
def registrar_log_accion():
    """
    Endpoint para registrar logs de acciones desde el frontend
    """
    try:
        data = request.get_json()
        accion = data.get('accion')
        detalles = data.get('detalles', {})
        usuario = data.get('usuario', session.get('user_name', 'Desconocido'))
        timestamp = data.get('timestamp')
        
        app.logger.info(f"[LOG ACCIÃ“N] {accion} - {usuario} - {json.dumps(detalles)}")
        
        # Registrar en base de datos
        connection = get_db_connection()
        if connection:
            cursor = get_db_cursor(connection)
            
            try:
                # CORREGIDO: Formato de fecha correcto para MySQL
                # Si viene timestamp ISO, convertirlo a formato MySQL
                fecha_mysql = None
                if timestamp:
                    # Convertir de ISO a formato MySQL (YYYY-MM-DD HH:MM:SS)
                    try:
                        # Eliminar la 'Z' y reemplazar T con espacio
                        fecha_iso = timestamp.replace('Z', '').replace('T', ' ')
                        # Tomar solo hasta los segundos (19 caracteres)
                        if len(fecha_iso) > 19:
                            fecha_iso = fecha_iso[:19]
                        fecha_mysql = fecha_iso
                    except:
                        fecha_mysql = format_datetime_for_db(get_colombia_time())
                else:
                    fecha_mysql = format_datetime_for_db(get_colombia_time())
                
                mensaje = f"AcciÃ³n: {accion} | Detalles: {json.dumps(detalles)} | Usuario: {usuario}"
                cursor.execute("""
                    INSERT INTO app_logs (level, module, message, user_id, created_at)
                    VALUES (%s, %s, %s, %s, %s)
                """, ('INFO', 'frontend_action', mensaje[:500], session.get('user_id'), fecha_mysql))
                
                connection.commit()
            except Exception as db_error:
                app.logger.warning(f"No se pudo insertar en app_logs: {db_error}")
                # Intentar sin fecha
                try:
                    cursor.execute("""
                        INSERT INTO app_logs (level, module, message, user_id)
                        VALUES (%s, %s, %s, %s)
                    """, ('INFO', 'frontend_action', mensaje[:500], session.get('user_id')))
                    connection.commit()
                except:
                    pass
            
            cursor.close()
            connection.close()
        
        return api_response('S001', status='success', data={'logged': True})
        
    except Exception as e:
        app.logger.error(f"Error registrando log de acciÃ³n: {e}")
        return api_response('E001', http_status=500)

# ==================== APIS PARA MENSAJES DEL SISTEMA ====================

@app.route('/api/mensajes', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_mensajes():
    """Obtener todos los mensajes del sistema"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("""
            SELECT id, message_code, message_type, message_text, language_code,
                   created_at, updated_at
            FROM system_messages
            ORDER BY message_code, language_code
        """)
        
        mensajes = cursor.fetchall()

        for mensaje in mensajes:
            if mensaje['created_at']:
                fecha_colombia = parse_db_datetime(mensaje['created_at'])
                mensaje['created_at'] = fecha_colombia.strftime('%Y-%m-%d %H:%M:%S')
            if mensaje['updated_at']:
                fecha_colombia = parse_db_datetime(mensaje['updated_at'])
                mensaje['updated_at'] = fecha_colombia.strftime('%Y-%m-%d %H:%M:%S')
        
        return jsonify(mensajes)
        
    except Exception as e:
        app.logger.error(f"Error obteniendo mensajes: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/mensajes', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
@validate_required_fields(['message_code', 'message_type', 'message_text'])
def crear_mensaje():
    """Crear un nuevo mensaje"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        message_code = data['message_code'].upper()
        message_type = data['message_type']
        message_text = data['message_text']
        language_code = data.get('language_code', 'es')

        if message_type not in ['error', 'success', 'warning', 'info']:
            return api_response('E005', http_status=400, data={'field': 'message_type'})
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)

        cursor.execute("""
            SELECT id FROM system_messages 
            WHERE message_code = %s AND language_code = %s
        """, (message_code, language_code))
        
        if cursor.fetchone():
            return api_response(
                'E007',
                http_status=400,
                data={
                    'message': f'El cÃ³digo {message_code} ya existe para el idioma {language_code}'
                }
            )

        cursor.execute("""
            INSERT INTO system_messages 
            (message_code, message_type, message_text, language_code)
            VALUES (%s, %s, %s, %s)
        """, (message_code, message_type, message_text, language_code))
        
        connection.commit()

        MessageService.clear_cache()
        
        app.logger.info(f"Mensaje creado: {message_code} ({message_type})")
        
        return api_response(
            'S002',
            status='success',
            data={'message_id': cursor.lastrowid}
        )
        
    except Exception as e:
        app.logger.error(f"Error creando mensaje: {e}")
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/mensajes/<int:mensaje_id>', methods=['PUT'])
@handle_api_errors
@require_login(['admin'])
def actualizar_mensaje(mensaje_id):
    """Actualizar un mensaje existente"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        
        if not data:
            return api_response('E005', http_status=400)
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)

        cursor.execute("SELECT message_code FROM system_messages WHERE id = %s", (mensaje_id,))
        mensaje = cursor.fetchone()
        
        if not mensaje:
            return api_response('E002', http_status=404, data={'mensaje_id': mensaje_id})

        update_fields = []
        update_values = []
        
        if 'message_text' in data:
            update_fields.append("message_text = %s")
            update_values.append(data['message_text'])
        
        if 'message_type' in data:
            if data['message_type'] not in ['error', 'success', 'warning', 'info']:
                return api_response('E005', http_status=400, data={'field': 'message_type'})
            update_fields.append("message_type = %s")
            update_values.append(data['message_type'])
        
        if 'language_code' in data:
            update_fields.append("language_code = %s")
            update_values.append(data['language_code'])
        
        if not update_fields:
            return api_response('E005', http_status=400, data={'message': 'No hay campos para actualizar'})
        
        update_values.append(mensaje_id)
        update_query = f"UPDATE system_messages SET {', '.join(update_fields)} WHERE id = %s"
        
        cursor.execute(update_query, update_values)
        connection.commit()

        MessageService.clear_cache()
        
        app.logger.info(f"Mensaje actualizado: {mensaje['message_code']} (ID: {mensaje_id})")
        
        return api_response('S003', status='success')
        
    except Exception as e:
        app.logger.error(f"Error actualizando mensaje: {e}")
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/mensajes/<int:mensaje_id>', methods=['DELETE'])
@handle_api_errors
@require_login(['admin'])
def eliminar_mensaje(mensaje_id):
    """Eliminar un mensaje"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)

        cursor.execute("SELECT message_code FROM system_messages WHERE id = %s", (mensaje_id,))
        mensaje = cursor.fetchone()
        
        if not mensaje:
            return api_response('E002', http_status=404, data={'mensaje_id': mensaje_id})

        codigos_esenciales = ['E001', 'E002', 'A001', 'S001']
        if mensaje['message_code'] in codigos_esenciales:
            return api_response(
                'E007',
                http_status=400,
                data={'message': 'No se pueden eliminar mensajes del sistema esenciales'}
            )

        cursor.execute("DELETE FROM system_messages WHERE id = %s", (mensaje_id,))
        connection.commit()

        MessageService.clear_cache()
        
        app.logger.info(f"Mensaje eliminado: {mensaje['message_code']} (ID: {mensaje_id})")
        
        return api_response('S004', status='success')
        
    except Exception as e:
        app.logger.error(f"Error eliminando mensaje: {e}")
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/mensajes/recargar-cache', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
def recargar_cache_mensajes():
    """Forzar recarga del cache de mensajes"""
    try:
        MessageService.clear_cache()
        app.logger.info("Cache de mensajes recargado")
        return api_response('S003', status='success', data={'message': 'Cache recargado'})
    except Exception as e:
        app.logger.error(f"Error recargando cache: {e}")
        return api_response('E001', http_status=500)
    
@app.route('/api/mensajes/buscar', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def buscar_mensajes():
    """Buscar mensajes con filtros"""
    connection = None
    cursor = None
    try:
        query = request.args.get('q', '').strip()
        tipo = request.args.get('tipo', '')
        idioma = request.args.get('idioma', '')
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)

        condiciones = []
        parametros = []
        
        if query:
            condiciones.append("(message_code LIKE %s OR message_text LIKE %s)")
            parametros.append(f"%{query}%")
            parametros.append(f"%{query}%")
        
        if tipo and tipo != 'todos':
            condiciones.append("message_type = %s")
            parametros.append(tipo)
        
        if idioma and idioma != 'todos':
            condiciones.append("language_code = %s")
            parametros.append(idioma)
        
        where_clause = " WHERE " + " AND ".join(condiciones) if condiciones else ""
        
        sql = f"""
            SELECT id, message_code, message_type, message_text, language_code,
                   created_at, updated_at
            FROM system_messages
            {where_clause}
            ORDER BY message_code, language_code
        """
        
        cursor.execute(sql, parametros)
        mensajes = cursor.fetchall()

        for mensaje in mensajes:
            if mensaje['created_at']:
                fecha_colombia = parse_db_datetime(mensaje['created_at'])
                mensaje['created_at'] = fecha_colombia.strftime('%Y-%m-%d %H:%M:%S')
            if mensaje['updated_at']:
                fecha_colombia = parse_db_datetime(mensaje['updated_at'])
                mensaje['updated_at'] = fecha_colombia.strftime('%Y-%m-%d %H:%M:%S')
        
        return jsonify({
            'resultados': mensajes,
            'total': len(mensajes),
            'parametros': {
                'query': query,
                'tipo': tipo,
                'idioma': idioma
            }
        })
        
    except Exception as e:
        app.logger.error(f"Error buscando mensajes: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/mensajes/validar-codigo/<codigo>', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def validar_codigo_mensaje(codigo):
    """Validar si un cÃ³digo de mensaje estÃ¡ disponible"""
    connection = None
    cursor = None
    try:
        import re

        if not re.match(r'^[A-Z][0-9]{3}$', codigo):
            return jsonify({
                'valido': False,
                'mensaje': 'Formato invÃ¡lido. Debe ser letra mayÃºscula seguida de 3 nÃºmeros (ej: E001)'
            })
        
        connection = get_db_connection()
        if not connection:
            return jsonify({
                'valido': False,
                'mensaje': 'Error de conexiÃ³n a la base de datos'
            })
            
        cursor = get_db_cursor(connection)

        cursor.execute("""
            SELECT language_code, message_type, message_text
            FROM system_messages
            WHERE message_code = %s
        """, (codigo,))
        
        mensajes = cursor.fetchall()
        
        if not mensajes:
            return jsonify({
                'valido': True,
                'disponible': True,
                'mensaje': 'CÃ³digo disponible para todos los idiomas'
            })

        idiomas_existentes = [m['language_code'] for m in mensajes]
        idiomas_disponibles = ['es', 'en']
        idiomas_faltantes = [idioma for idioma in idiomas_disponibles if idioma not in idiomas_existentes]
        
        if not idiomas_faltantes:
            return jsonify({
                'valido': True,
                'disponible': False,
                'mensaje': f'CÃ³digo ya existe en todos los idiomas (es, en)',
                'detalles': mensajes
            })
        
        return jsonify({
            'valido': True,
            'disponible': True,
            'mensaje': f'CÃ³digo disponible para idiomas: {", ".join(idiomas_faltantes)}',
            'idiomas_faltantes': idiomas_faltantes,
            'detalles': mensajes
        })
        
    except Exception as e:
        app.logger.error(f"Error validando cÃ³digo: {e}")
        return jsonify({
            'valido': False,
            'mensaje': f'Error interno: {str(e)}'
        })
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# ==================== APIS PARA DASHBOARD ====================

@app.route('/api/dashboard/estadisticas', methods=['GET'])
@handle_api_errors
def obtener_estadisticas_dashboard():
    if not session.get('logged_in'):
        return api_response('E003', http_status=401)
    permisos = get_user_permissions()
    if 'ver_dashboard' not in permisos and 'admin_panel' not in permisos:
        return api_response('E004', http_status=403)
    """Obtener estadÃ­sticas principales para el dashboard"""
    connection = None
    cursor = None
    try:
        fecha_inicio = request.args.get('fecha_inicio', get_colombia_time().strftime('%Y-%m-%d'))
        fecha_fin = request.args.get('fecha_fin', get_colombia_time().strftime('%Y-%m-%d'))
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)

        cursor.execute("""
            SELECT 
                COALESCE(SUM(tp.price), 0) as ingresos_totales,
                COUNT(DISTINCT qh.qr_code) as paquetes_vendidos
            FROM qrhistory qh
            LEFT JOIN qrcode qr ON qr.code = qh.qr_code
            LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
            AND qr.turnPackageId IS NOT NULL
            AND qr.turnPackageId != 1
            AND qh.es_venta_real = TRUE
        """, (fecha_inicio, fecha_fin))
        
        ingresos = cursor.fetchone()

        cursor.execute("""
            SELECT 
                COUNT(CASE WHEN status = 'activa' THEN 1 END) as maquinas_activas,
                COUNT(*) as maquinas_totales
            FROM machine
        """)
        
        maquinas = cursor.fetchone()

        cursor.execute("""
            SELECT 
                CASE 
                    WHEN COUNT(DISTINCT qh.qr_code) > 0 THEN 
                        COALESCE(SUM(tp.price), 0) / COUNT(DISTINCT qh.qr_code)
                    ELSE 0 
                END as ticket_promedio
            FROM qrhistory qh
            LEFT JOIN qrcode qr ON qr.code = qh.qr_code
            LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
            AND qr.turnPackageId IS NOT NULL
            AND qr.turnPackageId != 1
        """, (fecha_inicio, fecha_fin))
        
        ticket = cursor.fetchone()

        fecha_inicio_anterior = (datetime.strptime(fecha_inicio, '%Y-%m-%d') - timedelta(days=7)).strftime('%Y-%m-%d')
        fecha_fin_anterior = (datetime.strptime(fecha_fin, '%Y-%m-%d') - timedelta(days=7)).strftime('%Y-%m-%d')
        
        cursor.execute("""
            SELECT 
                COALESCE(SUM(tp.price), 0) as ingresos_anterior,
                COUNT(DISTINCT qh.qr_code) as paquetes_anterior
            FROM qrhistory qh
            LEFT JOIN qrcode qr ON qr.code = qh.qr_code
            LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
            AND qr.turnPackageId IS NOT NULL
            AND qr.turnPackageId != 1
        """, (fecha_inicio_anterior, fecha_fin_anterior))
        
        anterior = cursor.fetchone()

        ingresos_actual = float(ingresos['ingresos_totales'] or 0)
        ingresos_previo = float(anterior['ingresos_anterior'] or 0)
        
        paquetes_actual = ingresos['paquetes_vendidos'] or 0
        paquetes_previo = anterior['paquetes_anterior'] or 0
        
        tendencia_ingresos = 0
        if ingresos_previo > 0:
            tendencia_ingresos = ((ingresos_actual - ingresos_previo) / ingresos_previo) * 100
        
        tendencia_paquetes = 0
        if paquetes_previo > 0:
            tendencia_paquetes = ((paquetes_actual - paquetes_previo) / paquetes_previo) * 100
        
        app.logger.info(f"Dashboard stats: {ingresos_actual} ingresos, {paquetes_actual} paquetes")
        
        return jsonify({
            'ingresos_totales': ingresos_actual,
            'paquetes_vendidos': paquetes_actual,
            'maquinas_activas': maquinas['maquinas_activas'] or 0,
            'maquinas_totales': maquinas['maquinas_totales'] or 0,
            'ticket_promedio': float(ticket['ticket_promedio'] or 0),
            'tendencias': {
                'ingresos': round(tendencia_ingresos, 1),
                'paquetes': round(tendencia_paquetes, 1)
            },
            'rango_fechas': {
                'inicio': fecha_inicio,
                'fin': fecha_fin
            },
            'timestamp': get_colombia_time().isoformat()
        })
        
    except Exception as e:
        app.logger.error(f"Error obteniendo estadÃ­sticas dashboard: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/dashboard/graficas', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
def obtener_graficas_dashboard():
    """Obtener datos para grÃ¡ficas del dashboard"""
    connection = None
    cursor = None
    try:
        fecha_inicio = request.args.get('fecha_inicio', get_colombia_time().strftime('%Y-%m-%d'))
        fecha_fin = request.args.get('fecha_fin', get_colombia_time().strftime('%Y-%m-%d'))
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)

        tipo_agrupacion = request.args.get('tipo', 'diario')

        if tipo_agrupacion == 'mensual':
            cursor.execute("""
                SELECT 
                    DATE_FORMAT(qh.fecha_hora, '%Y-%m') as fecha,
                    COUNT(DISTINCT qh.qr_code) as ventas,
                    COALESCE(SUM(tp.price), 0) as ingresos
                FROM qrhistory qh
                LEFT JOIN qrcode qr ON qr.code = qh.qr_code
                LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
                WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
                AND qr.turnPackageId IS NOT NULL
                AND qr.turnPackageId != 1
                AND qh.es_venta_real = TRUE
                GROUP BY DATE_FORMAT(qh.fecha_hora, '%Y-%m')
                ORDER BY fecha
            """, (fecha_inicio, fecha_fin))
        elif tipo_agrupacion == 'semanal':
            cursor.execute("""
                SELECT 
                    DATE_FORMAT(qh.fecha_hora, '%Y-S%u') as fecha,
                    COUNT(DISTINCT qh.qr_code) as ventas,
                    COALESCE(SUM(tp.price), 0) as ingresos
                FROM qrhistory qh
                LEFT JOIN qrcode qr ON qr.code = qh.qr_code
                LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
                WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
                AND qr.turnPackageId IS NOT NULL
                AND qr.turnPackageId != 1
                AND qh.es_venta_real = TRUE
                GROUP BY DATE_FORMAT(qh.fecha_hora, '%Y-%u')
                ORDER BY fecha
            """, (fecha_inicio, fecha_fin))
        else:
            cursor.execute("""
                SELECT 
                    DATE(qh.fecha_hora) as fecha,
                    COUNT(DISTINCT qh.qr_code) as ventas,
                    COALESCE(SUM(tp.price), 0) as ingresos
                FROM qrhistory qh
                LEFT JOIN qrcode qr ON qr.code = qh.qr_code
                LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
                WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
                AND qr.turnPackageId IS NOT NULL
                AND qr.turnPackageId != 1
                AND qh.es_venta_real = TRUE
                GROUP BY DATE(qh.fecha_hora)
                ORDER BY fecha
            """, (fecha_inicio, fecha_fin))

        evolucion_data = cursor.fetchall()

        evolucion_ventas = {
            'labels': [str(item['fecha']) for item in evolucion_data],
            'data': [float(item['ingresos']) for item in evolucion_data]
        }

        cursor.execute("""
            SELECT 
                tp.name as paquete,
                COUNT(DISTINCT qh.qr_code) as cantidad,
                SUM(tp.price) as ingresos
            FROM qrhistory qh
            LEFT JOIN qrcode qr ON qr.code = qh.qr_code
            LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
            AND qr.turnPackageId IS NOT NULL
            AND qr.turnPackageId != 1
            AND qh.es_venta_real = TRUE
            GROUP BY tp.id, tp.name
            ORDER BY ingresos DESC
            LIMIT 10
        """, (fecha_inicio, fecha_fin))
        
        paquetes_data = cursor.fetchall()
        
        ventas_paquetes = {
            'labels': [item['paquete'] for item in paquetes_data],
            'data': [item['cantidad'] for item in paquetes_data]
        }

        cursor.execute("""
            SELECT 
                m.name as maquina,
                COUNT(tu.id) as usos,
                COALESCE(SUM(tp.price), 0) as ingresos
            FROM machine m
            LEFT JOIN turnusage tu ON tu.machineId = m.id 
                AND DATE(tu.usedAt) BETWEEN %s AND %s
            LEFT JOIN qrcode qr ON tu.qrCodeId = qr.id
            LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
            GROUP BY m.id, m.name
            ORDER BY ingresos DESC, usos DESC
            LIMIT 10
        """, (fecha_inicio, fecha_fin))

        maquinas_data = cursor.fetchall()

        rendimiento_maquinas = {
            'labels': [item['maquina'] for item in maquinas_data],
            'data': [float(item['ingresos']) for item in maquinas_data]
        }

        cursor.execute("""
            SELECT 
                COUNT(CASE WHEN status = 'activa' THEN 1 END) as activas,
                COUNT(CASE WHEN status = 'mantenimiento' THEN 1 END) as mantenimiento,
                COUNT(CASE WHEN status = 'inactiva' THEN 1 END) as inactivas
            FROM machine
        """)
        
        estado_data = cursor.fetchone()
        
        estado_maquinas = [
            estado_data['activas'] or 0,
            estado_data['mantenimiento'] or 0,
            estado_data['inactivas'] or 0
        ]
        
        return jsonify({
            'evolucion_ventas': evolucion_ventas,
            'ventas_paquetes': ventas_paquetes,
            'rendimiento_maquinas': rendimiento_maquinas,
            'estado_maquinas': estado_maquinas,
            'rango_fechas': {
                'inicio': fecha_inicio,
                'fin': fecha_fin
            },
            'timestamp': get_colombia_time().isoformat()
        })
        
    except Exception as e:
        app.logger.error(f"Error obteniendo grÃ¡ficas dashboard: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/maquinas/<int:maquina_id>/resolver-falla', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
def resolver_falla_maquina(maquina_id):
    connection = None
    cursor = None
    try:
        data = request.get_json() or {}
        estacion_index = data.get('estacion_index', None)

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)

        cursor.execute("SELECT id, name, status FROM machine WHERE id = %s", (maquina_id,))
        maquina = cursor.fetchone()
        if not maquina:
            return api_response('E005', http_status=404, data={'message': 'MÃ¡quina no encontrada'})

        # Limpiar errorNote y reactivar mÃ¡quina
        cursor.execute("""
            UPDATE machine
            SET errorNote = NULL, status = 'activa'
            WHERE id = %s
        """, (maquina_id,))

        # Resolver fallas en machinefailures
        if estacion_index is not None:
            cursor.execute("""
                UPDATE machinefailures 
                SET resolved = 1, resolved_at = NOW()
                WHERE machine_id = %s AND station_index = %s AND resolved = 0
            """, (maquina_id, estacion_index))
        else:
            cursor.execute("""
                UPDATE machinefailures 
                SET resolved = 1, resolved_at = NOW()
                WHERE machine_id = %s AND resolved = 0
            """, (maquina_id,))

        # Resolver reportes manuales en errorreport
        cursor.execute("""
            UPDATE errorreport 
            SET isResolved = 1, resolved_at = NOW()
            WHERE machineId = %s AND isResolved = 0
        """, (maquina_id,))

        # Enviar comando RESUME al ESP32 para reanudar operaciÃ³n normal
        try:
            cursor.execute("""
                INSERT INTO esp32_commands
                (machine_id, command, parameters, triggered_by, status, triggered_at)
                VALUES (%s, 'RESUME', %s, %s, 'queued', NOW())
            """, (maquina_id, json.dumps({
                'machine_name': maquina['name'],
                'estacion_index': estacion_index,
                'resolved_by': session.get('user_name', 'admin')
            }), session.get('user_name', 'admin')))
        except Exception as cmd_err:
            app.logger.error(f"No se pudo encolar RESUME: {cmd_err}")

        connection.commit()
        estacion_str = f" estaciÃ³n {estacion_index}" if estacion_index is not None else ""
        app.logger.info(f"Falla resuelta â€” MÃ¡quina: {maquina['name']} ({maquina_id}){estacion_str} | Admin: {session.get('user_name','-')}")

        _log_transaccion(
            tipo='resolver_falla',
            categoria='operacional',
            descripcion=f"Falla resuelta en {maquina['name']}" + estacion_str,
            usuario=session.get('user_name'),
            usuario_id=session.get('user_id'),
            maquina_id=maquina_id,
            maquina_nombre=maquina['name'],
            entidad='machine',
            entidad_id=maquina_id,
            datos_extra={'estacion_index': estacion_index}
        )

        return jsonify({
            'success': True,
            'message': f'Falla resuelta en {maquina["name"]}'
        })

    except Exception as e:
        app.logger.error(f"Error resolviendo falla: {e}")
        if connection: connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor: cursor.close()
        if connection: connection.close()

@app.route('/api/dashboard/top-maquinas', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
def obtener_top_maquinas():
    """Obtener top 5 mÃ¡quinas por usos"""
    connection = None
    cursor = None
    try:
        fecha_inicio = request.args.get('fecha_inicio', get_colombia_time().strftime('%Y-%m-%d'))
        fecha_fin = request.args.get('fecha_fin', get_colombia_time().strftime('%Y-%m-%d'))

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("""
            SELECT 
                m.name as nombre,
                COUNT(tu.id) as usos
            FROM machine m
            INNER JOIN turnusage tu ON tu.machineId = m.id
            WHERE DATE(tu.usedAt) BETWEEN %s AND %s
            GROUP BY m.id, m.name
            ORDER BY usos DESC
            LIMIT 5
        """, (fecha_inicio, fecha_fin))

        top_maquinas = cursor.fetchall()

        maquinas_formateadas = []
        for maquina in top_maquinas:
            maquinas_formateadas.append({
                'nombre': maquina['nombre'],
                'usos': maquina['usos'] or 0,
                'ventas': maquina['usos'] or 0,
                'ingresos': 0
            })

        return jsonify(maquinas_formateadas)

    except Exception as e:
        app.logger.error(f"Error obteniendo top mÃ¡quinas: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/dashboard/ventas-recientes', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
def obtener_ventas_recientes():
    """Obtener las Ãºltimas 50 ventas"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("""
            SELECT 
                qh.qr_code,
                qh.user_name,
                qh.fecha_hora,
                tp.name as paquete,
                tp.price as precio
            FROM qrhistory qh
            LEFT JOIN qrcode qr ON qr.code = qh.qr_code
            LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE qr.turnPackageId IS NOT NULL
            AND qr.turnPackageId != 1
            AND qh.es_venta_real = TRUE
           ORDER BY qh.fecha_hora DESC
           LIMIT 50
        """)
        
        ventas = cursor.fetchall()

        ventas_formateadas = []
        for venta in ventas:

            fecha_hora = venta['fecha_hora']
            if fecha_hora:
                try:
                    fecha_colombia = parse_db_datetime(fecha_hora)
                    hora_formateada = fecha_colombia.strftime('%H:%M')
                    fecha_formateada = fecha_colombia.strftime('%Y-%m-%d')
                except Exception as e:
                    app.logger.warning(f"Error formateando fecha: {e}")
                    hora_formateada = str(fecha_hora)
                    fecha_formateada = str(fecha_hora)
            else:
                hora_formateada = "N/A"
                fecha_formateada = "N/A"
            
            ventas_formateadas.append({
                'qr_code': venta['qr_code'],
                'usuario': venta['user_name'],
                'paquete': venta['paquete'] or 'Sin paquete',
                'precio': float(venta['precio'] or 0),
                'hora': hora_formateada,
                'fecha': fecha_formateada
            })
        
        return jsonify(ventas_formateadas)
        
    except Exception as e:
        app.logger.error(f"Error obteniendo ventas recientes: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# ==================== APIS PARA CAMBIAR ESTADO DE USUARIOS ====================

@app.route('/api/usuarios/<int:usuario_id>/estado', methods=['PUT'])
@handle_api_errors
@require_login(['admin'])
def cambiar_estado_usuario(usuario_id):
    """Cambiar estado activo/inactivo de un usuario"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        is_active = data['isActive']
        
        if usuario_id == session.get('user_id'):
            return api_response('U005', http_status=400, data={
                'message': 'No puedes cambiar tu propio estado'
            })
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)

        cursor.execute("SELECT name FROM users WHERE id = %s", (usuario_id,))
        usuario = cursor.fetchone()
        if not usuario:
            return api_response('U001', http_status=404, data={'usuario_id': usuario_id})

        cursor.execute("""
            UPDATE users 
            SET isActive = %s,
                updatedAt = NOW()
            WHERE id = %s
        """, (1 if is_active else 0, usuario_id))

        app.logger.info(f"Filas afectadas: {cursor.rowcount}, isActive: {1 if is_active else 0}, usuario_id: {usuario_id}")
        
        connection.commit()
        
        app.logger.info(f"Estado de usuario cambiado: {usuario['name']} (ID: {usuario_id}, Activo: {is_active})")
        
        return api_response('S003', status='success', data={
            'isActive': is_active,
            'message': f'Usuario {"activado" if is_active else "desactivado"} correctamente'
        })
        
    except Exception as e:
        app.logger.error(f"Error cambiando estado de usuario: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# ==================== APIS PARA ESTADÃSTICAS DE USUARIOS ====================

@app.route('/api/usuarios/estadisticas', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_estadisticas_usuarios():
    """Obtener estadÃ­sticas de usuarios"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)

        cursor.execute("""
            SELECT 
                COUNT(*) as total,
                COUNT(CASE WHEN isActive = TRUE OR isActive IS NULL THEN 1 END) as activos,
                COUNT(CASE WHEN isActive = FALSE THEN 1 END) as inactivos,
                COUNT(CASE WHEN role = 'admin' THEN 1 END) as admins,
                COUNT(CASE WHEN role = 'cajero' THEN 1 END) as cajeros,
                COUNT(CASE WHEN role = 'admin_restaurante' THEN 1 END) as admin_restaurante,
                COUNT(CASE WHEN role = 'socio' THEN 1 END) as socios
            FROM users
        """)
        
        estadisticas = cursor.fetchone()
        
        return jsonify({
            'total': estadisticas['total'] or 0,
            'activos': estadisticas['activos'] or 0,
            'inactivos': estadisticas['inactivos'] or 0,
            'admins': estadisticas['admins'] or 0,
            'cajeros': estadisticas['cajeros'] or 0,
            'admin_restaurante': estadisticas['admin_restaurante'] or 0,
            'socios': estadisticas['socios'] or 0,
            'timestamp': get_colombia_time().isoformat()
        })
        
    except Exception as e:
        app.logger.error(f"Error obteniendo estadÃ­sticas de usuarios: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# ==================== RUTAS DE DEBUG ====================

@app.route('/debug/session')
def debug_session():
    return jsonify(dict(session))

@app.route('/check-session')
def check_session():
    return jsonify({
        "session_working": True,
        "logged_in": session.get('logged_in', False),
        "user_name": session.get('user_name', 'No user')
    })

@app.route('/health')
def health_check():
    return jsonify({"status": "ok", "message": "Server is running"})

@app.route('/test-sentry-activo')
def test_sentry_activo():
    try:
        resultado = 10 / 0
        return "Esto no deberÃ­a mostrarse"
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return f"âœ… Error capturado y enviado a Sentry: {str(e)}"

@app.route('/api/debug/mensaje/<message_code>', methods=['GET'])
@handle_api_errors
def debug_mensaje(message_code):
    """Endpoint para probar mensajes"""
    language = request.args.get('language', 'es')
    formato = request.args.get('formato', 'json')
    
    if formato == 'texto':
        mensaje = MessageService.get_error_message(message_code, language_code=language)
        return mensaje
    else:
        return api_response(message_code, language_code=language)

# ==================== RUTAS PARA ESP32 ====================

@app.route('/api/esp32/status', methods=['GET'])
def esp32_status():
    """Endpoint para verificar estado del servidor desde ESP32"""
    return jsonify({
        'status': 'online',
        'message': 'Servidor funcionando correctamente',
        'timestamp': get_colombia_time().isoformat()
    })

@app.route('/api/esp32/heartbeat', methods=['POST'])
def esp32_heartbeat():
    """
    ESP32 llama a este endpoint cada STATUS_UPDATE_MS (~30s) para reportar
    que sigue activo y cuÃ¡l es su estado de conectividad.
    Body JSON: { machine_id, wifi_connected, server_online, rssi (opcional) }
    """
    data = request.get_json(silent=True) or {}
    machine_id = data.get('machine_id')
    if not machine_id:
        return jsonify({'status': 'error', 'message': 'machine_id requerido'}), 400
    _esp32_heartbeats[int(machine_id)] = {
        'wifi':   bool(data.get('wifi_connected', True)),
        'server': bool(data.get('server_online', True)),
        'rssi':   int(data.get('rssi', 0)),
        'ts':     _time.time()
    }
    return jsonify({'status': 'ok'})


@app.route('/api/esp32/estado-fallas/<int:machine_id>', methods=['GET'])
@handle_api_errors
def esp32_estado_fallas(machine_id):
    """
    El ESP32 consulta este endpoint al arrancar para precargar los contadores de
    fallas consecutivas y saber quÃ© estaciones estÃ¡n en mantenimiento.
    Respuesta: { consecutive_failures: {"0":2,"1":0}, stations_in_maintenance: [0] }
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'db'}), 500
        cursor = get_db_cursor(connection)

        cursor.execute("""
            SELECT consecutive_failures, stations_in_maintenance, status
            FROM machine WHERE id = %s
        """, (machine_id,))
        row = cursor.fetchone()
        if not row:
            return jsonify({'error': 'machine_not_found'}), 404

        try:
            consec = json.loads(row['consecutive_failures'] or '{}')
        except Exception:
            consec = {}
        try:
            en_mant = json.loads(row['stations_in_maintenance'] or '[]')
        except Exception:
            en_mant = []

        return jsonify({
            'machine_id': machine_id,
            'status': row['status'],
            'consecutive_failures': consec,
            'stations_in_maintenance': en_mant,
        })
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


@app.route('/api/esp32/registrar-uso', methods=['POST'])
@handle_api_errors
@validate_required_fields(['qr_code', 'machine_id'])
def esp32_registrar_uso():
    """Registrar uso de mÃ¡quina desde ESP32 - CON SOPORTE PARA ESTACIONES"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        qr_code = data['qr_code']
        machine_id = data['machine_id']
        station_index = data.get('selected_station', 0)  # Por defecto estaciÃ³n 0
        
        app.logger.info(f"ESP32: Registrando uso - QR: {qr_code}, MÃ¡quina: {machine_id}, EstaciÃ³n: {station_index}")
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)

        cursor.execute("SELECT id, qr_name FROM qrcode WHERE code = %s", (qr_code,))
        qr_data = cursor.fetchone()
        if not qr_data:
            return api_response('Q001', http_status=404)
        
        qr_id = qr_data['id']
        qr_name = qr_data['qr_name']
        
        cursor.execute("SELECT turns_remaining FROM userturns WHERE qr_code_id = %s", (qr_id,))
        turnos_data = cursor.fetchone()
        
        if not turnos_data or turnos_data['turns_remaining'] <= 0:
            return api_response('Q003', http_status=400)

        turns_after = turnos_data['turns_remaining'] - 1

        # Insertar con station_index y turns_remaining_after (V36); fallback si columna no existe
        try:
            cursor.execute("""
                INSERT INTO turnusage (qrCodeId, machineId, station_index, turns_remaining_after, usedAt)
                VALUES (%s, %s, %s, %s, NOW())
            """, (qr_id, machine_id, station_index, turns_after))
        except Exception:
            cursor.execute("""
                INSERT INTO turnusage (qrCodeId, machineId, station_index, usedAt)
                VALUES (%s, %s, %s, NOW())
            """, (qr_id, machine_id, station_index))

        usage_id = cursor.lastrowid
        app.logger.info(f"âœ… USAGE_ID generado: {usage_id}, EstaciÃ³n: {station_index}")

        cursor.execute("UPDATE userturns SET turns_remaining = turns_remaining - 1 WHERE qr_code_id = %s", (qr_id,))

        cursor.execute("UPDATE machine SET dateLastQRUsed = NOW() WHERE id = %s", (machine_id,))
        
        connection.commit()

        cursor.execute("""
            SELECT ut.turns_remaining, tp.name as package_name 
            FROM userturns ut 
            JOIN qrcode qr ON qr.id = ut.qr_code_id
            LEFT JOIN turnpackage tp ON ut.package_id = tp.id
            WHERE ut.qr_code_id = %s
        """, (qr_id,))
        
        info_actualizada = cursor.fetchone()
        
        turnos_restantes = info_actualizada['turns_remaining']
        app.logger.info(f"ESP32: Uso registrado â€” QR: {qr_code} | MÃ¡quina: {machine_id} | EstaciÃ³n: {station_index} | Turnos restantes: {turnos_restantes} | Usage ID: {usage_id}")

        _log_transaccion(
            tipo='turno_qr',
            categoria='operacional',
            descripcion=f"Turno vÃ­a QR {qr_code} ({qr_name}) â€” EstaciÃ³n {station_index}",
            maquina_id=machine_id,
            entidad='qr',
            entidad_id=qr_id,
            datos_extra={
                'qr_code': qr_code,
                'qr_name': qr_name,
                'usage_id': usage_id,
                'station_index': station_index,
                'turns_remaining': turnos_restantes,
                'package_name': info_actualizada['package_name'],
                'origen': 'esp32'
            }
        )

        return api_response(
            'S010',
            status='success',
            data={
                'turns_remaining': turnos_restantes,
                'package_name': info_actualizada['package_name'],
                'qr_name': qr_name,
                'qr_code': qr_code,
                'machine_id': machine_id,
                'usage_id': usage_id,
                'station_index': station_index
            }
        )
        
    except Exception as e:
        app.logger.error(f"Error registrando uso desde ESP32: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/esp32/ultimo-usage/<qr_code>/<int:machine_id>', methods=['GET'])
@handle_api_errors
def esp32_ultimo_usage(qr_code, machine_id):
    """Obtener el Ãºltimo usage_id para un QR y mÃ¡quina especÃ­ficos"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'usage_id': 0, 'error': 'Sin conexiÃ³n'}), 500
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("""
            SELECT tu.id as usage_id
            FROM turnusage tu
            JOIN qrcode qr ON tu.qrCodeId = qr.id
            WHERE qr.code = %s AND tu.machineId = %s
            ORDER BY tu.usedAt DESC
            LIMIT 1
        """, (qr_code, machine_id))
        
        result = cursor.fetchone()
        
        if result:
            return jsonify({'usage_id': result['usage_id']})
        else:
            return jsonify({'usage_id': 0})
            
    except Exception as e:
        app.logger.error(f"Error obteniendo Ãºltimo usage_id: {e}")
        return jsonify({'usage_id': 0, 'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/esp32/check-commands/<int:machine_id>', methods=['GET'])
@handle_api_errors
def esp32_check_commands(machine_id):
    """Endpoint para que el ESP32 consulte comandos pendientes - VERSIÃ“N FINAL CORREGIDA"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'has_commands': False, 'commands': []})
            
        cursor = get_db_cursor(connection)
        
        # Buscar comandos pendientes para esta mÃ¡quina
        # NOTA: Usamos triggered_at que SÃ existe en tu BD
        cursor.execute("""
            SELECT id, command, parameters, triggered_at
            FROM esp32_commands 
            WHERE machine_id = %s AND status = 'queued'
            ORDER BY triggered_at ASC
        """, (machine_id,))
        
        commands = cursor.fetchall()
        
        return jsonify({
            'has_commands': len(commands) > 0,
            'commands': commands
        })
        
    except Exception as e:
        app.logger.error(f"Error checking commands for ESP32: {e}")
        return jsonify({'has_commands': False, 'commands': []})
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@app.route('/api/esp32/command-executed/<int:command_id>', methods=['POST'])
@handle_api_errors
def esp32_command_executed(command_id):
    """Endpoint para que el ESP32 confirme ejecuciÃ³n de comando"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        machine_id = data.get('machine_id')
        estacion = data.get('estacion', 0)
        result = data.get('result', 'success')
        
        app.logger.info(f"âœ… ESP32 confirmÃ³ comando {command_id} - MÃ¡quina: {machine_id}, EstaciÃ³n: {estacion}, Resultado: {result}")
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("""
            UPDATE esp32_commands
            SET status = 'executed',
                executed_at = NOW(),
                response = %s
            WHERE id = %s
        """, (json.dumps(data), command_id))
        
        connection.commit()
        
        return api_response('S001', status='success')
        
    except Exception as e:
        app.logger.error(f"Error updating command status: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/esp32/machine-config/<int:machine_id>', methods=['GET'])
def esp32_machine_config(machine_id):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = get_db_cursor(connection)
        
        # IMPORTANTE: NO incluir station_count (no existe)
        try:
            cursor.execute("""
                SELECT
                    m.id, m.name, m.type, m.status,
                    m.consecutive_failures, m.stations_in_maintenance,
                    mt.credits_virtual, mt.credits_machine,
                    mt.game_duration_seconds, mt.reset_time_seconds,
                    mt.machine_subtype, mt.station_names,
                    mt.game_type, mt.has_failure_report,
                    mt.show_station_selection,
                    (SELECT MAX(usedAt) FROM turnusage WHERE machineId = m.id) as last_play_time
                FROM machine m
                LEFT JOIN machinetechnical mt ON m.id = mt.machine_id
                WHERE m.id = %s
            """, (machine_id,))
        except Exception:
            # MigraciÃ³n V32 pendiente: columnas de fallas por estaciÃ³n aÃºn no existen
            cursor.execute("""
                SELECT
                    m.id, m.name, m.type, m.status,
                    NULL AS consecutive_failures, NULL AS stations_in_maintenance,
                    mt.credits_virtual, mt.credits_machine,
                    mt.game_duration_seconds, mt.reset_time_seconds,
                    mt.machine_subtype, mt.station_names,
                    mt.game_type, mt.has_failure_report,
                    mt.show_station_selection,
                    (SELECT MAX(usedAt) FROM turnusage WHERE machineId = m.id) as last_play_time
                FROM machine m
                LEFT JOIN machinetechnical mt ON m.id = mt.machine_id
                WHERE m.id = %s
            """, (machine_id,))
        
        config = cursor.fetchone()
        
        if not config:
            return jsonify({'status': 'error', 'code': 'M001', 'message': 'MÃ¡quina no encontrada'}), 404
        
        # Procesar station_names (JSON string a array)
        station_names = []
        if config['station_names']:
            try:
                # Si ya es un string JSON, convertirlo
                if isinstance(config['station_names'], str):
                    station_names = json.loads(config['station_names'])
                else:
                    station_names = config['station_names']
            except:
                # Si falla, usar un array con el nombre de la mÃ¡quina
                station_names = [config['name']]
        else:
            # Si estÃ¡ vacÃ­o, crear nombres por defecto segÃºn el subtipo
            if config['machine_subtype'] == 'multi_station':
                station_names = ["EstaciÃ³n 1", "EstaciÃ³n 2"]
            else:
                station_names = [config['name']]
        
        # Construir active_failure_stations desde consecutive_failures
        active_failure_stations = []
        try:
            cf = _parse_json_col(config.get('consecutive_failures'), {})
            sim = _parse_json_col(config.get('stations_in_maintenance'), [])
            for key, count in cf.items():
                if count > 0:
                    idx = int(key) if key != 'all' else 0
                    active_failure_stations.append({
                        'station_index': idx,
                        'count': int(count),
                        'in_maintenance': idx in sim or str(idx) in [str(x) for x in sim]
                    })
        except Exception:
            pass

        response_data = {
            'id': config['id'],
            'name': config['name'],
            'type': config['type'],
            'status': config['status'],
            'credits_virtual': config['credits_virtual'] or 1,
            'credits_machine': config['credits_machine'] or 1,
            'game_duration_seconds': config['game_duration_seconds'] or 180,
            'reset_time_seconds': config['reset_time_seconds'] or 5,
            'machine_subtype': config['machine_subtype'] or 'simple',
            'station_names': station_names,
            'game_type': config['game_type'] or 'time_based',
            'has_failure_report': bool(config['has_failure_report']),
            'show_station_selection': bool(config['show_station_selection']),
            'last_play_time': config['last_play_time'],
            'active_failure_stations': active_failure_stations
        }
        
        return jsonify({'status': 'success', 'data': response_data})
        
    except Exception as e:
        app.logger.error(f"Error obteniendo configuraciÃ³n: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if cursor: cursor.close()
        if connection: connection.close()

# ==================== APIS PARA ESP32 - REPORTE DE FALLA DESDE TFT ====================

@app.route('/api/esp32/reportar-falla', methods=['POST'])
@handle_api_errors
def esp32_reportar_falla():
    """
    Endpoint para recibir reportes de falla desde la TFT/ESP32
    Cuando el usuario presiona el botÃ³n REPORTAR durante el juego
    """
    connection = None
    cursor = None
    try:

        data = request.get_json()
        
        machine_id = data.get('machine_id')
        machine_name = data.get('machine_name')
        qr_code = data.get('qr_code')
        usage_id = data.get('usage_id')
        turnos_devueltos = data.get('turnos_devueltos', 1)
        is_forced = data.get('is_forced', False)
        notes = data.get('notes', 'Reporte desde TFT - BotÃ³n REPORTAR')
        
        app.logger.info(f"ðŸ”„ [TFT] Reporte de falla recibido - MÃ¡quina: {machine_name}, QR: {qr_code}")
        
        if not machine_id or not qr_code:
            return api_response('E005', http_status=400, data={
                'message': 'Faltan datos: machine_id y qr_code son requeridos'
            })
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)

        cursor.execute("SELECT id FROM qrcode WHERE code = %s", (qr_code,))
        qr_data = cursor.fetchone()
        
        if not qr_data:
            app.logger.warning(f"âŒ [TFT] QR no encontrado: {qr_code}")
            return api_response('Q001', http_status=404, data={
                'qr_code': qr_code,
                'message': 'CÃ³digo QR no existe en el sistema'
            })
        
        qr_id = qr_data['id']
        
        # ==========================================
        # 4. VERIFICAR QUE EL USAGE_ID CORRESPONDA
        # ==========================================
        if usage_id:
            cursor.execute("""
                SELECT id, usedAt 
                FROM turnusage 
                WHERE id = %s AND qrCodeId = %s AND machineId = %s
            """, (usage_id, qr_id, machine_id))
            
            uso_data = cursor.fetchone()
            
            if not uso_data:
                app.logger.warning(f"âš ï¸ [TFT] Usage ID {usage_id} no coincide, se usarÃ¡ el Ãºltimo juego")
                usage_id = None  # Forzar bÃºsqueda del Ãºltimo
        
        # ==========================================
        # 5. SI NO HAY USAGE_ID, BUSCAR EL ÃšLTIMO JUEGO
        # ==========================================
        if not usage_id:
            cursor.execute("""
                SELECT id, usedAt
                FROM turnusage
                WHERE qrCodeId = %s AND machineId = %s
                ORDER BY usedAt DESC
                LIMIT 1
            """, (qr_id, machine_id))
            
            ultimo_uso = cursor.fetchone()
            
            if not ultimo_uso:
                app.logger.warning(f"âŒ [TFT] No hay juegos registrados para QR {qr_code} en mÃ¡quina {machine_id}")
                return api_response('E002', http_status=404, data={
                    'message': 'No hay juegos registrados para este QR en esta mÃ¡quina'
                })
            
            usage_id = ultimo_uso['id']
            app.logger.info(f"âœ… [TFT] Usando Ãºltimo juego ID: {usage_id}")
        
        # ==========================================
        # 6. VERIFICAR SI YA SE REPORTÃ“ ESTA FALLA
        # ==========================================
        cursor.execute("""
            SELECT id, reported_at
            FROM machinefailures
            WHERE qr_code_id = %s 
            AND machine_id = %s
            AND ABS(TIMESTAMPDIFF(MINUTE, reported_at, NOW())) < 5
            ORDER BY reported_at DESC
            LIMIT 1
        """, (qr_id, machine_id))
        
        falla_reciente = cursor.fetchone()
        
        if falla_reciente:
            app.logger.info(f"âš ï¸ [TFT] Falla ya reportada hace menos de 5 minutos (ID: {falla_reciente['id']})")
            return api_response(
                'W007',
                status='warning',
                http_status=200,  # 200 para no generar error en ESP32
                data={
                    'message': 'Falla ya reportada recientemente',
                    'failure_id': falla_reciente['id'],
                    'already_reported': True
                }
            )
        
        # ==========================================
        # 7. REGISTRAR LA FALLA EN MACHINEFAILURES
        # ==========================================
        cursor.execute("""
            INSERT INTO machinefailures 
            (qr_code_id, machine_id, machine_name, turnos_devueltos, notes, is_forced, forced_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            qr_id,
            machine_id,
            machine_name,
            turnos_devueltos,
            notes,
            0,  # is_forced = FALSE (reporte genuino desde TFT)
            None  # forced_by = NULL
        ))
        
        failure_id = cursor.lastrowid
        
        # ==========================================
        # 8. DEVOLVER EL TURNO (AUTOMÃTICAMENTE)
        # ==========================================
        cursor.execute("""
            UPDATE userturns 
            SET turns_remaining = turns_remaining + %s
            WHERE qr_code_id = %s
        """, (turnos_devueltos, qr_id))
        
        cursor.execute("""
            UPDATE qrcode 
            SET remainingTurns = remainingTurns + %s
            WHERE id = %s
        """, (turnos_devueltos, qr_id))
        
        # ==========================================
        # 9. OBTENER NUEVOS TURNOS
        # ==========================================
        cursor.execute("SELECT turns_remaining FROM userturns WHERE qr_code_id = %s", (qr_id,))
        nuevos_turnos = cursor.fetchone()['turns_remaining']
        
        connection.commit()
        
        app.logger.info(f"âœ… [TFT] Falla reportada â€” ID: {failure_id} | MÃ¡quina: {machine_name} ({machine_id}) | QR: {qr_code} | Turnos devueltos: {turnos_devueltos} | Turnos restantes: {nuevos_turnos}")

        _log_transaccion(
            tipo='falla_maquina',
            categoria='operacional',
            descripcion=f"Falla reportada desde ESP32/TFT en {machine_name} â€” turno devuelto",
            maquina_id=machine_id,
            maquina_nombre=machine_name,
            entidad='qr',
            entidad_id=qr_id,
            datos_extra={
                'failure_id': failure_id,
                'qr_code': qr_code,
                'usage_id': usage_id,
                'turnos_devueltos': turnos_devueltos,
                'turnos_restantes': nuevos_turnos,
                'is_forced': is_forced,
                'notes': notes,
                'origen': 'esp32_tft'
            }
        )

        # ==========================================
        # 10. RESPUESTA AL ESP32
        # ==========================================
        return api_response(
            'S012',
            status='success',
            data={
                'failure_id': failure_id,
                'qr_code': qr_code,
                'machine_id': machine_id,
                'usage_id': usage_id,
                'turnos_devueltos': turnos_devueltos,
                'turnos_restantes': nuevos_turnos,
                'message': 'Falla reportada y turno devuelto automÃ¡ticamente'
            }
        )
        
    except Exception as e:
        app.logger.error(f"âŒ [TFT] Error procesando reporte de falla: {e}", exc_info=True)
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500, data={
            'error': str(e),
            'message': 'Error interno del servidor al reportar falla'
        })
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/tft/machine-status/<machine_id>', methods=['GET'])
def tft_machine_status(machine_id):
    """Obtener estado de mÃ¡quina para pantalla TFT"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexiÃ³n'}), 500
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("""
            SELECT 
                m.id, m.name, m.status, m.type,
                l.name as location_name,
                COUNT(tu.id) as usos_hoy
            FROM machine m
            LEFT JOIN location l ON m.location_id = l.id
            LEFT JOIN turnusage tu ON tu.machineId = m.id AND DATE(tu.usedAt) = CURDATE()
            WHERE m.id = %s OR m.name = %s
            GROUP BY m.id, m.name, m.status, m.type, l.name
        """, (machine_id, machine_id))
        
        machine_data = cursor.fetchone()
        
        if not machine_data:
            return jsonify({
                'machine_id': machine_id,
                'machine_name': 'Desconocida',
                'status': 'offline',
                'type': 'arcade',
                'location': 'Sin ubicaciÃ³n',
                'usos_hoy': 0,
                'message': 'MÃ¡quina no registrada'
            }), 200
        
        # Determinar mensaje segÃºn estado
        status_messages = {
            'activa': 'Disponible para jugar',
            'mantenimiento': 'En mantenimiento',
            'inactiva': 'MÃ¡quina desactivada'
        }
        
        return jsonify({
            'machine_id': machine_data['id'],
            'machine_name': machine_data['name'],
            'status': machine_data['status'],
            'type': machine_data['type'],
            'location': machine_data['location_name'],
            'usos_hoy': machine_data['usos_hoy'],
            'message': status_messages.get(machine_data['status'], 'Estado desconocido'),
            'online': True,
            'timestamp': get_colombia_time().isoformat()
        })
        
    except Exception as e:
        app.logger.error(f"Error estado mÃ¡quina TFT: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/esp32/machine-technical/<int:machine_id>', methods=['GET'])
@handle_api_errors
def esp32_machine_technical(machine_id):
    """Obtener datos tÃ©cnicos de la mÃ¡quina para ESP32/TFT"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("""
            SELECT 
                COALESCE(mt.credits_virtual, 1) as credits_virtual,
                COALESCE(mt.credits_machine, 1) as credits_machine,
                COALESCE(mt.game_duration_seconds, 60) as game_duration_seconds,
                COALESCE(mt.reset_time_seconds, 5) as reset_time_seconds,  
                m.name as machine_name,
                COALESCE(l.name, 'Sin ubicaciÃ³n') as location_name,
                MAX(tu.usedAt) as last_play_time
            FROM machine m
            LEFT JOIN machinetechnical mt ON m.id = mt.machine_id
            LEFT JOIN location l ON m.location_id = l.id
            LEFT JOIN turnusage tu ON tu.machineId = m.id
            WHERE m.id = %s
            GROUP BY m.id, m.name, l.name, mt.credits_virtual, 
                     mt.credits_machine, mt.game_duration_seconds, mt.reset_time_seconds
        """, (machine_id,))
        
        tech_data = cursor.fetchone()
        
        if not tech_data:
            return api_response('M001', http_status=404)
        
        return api_response(
            'S011',
            status='success',
            data={
                'machine_name': tech_data['machine_name'],
                'location': tech_data['location_name'],
                'credits_virtual': tech_data['credits_virtual'],
                'credits_machine': tech_data['credits_machine'],
                'game_duration_seconds': tech_data['game_duration_seconds'],
                'reset_time_seconds': tech_data['reset_time_seconds'],  # âœ… NUEVO
                'last_play_time': tech_data['last_play_time'].isoformat() if tech_data['last_play_time'] else None,
                'machine_id': machine_id
            }
        )
        
    except Exception as e:
        app.logger.error(f"Error obteniendo datos tÃ©cnicos: {str(e)}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/esp32/machine-reset', methods=['POST'])
@handle_api_errors
def esp32_machine_reset():
    """
    Endpoint para registrar cuando una mÃ¡quina se reinicia 
    despuÃ©s de una devoluciÃ³n exitosa
    """
    connection = None
    cursor = None
    try:
        data = request.get_json()
        machine_id = data.get('machine_id')
        machine_name = data.get('machine_name')
        qr_code = data.get('qr_code')
        usage_id = data.get('usage_id')
        failure_id = data.get('failure_id')
        reset_time = data.get('reset_time_seconds', 5)
        
        app.logger.info(f"ðŸ”„ðŸ”„ðŸ”„ [REINICIO MÃQUINA] ðŸ”„ðŸ”„ðŸ”„")
        app.logger.info(f"   MÃ¡quina ID: {machine_id}")
        app.logger.info(f"   MÃ¡quina Nombre: {machine_name}")
        app.logger.info(f"   QR Code: {qr_code}")
        app.logger.info(f"   Usage ID: {usage_id}")
        app.logger.info(f"   Failure ID: {failure_id}")
        app.logger.info(f"   Tiempo de reinicio: {reset_time}s")
        app.logger.info(f"   Timestamp: {get_colombia_time().strftime('%Y-%m-%d %H:%M:%S')}")
        app.logger.info(f"ðŸ”„ðŸ”„ðŸ”„ ==================== ðŸ”„ðŸ”„ðŸ”„")
        
        return api_response(
            'S013',
            status='success',
            data={
                'message': 'Reinicio registrado',
                'machine_id': machine_id,
                'timestamp': get_colombia_time().isoformat()
            }
        )
        
    except Exception as e:
        app.logger.error(f"âŒ Error registrando reinicio de mÃ¡quina: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# ==================== RUTAS DE REDIRECCIÃ“N ====================

@app.route('/admin/usuarios/lista')
def mostrar_lista_usuarios():
    """Redirigir a la gestiÃ³n de usuarios"""
    return redirect(url_for('mostrar_gestion_usuarios'))

@app.route('/admin/paquetes/lista')
def mostrar_lista_paquetes():
    """Redirigir a la gestiÃ³n de paquetes"""
    return redirect(url_for('admin.mostrar_gestion_paquetes'))

@app.route('/admin/locales/listalocales')
def mostrar_lista_locales():
    """Redirigir a la gestiÃ³n de locales"""
    return redirect(url_for('admin.mostrar_gestion_locales'))

@app.route('/admin/maquinas/inventario')
def mostrar_inventario_maquinas():
    """Redirigir a la gestiÃ³n de mÃ¡quinas"""
    return redirect(url_for('admin.mostrar_gestion_maquinas'))

# ==================== APIS PARA CONTADORES GLOBALES ====================

# counters routes â†’ blueprints/counters/routes.py

# ==================== APIS PARA ESTADÃSTICAS HISTÃ“RICAS ====================

@app.route('/api/estadisticas/rango-fechas', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
def obtener_estadisticas_rango_fechas():
    """Obtener estadÃ­sticas por rango de fechas"""
    connection = None
    cursor = None
    try:
        fecha_inicio = request.args.get('fecha_inicio', get_colombia_time().strftime('%Y-%m-%d'))
        fecha_fin = request.args.get('fecha_fin', get_colombia_time().strftime('%Y-%m-%d'))
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        # EstadÃ­sticas por dÃ­a en el rango
        cursor.execute("""
            SELECT 
                DATE(qh.fecha_hora) as fecha,
                COUNT(DISTINCT qh.qr_code) as total_escaneados,
                COUNT(DISTINCT CASE WHEN qr.turnPackageId IS NOT NULL AND qr.turnPackageId != 1 THEN qh.qr_code END) as vendidos,
                COALESCE(SUM(CASE WHEN qr.turnPackageId IS NOT NULL AND qr.turnPackageId != 1 THEN tp.price END), 0) as valor_ventas,
                COUNT(tu.id) as turnos_utilizados
            FROM qrhistory qh
            LEFT JOIN qrcode qr ON qr.code = qh.qr_code
            LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
            LEFT JOIN turnusage tu ON DATE(tu.usedAt) = DATE(qh.fecha_hora)
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
            GROUP BY DATE(qh.fecha_hora)
            ORDER BY fecha DESC
        """, (fecha_inicio, fecha_fin))
        
        estadisticas = cursor.fetchall()
        
        # Totales del rango
        cursor.execute("""
            SELECT 
                COUNT(DISTINCT qh.qr_code) as total_escaneados,
                COUNT(DISTINCT CASE WHEN qr.turnPackageId IS NOT NULL AND qr.turnPackageId != 1 THEN qh.qr_code END) as total_vendidos,
                COALESCE(SUM(CASE WHEN qr.turnPackageId IS NOT NULL AND qr.turnPackageId != 1 THEN tp.price END), 0) as total_valor_ventas,
                COUNT(DISTINCT tu.id) as total_turnos_utilizados
            FROM qrhistory qh
            LEFT JOIN qrcode qr ON qr.code = qh.qr_code
            LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
            LEFT JOIN turnusage tu ON DATE(tu.usedAt) = DATE(qh.fecha_hora)
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
        """, (fecha_inicio, fecha_fin))
        
        totales = cursor.fetchone()
        
        # MÃ¡quinas mÃ¡s utilizadas en el rango
        cursor.execute("""
            SELECT 
                m.name as maquina_nombre,
                COUNT(tu.id) as turnos_utilizados
            FROM turnusage tu
            JOIN machine m ON tu.machineId = m.id
            WHERE DATE(tu.usedAt) BETWEEN %s AND %s
            GROUP BY m.id, m.name
            ORDER BY turnos_utilizados DESC
            LIMIT 10
        """, (fecha_inicio, fecha_fin))
        
        maquinas_populares = cursor.fetchall()
        
        # Paquetes mÃ¡s vendidos en el rango
        cursor.execute("""
            SELECT 
                tp.name as paquete_nombre,
                COUNT(DISTINCT qh.qr_code) as veces_vendido,
                SUM(tp.price) as valor_total
            FROM qrhistory qh
            JOIN qrcode qr ON qr.code = qh.qr_code
            JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
            AND qr.turnPackageId IS NOT NULL
            AND qr.turnPackageId != 1
            GROUP BY tp.id, tp.name
            ORDER BY veces_vendido DESC
            LIMIT 10
        """, (fecha_inicio, fecha_fin))
        
        paquetes_populares = cursor.fetchall()
        
        app.logger.info(f"EstadÃ­sticas rango {fecha_inicio} a {fecha_fin}: {totales['total_vendidos'] or 0} vendidos")
        
        return jsonify({
            'rango': {
                'fecha_inicio': fecha_inicio,
                'fecha_fin': fecha_fin
            },
            'estadisticas_por_dia': estadisticas,
            'totales': {
                'total_escaneados': totales['total_escaneados'] or 0,
                'total_vendidos': totales['total_vendidos'] or 0,
                'total_valor_ventas': float(totales['total_valor_ventas'] or 0),
                'total_turnos_utilizados': totales['total_turnos_utilizados'] or 0
            },
            'maquinas_populares': maquinas_populares,
            'paquetes_populares': paquetes_populares,
            'timestamp': get_colombia_time().isoformat()
        })
        
    except Exception as e:
        app.logger.error(f"Error obteniendo estadÃ­sticas por rango: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/dashboard/resumen', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
def obtener_resumen_dashboard():
    """Obtener resumen para dashboard/panel de control"""
    connection = None
    cursor = None
    try:
        fecha_hoy = get_colombia_time().strftime('%Y-%m-%d')
        fecha_ayer = (get_colombia_time() - timedelta(days=1)).strftime('%Y-%m-%d')
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        # EstadÃ­sticas de hoy
        cursor.execute("""
            SELECT 
                COUNT(DISTINCT CASE WHEN qr.turnPackageId IS NOT NULL AND qr.turnPackageId != 1 THEN qh.qr_code END) as vendidos_hoy,
                COALESCE(SUM(CASE WHEN qr.turnPackageId IS NOT NULL AND qr.turnPackageId != 1 THEN tp.price END), 0) as valor_hoy
            FROM qrhistory qh
            LEFT JOIN qrcode qr ON qr.code = qh.qr_code
            LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) = %s
        """, (fecha_hoy,))
        
        hoy = cursor.fetchone()
        
        # Turnos utilizados hoy
        cursor.execute("SELECT COUNT(*) as turnos_hoy FROM turnusage WHERE DATE(usedAt) = %s", (fecha_hoy,))
        turnos_hoy = cursor.fetchone()
        
        # EstadÃ­sticas de ayer
        cursor.execute("""
            SELECT 
                COUNT(DISTINCT CASE WHEN qr.turnPackageId IS NOT NULL AND qr.turnPackageId != 1 THEN qh.qr_code END) as vendidos_ayer,
                COALESCE(SUM(CASE WHEN qr.turnPackageId IS NOT NULL AND qr.turnPackageId != 1 THEN tp.price END), 0) as valor_ayer
            FROM qrhistory qh
            LEFT JOIN qrcode qr ON qr.code = qh.qr_code
            LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) = %s
        """, (fecha_ayer,))
        
        ayer = cursor.fetchone()
        
        # Turnos utilizados ayer
        cursor.execute("SELECT COUNT(*) as turnos_ayer FROM turnusage WHERE DATE(usedAt) = %s", (fecha_ayer,))
        turnos_ayer = cursor.fetchone()
        
        # MÃ¡quinas activas/inactivas
        cursor.execute("""
            SELECT 
                COUNT(CASE WHEN status = 'activa' THEN 1 END) as maquinas_activas,
                COUNT(CASE WHEN status = 'mantenimiento' THEN 1 END) as maquinas_mantenimiento,
                COUNT(CASE WHEN status = 'inactiva' THEN 1 END) as maquinas_inactivas,
                COUNT(*) as total_maquinas
            FROM machine
        """)
        
        maquinas = cursor.fetchone()
        
        # Reportes pendientes
        cursor.execute("""
            SELECT COUNT(*) as reportes_pendientes
            FROM errorreport
            WHERE isResolved = FALSE
        """)
        
        reportes = cursor.fetchone()
        
        # Ãšltimas ventas (5)
        cursor.execute("""
            SELECT 
                qh.qr_code,
                qh.user_name,
                qh.fecha_hora,
                tp.name as paquete_nombre,
                tp.price as precio
            FROM qrhistory qh
            JOIN qrcode qr ON qr.code = qh.qr_code
            JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE qr.turnPackageId IS NOT NULL
            AND qr.turnPackageId != 1
            ORDER BY qh.fecha_hora DESC
            LIMIT 5
        """)
        
        ultimas_ventas = cursor.fetchall()
        
        # Formatear fechas
        for venta in ultimas_ventas:
            if venta['fecha_hora']:
                try:
                    fecha_colombia = parse_db_datetime(venta['fecha_hora'])
                    venta['fecha_hora'] = fecha_colombia.strftime('%H:%M')
                    venta['fecha_completa'] = fecha_colombia.strftime('%Y-%m-%d %H:%M:%S')
                except Exception as e:
                    app.logger.warning(f"Error formateando fecha: {e}")
                    venta['fecha_hora'] = str(venta['fecha_hora'])
                    venta['fecha_completa'] = str(venta['fecha_hora'])
        
        app.logger.info(f"Dashboard: {hoy['vendidos_hoy'] or 0} vendidos hoy")
        
        return jsonify({
            'hoy': {
                'fecha': fecha_hoy,
                'vendidos': hoy['vendidos_hoy'] or 0,
                'valor': float(hoy['valor_hoy'] or 0),
                'turnos': turnos_hoy['turnos_hoy'] or 0
            },
            'ayer': {
                'fecha': fecha_ayer,
                'vendidos': ayer['vendidos_ayer'] or 0,
                'valor': float(ayer['valor_ayer'] or 0),
                'turnos': turnos_ayer['turnos_ayer'] or 0
            },
            'maquinas': {
                'activas': maquinas['maquinas_activas'] or 0,
                'mantenimiento': maquinas['maquinas_mantenimiento'] or 0,
                'inactivas': maquinas['maquinas_inactivas'] or 0,
                'total': maquinas['total_maquinas'] or 0
            },
            'reportes': {
                'pendientes': reportes['reportes_pendientes'] or 0
            },
            'ultimas_ventas': ultimas_ventas,
            'timestamp': get_colombia_time().isoformat()
        })
        
    except Exception as e:
        app.logger.error(f"Error obteniendo resumen dashboard: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/mis-permisos', methods=['GET'])
@handle_api_errors
def obtener_mis_permisos():
    """Obtener permisos del usuario actual"""
    if not session.get('logged_in'):
        return api_response('E003', http_status=401)
    permisos = get_user_permissions()
    return jsonify({
        'role': session.get('user_role'),
        'permisos': permisos,
        'es_admin': 'admin_panel' in permisos
    })

# ==================== FUNCIÃ“N PARA ACTUALIZAR CONTADORES DIARIOS ====================

def actualizar_contador_diario(fecha=None):
    """Actualizar contador diario - SOLO VENTAS REALES"""
    if fecha is None:
        fecha = get_colombia_time().strftime('%Y-%m-%d')
    
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return False
            
        cursor = get_db_cursor(connection)
        
        # Crear tabla si no existe
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ContadorDiario (
                id INT AUTO_INCREMENT PRIMARY KEY,
                fecha DATE NOT NULL UNIQUE,
                qr_vendidos INT DEFAULT 0,
                valor_ventas DECIMAL(10, 2) DEFAULT 0,
                qr_escaneados INT DEFAULT 0,
                turnos_utilizados INT DEFAULT 0,
                fallas_reportadas INT DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_fecha (fecha)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        
        # Solo contar ventas REALES
        cursor.execute("""
            INSERT INTO ContadorDiario (fecha, qr_vendidos, valor_ventas, qr_escaneados, turnos_utilizados, fallas_reportadas)
            SELECT 
                %s as fecha,
                COUNT(DISTINCT CASE WHEN qr.turnPackageId IS NOT NULL AND qr.turnPackageId != 1 
                          AND qh.es_venta_real = TRUE  -- SOLO VENTAS REALES
                          THEN qh.qr_code END) as qr_vendidos,
                COALESCE(SUM(CASE WHEN qr.turnPackageId IS NOT NULL AND qr.turnPackageId != 1 
                           AND qh.es_venta_real = TRUE  -- SOLO VENTAS REALES
                           THEN tp.price END), 0) as valor_ventas,
                COUNT(DISTINCT qh.qr_code) as qr_escaneados,
                COUNT(DISTINCT tu.id) as turnos_utilizados,
                COUNT(DISTINCT mf.id) as fallas_reportadas
            FROM qrhistory qh
            LEFT JOIN qrcode qr ON qr.code = qh.qr_code
            LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
            LEFT JOIN turnusage tu ON DATE(tu.usedAt) = %s
            LEFT JOIN machinefailures mf ON DATE(mf.reported_at) = %s
            WHERE DATE(qh.fecha_hora) = %s
            ON DUPLICATE KEY UPDATE
                qr_vendidos = VALUES(qr_vendidos),
                valor_ventas = VALUES(valor_ventas),
                qr_escaneados = VALUES(qr_escaneados),
                turnos_utilizados = VALUES(turnos_utilizados),
                fallas_reportadas = VALUES(fallas_reportadas),
                updated_at = NOW()
        """, (fecha, fecha, fecha, fecha))
        
        connection.commit()
        app.logger.info(f"Contador diario actualizado para {fecha}")
        return True
        
    except Exception as e:
        app.logger.error(f"Error actualizando contador diario: {e}")
        if connection:
            connection.rollback()
        return False
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# ==================== APIS PARA PROPIETARIOS ====================

@app.route('/api/propietarios', methods=['GET'])
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
        
        # Formatear respuesta
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
        app.logger.error(f"Error obteniendo propietarios: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/propietarios/<int:propietario_id>', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_propietario(propietario_id):
    """Obtener un propietario especÃ­fico"""
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
        
        # Obtener mÃ¡quinas asociadas
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
        app.logger.error(f"Error obteniendo propietario: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/propietarios', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
@validate_required_fields(['nombre'])
def crear_propietario():
    """Crear un nuevo propietario"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        nombre = data['nombre']
        telefono = data.get('telefono', '')
        email = data.get('email', '')
        notas = data.get('notas', '')
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        # Verificar si ya existe
        cursor.execute("SELECT id FROM Propietarios WHERE nombre = %s", (nombre,))
        if cursor.fetchone():
            return api_response('E007', http_status=400, data={'message': 'Propietario ya existe'})
        
        # Crear propietario
        cursor.execute("""
            INSERT INTO Propietarios (nombre, telefono, email, notas)
            VALUES (%s, %s, %s, %s)
        """, (nombre, telefono, email, notas))
        
        connection.commit()
        
        app.logger.info(f"Propietario creado: {nombre}")
        
        return api_response(
            'S002',
            status='success',
            data={'propietario_id': cursor.lastrowid}
        )
        
    except Exception as e:
        app.logger.error(f"Error creando propietario: {e}")
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/propietarios/<int:propietario_id>', methods=['PUT'])
@handle_api_errors
@require_login(['admin'])
@validate_required_fields(['nombre'])
def actualizar_propietario(propietario_id):
    """Actualizar un propietario existente"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        nombre = data['nombre']
        telefono = data.get('telefono', '')
        email = data.get('email', '')
        notas = data.get('notas', '')
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        # Verificar que existe
        cursor.execute("SELECT id FROM Propietarios WHERE id = %s", (propietario_id,))
        if not cursor.fetchone():
            return api_response('E002', http_status=404, data={'propietario_id': propietario_id})
        
        # Verificar nombre duplicado
        cursor.execute("SELECT id FROM Propietarios WHERE nombre = %s AND id != %s", (nombre, propietario_id))
        if cursor.fetchone():
            return api_response('E007', http_status=400, data={'message': 'Nombre de propietario ya existe'})
        
        # Actualizar
        cursor.execute("""
            UPDATE Propietarios 
            SET nombre = %s, telefono = %s, email = %s, notas = %s
            WHERE id = %s
        """, (nombre, telefono, email, notas, propietario_id))
        
        connection.commit()
        
        app.logger.info(f"Propietario actualizado: {nombre} (ID: {propietario_id})")
        
        return api_response('S003', status='success')
        
    except Exception as e:
        app.logger.error(f"Error actualizando propietario: {e}")
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/propietarios/<int:propietario_id>', methods=['DELETE'])
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
        
        # Verificar que existe
        cursor.execute("SELECT nombre FROM Propietarios WHERE id = %s", (propietario_id,))
        propietario = cursor.fetchone()
        if not propietario:
            return api_response('E002', http_status=404, data={'propietario_id': propietario_id})
        
        # Verificar si tiene mÃ¡quinas asociadas
        cursor.execute("SELECT COUNT(*) as count FROM MaquinaPropietario WHERE propietario_id = %s", (propietario_id,))
        maquinas_count = cursor.fetchone()['count']
        
        if maquinas_count > 0:
            return api_response(
                'W006',
                status='warning',
                http_status=400,
                data={
                    'message': f'Propietario tiene {maquinas_count} mÃ¡quinas asociadas',
                    'maquinas_count': maquinas_count
                }
            )
        
        # Eliminar
        cursor.execute("DELETE FROM Propietarios WHERE id = %s", (propietario_id,))
        connection.commit()
        
        app.logger.info(f"Propietario eliminado: {propietario['nombre']} (ID: {propietario_id})")
        
        return api_response('S004', status='success')
        
    except Exception as e:
        app.logger.error(f"Error eliminando propietario: {e}")
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# ==================== APIS PARA REPORTES DE MÃQUINAS ====================

@app.route('/api/maquinas/<int:maquina_id>/reportes', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_reportes_maquina(maquina_id):
    """Obtener reportes de fallas de una mÃ¡quina especÃ­fica"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        # Verificar que la mÃ¡quina existe
        cursor.execute("SELECT name FROM machine WHERE id = %s", (maquina_id,))
        maquina = cursor.fetchone()
        if not maquina:
            return api_response('M001', http_status=404, data={'machine_id': maquina_id})
        
        # Obtener reportes de la mÃ¡quina
        cursor.execute("""
            SELECT 
                er.id,
                er.machineId,
                er.userId,
                er.description,
                er.reportedAt,
                er.isResolved,
                u.name as user_name
            FROM errorreport er
            JOIN users u ON er.userId = u.id
            WHERE er.machineId = %s
            ORDER BY er.reportedAt DESC
        """, (maquina_id,))
        
        reportes = cursor.fetchall()
        
        # Formatear fechas
        for reporte in reportes:
            if reporte['reportedAt']:
                fecha_colombia = parse_db_datetime(reporte['reportedAt'])
                reporte['reportedAt'] = fecha_colombia.strftime('%Y-%m-%d %H:%M:%S')
        
        return jsonify({
            'maquina_id': maquina_id,
            'maquina_nombre': maquina['name'],
            'reportes': reportes,
            'total': len(reportes)
        })
        
    except Exception as e:
        app.logger.error(f"Error obteniendo reportes de mÃ¡quina: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/maquinas/<int:maquina_id>/estadisticas', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
def obtener_estadisticas_maquina(maquina_id):
    """Obtener estadÃ­sticas de una mÃ¡quina"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        # Verificar que la mÃ¡quina existe
        cursor.execute("SELECT name, status FROM machine WHERE id = %s", (maquina_id,))
        maquina = cursor.fetchone()
        if not maquina:
            return api_response('M001', http_status=404, data={'machine_id': maquina_id})
        
        # EstadÃ­sticas de uso
        cursor.execute("""
            SELECT 
                COUNT(*) as total_usos,
                COUNT(DISTINCT DATE(usedAt)) as dias_con_usos,
                MIN(usedAt) as primer_uso,
                MAX(usedAt) as ultimo_uso
            FROM turnusage
            WHERE machineId = %s
        """, (maquina_id,))
        
        uso_stats = cursor.fetchone()
        
        # Usos por dÃ­a (Ãºltimos 30 dÃ­as)
        cursor.execute("""
            SELECT 
                DATE(usedAt) as fecha,
                COUNT(*) as usos
            FROM turnusage
            WHERE machineId = %s 
            AND usedAt >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            GROUP BY DATE(usedAt)
            ORDER BY fecha DESC
        """, (maquina_id,))
        
        usos_por_dia = cursor.fetchall()
        
        # Reportes de fallas
        cursor.execute("""
            SELECT 
                COUNT(*) as total_reportes,
                COUNT(CASE WHEN isResolved = TRUE THEN 1 END) as reportes_resueltos,
                COUNT(CASE WHEN isResolved = FALSE THEN 1 END) as reportes_pendientes
            FROM errorreport
            WHERE machineId = %s
        """, (maquina_id,))
        
        reportes_stats = cursor.fetchone()
        
        # Ãšltimos reportes (5)
        cursor.execute("""
            SELECT 
                er.description,
                er.reportedAt,
                er.isResolved,
                u.name as reportado_por
            FROM errorreport er
            JOIN users u ON er.userId = u.id
            WHERE er.machineId = %s
            ORDER BY er.reportedAt DESC
            LIMIT 5
        """, (maquina_id,))
        
        ultimos_reportes = cursor.fetchall()
        
        # Formatear fechas
        for reporte in ultimos_reportes:
            if reporte['reportedAt']:
                fecha_colombia = parse_db_datetime(reporte['reportedAt'])
                reporte['reportedAt'] = fecha_colombia.strftime('%Y-%m-%d %H:%M:%S')
        
        return jsonify({
            'maquina_id': maquina_id,
            'maquina_nombre': maquina['name'],
            'estado': maquina['status'],
            'estadisticas': {
                'uso': {
                    'total_usos': uso_stats['total_usos'] or 0,
                    'dias_con_usos': uso_stats['dias_con_usos'] or 0,
                    'primer_uso': uso_stats['primer_uso'].isoformat() if uso_stats['primer_uso'] else None,
                    'ultimo_uso': uso_stats['ultimo_uso'].isoformat() if uso_stats['ultimo_uso'] else None
                },
                'reportes': {
                    'total': reportes_stats['total_reportes'] or 0,
                    'resueltos': reportes_stats['reportes_resueltos'] or 0,
                    'pendientes': reportes_stats['reportes_pendientes'] or 0
                }
            },
            'usos_por_dia': usos_por_dia,
            'ultimos_reportes': ultimos_reportes,
            'timestamp': get_colombia_time().isoformat()
        })
        
    except Exception as e:
        app.logger.error(f"Error obteniendo estadÃ­sticas de mÃ¡quina: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# ==================== APIS PARA GESTIÃ“N DE ROLES ====================

@app.route('/api/roles/sistema', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_roles_sistema():
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
                import json
                rol['permisos'] = json.loads(rol['permisos'])
        return jsonify({'roles': roles, 'total_roles': len(roles), 'timestamp': get_colombia_time().isoformat()})
    except Exception as e:
        app.logger.error(f"Error obteniendo roles: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor: cursor.close()
        if connection: connection.close()

@app.route('/api/roles/agregar-automatico', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
def agregar_nuevo_rol_automatico():
    connection = None
    cursor = None
    try:
        data = request.get_json()
        nuevo_rol = data.get('nuevo_rol', '').strip().lower()
        nombre = data.get('nombre', nuevo_rol.capitalize().replace('_', ' '))
        descripcion = data.get('descripcion', '')
        nivel_acceso = data.get('nivel_acceso', 'bajo')
        permisos = data.get('permisos', [])
        color = data.get('color', 'gray')
        icono = data.get('icono', 'user')

        if not nuevo_rol:
            return api_response('E005', http_status=400, data={'message': 'Nombre del rol requerido'})
        if not re.match(r'^[a-z_]+$', nuevo_rol):
            return api_response('E005', http_status=400, data={'message': 'Solo letras minÃºsculas y guiones bajos'})
        if len(nuevo_rol) > 50:
            return api_response('E005', http_status=400, data={'message': 'MÃ¡ximo 50 caracteres'})

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
        app.logger.info(f"Rol creado: {nuevo_rol}")

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
        app.logger.error(f"Error creando rol: {e}")
        if connection: connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor: cursor.close()
        if connection: connection.close()

@app.route('/api/roles/<rol_id>', methods=['DELETE'])
@handle_api_errors
@require_login(['admin'])
def eliminar_rol(rol_id):
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
            return api_response('E005', http_status=400, data={'message': f'Hay {total} usuarios con este rol. ReasÃ­gnalos primero.'})

        cursor.execute("DELETE FROM roles WHERE id = %s", (rol_id,))
        connection.commit()
        return api_response('S004', status='success')
    except Exception as e:
        app.logger.error(f"Error eliminando rol: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor: cursor.close()
        if connection: connection.close()
        
# rutas de socios migradas a blueprints/socios/routes.py

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



