from flask import Flask, request, jsonify, render_template, redirect, url_for, session
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

# ==================== CONFIGURACIÓN DE ZONA HORARIA ====================

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

# ==================== CONFIGURACIÓN SENTRY ====================
sentry_sdk.init(
    dsn="https://5fc281c2ace4860969f2f1f6fa10039d@o4510071013310464.ingest.us.sentry.io/4510071047454720",
    integrations=[FlaskIntegration()],
    traces_sample_rate=1.0,
    send_default_pii=True,
    environment="development"
)

# Configuración del logger
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(
    __name__,
    static_folder=os.path.join(BASE_DIR, 'static'),
    template_folder=os.path.join(BASE_DIR, 'templates')
)
app.secret_key = 'maquinasmedellin_secret_key_2025'
CORS(app)

# Configurar logging
if not os.path.exists('logs'):
    os.makedirs('logs')

file_handler = RotatingFileHandler('logs/maquinas.log', maxBytes=10240, backupCount=10)
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
))
file_handler.setLevel(logging.INFO)
app.logger.addHandler(file_handler)
app.logger.setLevel(logging.INFO)
app.logger.info('Iniciando aplicación Máquinas Medellín')

# ==================== CLASE DE SERVICIO DE MENSAJES ====================

class MessageService:
    """Servicio para gestionar mensajes desde la base de datos"""
    _cache = {}
    
    @classmethod
    @lru_cache(maxsize=128)
    def get_message(cls, message_code: str, language_code: str = 'es', **kwargs) -> dict:
        """Obtiene un mensaje de la base de datos y aplica formato"""
        try:
            # Cache key
            cache_key = f"{message_code}_{language_code}"
            
            # Verificar cache primero
            if cache_key in cls._cache:
                message_data = cls._cache[cache_key]
            else:
                # Conexión a la base de datos
                connection = cls._get_connection()
                if not connection:
                    return cls._get_default_message(message_code)
                
                cursor = connection.cursor(dictionary=True)
                
                # Buscar mensaje
                query = """
                    SELECT message_code, message_type, message_text, language_code
                    FROM system_messages 
                    WHERE message_code = %s AND language_code = %s
                """
                cursor.execute(query, (message_code, language_code))
                message = cursor.fetchone()
                
                # Fallback a español 
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
        """Obtiene conexión a la base de datos"""
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
            'E005': {'code': 'E005', 'type': 'error', 'text': 'Parámetros inválidos'},
            'E006': {'code': 'E006', 'type': 'error', 'text': 'Error de conexión a la base de datos'},
            'A001': {'code': 'A001', 'type': 'error', 'text': 'Credenciales inválidas'},
            'S001': {'code': 'S001', 'type': 'success', 'text': 'Operación exitosa'},
        }
        
        message = default_messages.get(message_code, {
            'code': message_code,
            'type': 'error',
            'text': f'Mensaje no configurado: {message_code}'
        })
        
        message['formatted'] = message['text']
        return message

# ==================== DECORADORES Y UTILIDADES ====================

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
    Decorador para requerir autenticación y roles específicos
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not session.get('logged_in'):
                return api_response('A004', http_status=401)
            
            if roles and session.get('user_role') not in roles:
                return api_response('E004', http_status=403)
            
            return func(*args, **kwargs)
        return wrapper
    return decorator

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


# ==================== CONFIGURACIÓN DEL POOL DE CONEXIONES ====================

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

    app.logger.info("🔧 Intentando crear pool de conexiones...")
    app.logger.info(f"   Host: {db_config['host']}")
    app.logger.info(f"   User: {db_config['user']}")
    app.logger.info(f"   Database: {db_config['database']}")
    app.logger.info(f"   Port: {db_config['port']}")
    
    # Probar conexión simple primero
    test_conn = mysql.connector.connect(
         host=os.getenv("DB_HOST", "mysql"),
    user=os.getenv("DB_USER", "myuser"),
    password=os.getenv("DB_PASSWORD", "mypassword"),
    database=os.getenv("DB_NAME", "maquinasmedellin"),
    port=3306,
    auth_plugin="mysql_native_password"
)
    app.logger.info("✅ Conexión simple exitosa")
    test_conn.close()
    
    # Ahora intentar el pool
    connection_pool = pooling.MySQLConnectionPool(**db_config)
    app.logger.info("✅ Pool de conexiones creado exitosamente")
    
except mysql.connector.Error as e:
    app.logger.error(f"❌ Error MySQL específico: {e}")
    app.logger.error(f"   Error number: {e.errno}")
    app.logger.error(f"   SQL state: {e.sqlstate}")
    connection_pool = None
except Exception as e:
    app.logger.error(f"❌ Error general creando pool: {e}")
    import traceback
    traceback.print_exc()
    connection_pool = None

# Función para obtener conexión CON zona horaria
def get_db_connection():
    try:
        # Conexión directa sin pool para debugging
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
        app.logger.error(f"❌ Error obteniendo conexión: {e}")
        import traceback
        traceback.print_exc()
        return None

# Función para obtener cursor
def get_db_cursor(connection):
    try:
        cursor = connection.cursor(dictionary=True)
        return cursor
    except Exception as e:
        app.logger.error(f"❌ Error obteniendo cursor: {e}")
        return None

# ==================== RUTAS PRINCIPALES ====================

@app.route('/')
def mostrar_login():
    session.clear()
    return render_template('login.html')

@app.route('/login', methods=['POST'])
@handle_api_errors
def procesar_login():
    """Procesa el login del usuario"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        codigo = data.get('codigo')

        if not codigo:
            return jsonify({
                'valido': False,
                'error': 'Código requerido'
            }), 400

        connection = get_db_connection()
        if not connection:
            return jsonify({
                'valido': False,
                'error': 'Error de conexión a BD'
            }), 500

        cursor = get_db_cursor(connection)
        cursor.execute("SELECT * FROM users WHERE password = %s", (codigo,))
        usuario = cursor.fetchone()

        if usuario:
            session['user_id'] = usuario['id']
            session['user_name'] = usuario['name']
            session['user_role'] = usuario['role']
            session['user_local'] = usuario.get('local', 'El Mekatiadero')
            session['logged_in'] = True
            
            # Asegurar que la sesión se guarde
            session.modified = True
            
            app.logger.info(f"✅ Usuario {usuario['name']} inició sesión")
    
            # MODIFICACIÓN: Redirigir socios directamente a su interfaz
            return jsonify({
                'valido': True,
                'nombre': usuario.get("name", "Usuario"),
                'role': usuario.get("role", "Cajero"),
                'local': usuario.get("local", "El Mekatiadero"),
                'user_id': usuario['id'],
                # Agregar esta propiedad para el frontend
                'redirect_to': 'socios' if usuario.get("role") == 'socio' else None
            })
        else:
            return jsonify({
                'valido': False,
                'error': 'Código inválido'
            }), 401

    except Exception as e:
        app.logger.error(f"Error en login: {e}")
        return jsonify({
            'valido': False,
            'error': f'Error interno: {str(e)}'
        }), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()
            
@app.route('/test-db')
def test_db():
    """Ruta para probar conexión a BD"""
    try:
        connection = get_db_connection()
        if connection:
            cursor = get_db_cursor(connection)
            cursor.execute("SELECT COUNT(*) as count FROM users")
            resultado = cursor.fetchone()
            cursor.close()
            connection.close()
            return f"✅ Conexión exitosa. Usuarios en BD: {resultado['count']}"
        else:
            return "❌ No se pudo conectar a la BD"
    except Exception as e:
        return f"❌ Error: {str(e)}"

@app.route('/local')
def mostrar_local():
    if not session.get('logged_in'):
        return redirect(url_for('mostrar_login'))
    
    hora_colombia = get_colombia_time()
    
    return render_template('local.html',
                           nombre_usuario=session.get('user_name', 'Usuario'),
                           local_usuario=session.get('user_local', 'El Mekatiadero'),
                           hora_actual=hora_colombia.strftime('%H:%M:%S'),
                           fecha_actual=hora_colombia.strftime('%Y-%m-%d'))

@app.route('/package')
def mostrar_package():
    if not session.get('logged_in'):
        return redirect(url_for('mostrar_login'))
    return render_template('package.html',
                           nombre_usuario=session.get('user_name', 'Usuario'),
                           local_usuario=session.get('user_local', 'El Mekatiadero'))

@app.route('/package/failure')
def mostrar_package_failure():
    if not session.get('logged_in'):
        return redirect(url_for('mostrar_login'))
    return render_template('packfailure.html',
                           nombre_usuario=session.get('user_name', 'Usuario'),
                           local_usuario=session.get('user_local', 'El Mekatiadero'))

@app.route('/machinereport')
def mostrar_machine_report():
    if not session.get('logged_in'):
        return redirect(url_for('mostrar_login'))
    return render_template('machinereport.html',
                           nombre_usuario=session.get('user_name', 'Usuario'),
                           local_usuario=session.get('user_local', 'El Mekatiadero'))

@app.route('/sales')
def mostrar_sales():
    if not session.get('logged_in'):
        return redirect(url_for('mostrar_login'))
    return render_template('sales.html',
                           nombre_usuario=session.get('user_name', 'Usuario'),
                           local_usuario=session.get('user_local', 'El Mekatiadero'))

@app.route('/logout')
def logout():
    usuario = session.get('user_name', 'Usuario')
    session.clear()
    app.logger.info(f"Usuario {usuario} cerró sesión")
    return redirect(url_for('mostrar_login'))

@app.route('/Login.html')
def redirect_login():
    return redirect('/')

# ==================== APIS PARA QR Y PAQUETES ====================

def generar_codigo_qr():
    """Generar código QR con formato QR0001, QR0002, etc. usando contador global con reinicio en 9999"""
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
            # Si no existe el contador, crearlo
            cursor.execute("""
                INSERT INTO globalcounter (counter_type, counter_value, description) 
                VALUES ('QR_CODE', 1, 'Contador para códigos QR (formato QR0001, QR0002, etc.)')
            """)
            nuevo_numero = 1
        else:
            # Incrementar el contador
            nuevo_numero = resultado['counter_value'] + 1
            
            # Reiniciar si llega a 9999
            if nuevo_numero > 9999:
                nuevo_numero = 1
                app.logger.warning("Contador QR reiniciado a 1 (llegó al límite de 9999)")
            
            cursor.execute("""
                UPDATE globalcounter 
                SET counter_value = %s 
                WHERE counter_type = 'QR_CODE'
            """, (nuevo_numero,))
        
        # Formatear con 4 dígitos (reinicia en 1 después de 9999)
        nuevo_codigo = f"QR{nuevo_numero:04d}"  
        
        # Obtener información del usuario actual para el historial
        user_id = session.get('user_id')
        user_name = session.get('user_name', 'Sistema')
        local = session.get('user_local', 'El Mekatiadero')
        hora_colombia = get_colombia_time()
        fecha_hora_str = format_datetime_for_db(hora_colombia)
        
        # Insertar en la tabla qrcode
        cursor.execute("""
            INSERT INTO qrcode (code, remainingTurns, isActive, turnPackageId, qr_name)
            VALUES (%s, %s, %s, %s, %s)
        """, (nuevo_codigo, 0, 1, 1, ''))
        
        # Registrar automáticamente en el historial
        cursor.execute("""
            INSERT INTO qrhistory (qr_code, user_id, user_name, local, fecha_hora, qr_name)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (nuevo_codigo, user_id, user_name, local, fecha_hora_str, ''))
        
        connection.commit()
        
        app.logger.info(f"Generado código QR: {nuevo_codigo} (contador: {nuevo_numero}) por {user_name}")
        
        return nuevo_codigo
        
    except Exception as e:
        app.logger.error(f"Error generando código QR: {e}")
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
        
        # Llamar directamente a la función
        codigos = generar_codigos_qr_lote(cantidad, nombre)
        
        if not codigos:
            return jsonify({
                'error': 'La función retornó lista vacía',
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
    
    # función para generar múltiples QR con 4 cifras
def generar_codigos_qr_lote(cantidad_qr, nombre=""):
    """Generar múltiples códigos QR con 4 cifras usando contador global con manejo de reinicio"""
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
            # Si no existe el contador, crearlo
            cursor.execute("""
                INSERT INTO globalcounter (counter_type, counter_value, description) 
                VALUES ('QR_CODE', %s, 'Contador para códigos QR')
            """, (cantidad_qr,))
            numero_inicial = 1
            numero_final = cantidad_qr
        else:
            # Tomar el valor actual y calcular el rango
            numero_inicial = resultado['counter_value'] + 1
            numero_final = resultado['counter_value'] + cantidad_qr
            
            # Manejar el caso donde el rango excede 9999
            if numero_final > 9999:
                # Parte del rango antes de 9999
                numeros_antes_reinicio = 9999 - numero_inicial + 1
                # Parte del rango después del reinicio
                numeros_despues_reinicio = cantidad_qr - numeros_antes_reinicio
                
                # Configurar dos rangos
                rango1_inicio = numero_inicial
                rango1_final = 9999
                rango2_inicio = 1
                rango2_final = numeros_despues_reinicio
                
                nuevo_valor_contador = numeros_despues_reinicio
                
                # Actualizar el contador
                cursor.execute("""
                    UPDATE globalcounter 
                    SET counter_value = %s 
                    WHERE counter_type = 'QR_CODE'
                """, (nuevo_valor_contador,))
                
                codigos_generados = []
                
                # Obtener información del usuario actual para el historial
                user_id = session.get('user_id')
                user_name = session.get('user_name', 'Sistema')
                local = session.get('user_local', 'El Mekatiadero')
                hora_colombia = get_colombia_time()
                fecha_hora_str = format_datetime_for_db(hora_colombia)
                
                # Generar códigos del primer rango (hasta 9999)
                for i in range(rango1_inicio, rango1_final + 1):
                    nuevo_codigo = f"QR{i:04d}"
                    codigos_generados.append(nuevo_codigo)
                    
                    # Insertar en la tabla qrcode
                    cursor.execute("""
                        INSERT INTO qrcode (code, remainingTurns, isActive, turnPackageId, qr_name)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (nuevo_codigo, 0, 1, 1, nombre))
                    
                    # Registrar automáticamente en el historial
                    cursor.execute("""
                        INSERT INTO qrhistory (qr_code, user_id, user_name, local, fecha_hora, qr_name)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (nuevo_codigo, user_id, user_name, local, fecha_hora_str, nombre))
                
                # Generar códigos del segundo rango (después del reinicio)
                for i in range(rango2_inicio, rango2_final + 1):
                    nuevo_codigo = f"QR{i:04d}"
                    codigos_generados.append(nuevo_codigo)
                    
                    # Insertar en la tabla qrcode
                    cursor.execute("""
                        INSERT INTO qrcode (code, remainingTurns, isActive, turnPackageId, qr_name)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (nuevo_codigo, 0, 1, 1, nombre))
                    
                    # Registrar automáticamente en el historial
                    cursor.execute("""
                        INSERT INTO qrhistory (qr_code, user_id, user_name, local, fecha_hora, qr_name)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (nuevo_codigo, user_id, user_name, local, fecha_hora_str, nombre))
                
                connection.commit()
                
                app.logger.warning(f"Contador QR reiniciado automáticamente al generar lote grande. Generados {cantidad_qr} códigos")
                app.logger.info(f"Generados {cantidad_qr} códigos QR: desde QR{rango1_inicio:04d} hasta QR{rango1_final:04d} y desde QR{rango2_inicio:04d} hasta QR{rango2_final:04d} por {user_name}")
                
                return codigos_generados
            else:
                # Actualizar el contador normalmente
                cursor.execute("""
                    UPDATE globalcounter 
                    SET counter_value = %s 
                    WHERE counter_type = 'QR_CODE'
                """, (numero_final,))
        
        codigos_generados = []
        
        # Obtener información del usuario actual para el historial
        user_id = session.get('user_id')
        user_name = session.get('user_name', 'Sistema')
        local = session.get('user_local', 'El Mekatiadero')
        hora_colombia = get_colombia_time()
        fecha_hora_str = format_datetime_for_db(hora_colombia)
        
        # Generar todos los códigos
        for i in range(numero_inicial, numero_final + 1):
            nuevo_codigo = f"QR{i:04d}"
            codigos_generados.append(nuevo_codigo)
            
            # Insertar en la tabla qrcode
            cursor.execute("""
                INSERT INTO qrcode (code, remainingTurns, isActive, turnPackageId, qr_name)
                VALUES (%s, %s, %s, %s, %s)
            """, (nuevo_codigo, 0, 1, 1, nombre))
            
            # Registrar automáticamente en el historial
            cursor.execute("""
                INSERT INTO qrhistory (qr_code, user_id, user_name, local, fecha_hora, qr_name)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (nuevo_codigo, user_id, user_name, local, fecha_hora_str, nombre))
        
        connection.commit()
        
        app.logger.info(f"Generados {cantidad_qr} códigos QR: desde QR{numero_inicial:04d} hasta QR{numero_final:04d} por {user_name}")
        
        return codigos_generados
        
    except Exception as e:
        app.logger.error(f"Error generando códigos QR en lote: {e}")
        if connection:
            connection.rollback()
        return []
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

def generar_codigos_qr_lote_con_paquete(cantidad_qr, nombre="", paquete_id=1):
    """Generar múltiples códigos QR y asignar paquete desde el inicio (blindado contra duplicados)"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return []

        cursor = get_db_cursor(connection)

        # 🔹 Obtener información del paquete
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

        # 🔹 Bloquear contador
        cursor.execute("""
            SELECT counter_value FROM globalcounter
            WHERE counter_type = 'QR_CODE'
            FOR UPDATE
        """)
        resultado = cursor.fetchone()

        if not resultado:
            cursor.execute("""
                INSERT INTO globalcounter (counter_type, counter_value, description)
                VALUES ('QR_CODE', 0, 'Contador para códigos QR')
            """)
            contador_bd = 0
        else:
            contador_bd = resultado['counter_value']

        # 🔹 Obtener el mayor QR REAL existente
        cursor.execute("""
            SELECT MAX(CAST(SUBSTRING(code, 3) AS UNSIGNED)) AS max_real
            FROM qrcode
        """)
        max_real = cursor.fetchone()['max_real'] or 0

        # 🔹 Sincronizar contador
        contador_actual = max(contador_bd, max_real)

        numero_inicial = contador_actual + 1
        numero_final = contador_actual + cantidad_qr

        # 🔹 Actualizar contador global con el valor REAL final
        cursor.execute("""
            UPDATE globalcounter
            SET counter_value = %s
            WHERE counter_type = 'QR_CODE'
        """, (numero_final,))

        # 🔹 Datos del usuario
        user_id = session.get('user_id')
        user_name = session.get('user_name', 'Sistema')
        local = session.get('user_local', 'El Mekatiadero')
        hora_colombia = get_colombia_time()
        fecha_hora_str = format_datetime_for_db(hora_colombia)

        codigos_generados = []

        # 🔹 Generar QRs
        for i in range(numero_inicial, numero_final + 1):
            nuevo_codigo = f"QR{i:04d}"
            codigos_generados.append(nuevo_codigo)

            # Insertar QR
            cursor.execute("""
                INSERT INTO qrcode (code, remainingTurns, isActive, turnPackageId, qr_name)
                VALUES (%s, %s, %s, %s, %s)
            """, (nuevo_codigo, turns_paquete, 1, paquete_id, nombre))

            # Insertar userturns
            cursor.execute("""
                INSERT INTO userturns (qr_code_id, turns_remaining, total_turns, package_id)
                VALUES (LAST_INSERT_ID(), %s, %s, %s)
            """, (turns_paquete, turns_paquete, paquete_id))

            # Insertar historial
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
        app.logger.error(f"Error generando códigos QR en lote con paquete: {e}")
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
    """Obtener el estado actual del contador de QR con información de reinicio"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        # Obtener el valor actual del contador
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
        
        # Determinar el próximo código disponible con manejo de reinicio
        proximo_numero = resultado['counter_value'] + 1
        if proximo_numero > 9999:
            proximo_numero = 1
            proximo_codigo = f"QR{proximo_numero:04d}"
            reinicio_pendiente = True
        else:
            proximo_codigo = f"QR{proximo_numero:04d}"
            reinicio_pendiente = False
        
        # Calcular códigos disponibles hasta el próximo reinicio
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
                'advertencia': reinicio_pendiente and '¡El contador se reiniciará en el próximo QR generado!'
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
            
            # Actualizar el contador
            cursor.execute("""
                UPDATE globalcounter 
                SET counter_value = %s 
                WHERE counter_type = 'QR_CODE'
            """, (nuevo_valor,))
            
            connection.commit()
            
            # Verificar el nuevo valor
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
        return api_response('E005', http_status=400, data={'message': 'Valor inválido'})

def get_next_qr_number():
    """Obtener el próximo número de QR disponible"""
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
            # Si no existe, empezar desde 1
            return 1
            
    except Exception as e:
        app.logger.error(f"Error obteniendo próximo número QR: {e}")
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
    """Generar nuevos códigos QR con 4 cifras"""
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
        
        # Verificar que no se exceda el límite máximo
        if cantidad > 9999:
            return api_response(
                'E005',
                http_status=400,
                data={'message': 'No se pueden generar más de 9999 códigos a la vez'}
            )
        
        # Si se proporciona paquete_id, usar la función que incluye asignación de paquete
       
        if paquete_id:
            # Generar códigos con el paquete incluido
            codigos_generados = generar_codigos_qr_lote_con_paquete(cantidad, nombre, paquete_id)
            
            if not codigos_generados:
                return api_response('E001', http_status=500)
            
            # Verificar información del paquete para la respuesta
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
                'formato': 'QRXXXX (4 dígitos, de QR0001 a QR9999)',
                'nota': 'El contador se reiniciará automáticamente al llegar a QR9999'
            }
            
            app.logger.info(f"Generados {len(codigos_generados)} códigos QR con paquete {paquete['name']}")
            
            return api_response(
                'S002',
                status='success',
                data=response_data
            )
        else:
            # Generar códigos sin paquete asignado
            codigos_generados = generar_codigos_qr_lote(cantidad, nombre)
            
            if not codigos_generados:
                return api_response('E001', http_status=500)
            
            app.logger.info(f"Generados {len(codigos_generados)} códigos QR sin paquete")
            
            return api_response(
                'S002',
                status='success',
                data={
                    'codigos': codigos_generados,
                    'cantidad': len(codigos_generados),
                    'nombre': nombre,
                    'formato': 'QRXXXX (4 dígitos, de QR0001 a QR9999)',
                    'nota': 'El contador se reiniciará automáticamente al llegar a QR9999'
                }
            )
        
    except Exception as e:
        app.logger.error(f"Error generando QR: {e}")
        return api_response('E001', http_status=500)

@app.route('/api/obtener-siguiente-qr', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero'])
def obtener_siguiente_qr():
    """Obtener el siguiente código QR disponible con manejo de reinicio"""
    siguiente_codigo = generar_codigo_qr()
    
    if not siguiente_codigo:
        return api_response('E001', http_status=500)
    
    # Extraer el número del código para saber si se reinició
    numero_qr = int(siguiente_codigo[2:])
    
    return api_response(
        'S001',
        status='success',
        data={
            'siguiente_codigo': siguiente_codigo,
            'numero_qr': numero_qr,
            'es_reinicio': numero_qr == 1,
            'mensaje': '¡Contador reiniciado!' if numero_qr == 1 else None
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
    """Asignar un paquete a un código QR"""
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
        
        # Verificar si el QR ya tiene un paquete
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
    """Verificar información de un código QR"""
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
        
        cursor.execute("INSERT INTO turnusage (qrCodeId, machineId) VALUES (%s, %s)", (qr_id, machine_id))
        cursor.execute("UPDATE userturns SET turns_remaining = turns_remaining - 1 WHERE qr_code_id = %s", (qr_id,))
        connection.commit()
        
        app.logger.info(f"Turno usado - QR: {qr_code}, Máquina: {machine_id}")
        
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
@validate_required_fields(['qr_code', 'machine_id', 'turnos_devueltos'])
def reportar_falla():
    """Reportar falla en una máquina"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        qr_code = data['qr_code']
        machine_id = data['machine_id']
        machine_name = data.get('machine_name')
        turnos_devueltos = data['turnos_devueltos']
        
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
        if not turnos_data:
            return api_response('Q003', http_status=400)
        
        cursor.execute("""
            INSERT INTO machinefailures (qr_code_id, machine_id, machine_name, turnos_devueltos)
            VALUES (%s, %s, %s, %s)
        """, (qr_id, machine_id, machine_name, turnos_devueltos))
        
        cursor.execute("UPDATE userturns SET turns_remaining = turns_remaining + %s WHERE qr_code_id = %s",
                       (turnos_devueltos, qr_id))
        connection.commit()
        
        app.logger.info(f"Falla reportada - Máquina: {machine_id}, Turnos devueltos: {turnos_devueltos}")
        
        return api_response(
            'S003',
            status='success',
            data={
                'nuevos_turnos': turnos_data['turns_remaining'] + turnos_devueltos
            }
        )
        
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
    """Guardar QR en historial"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        qr_code = data['qr_code']
        user_id = session.get('user_id')
        user_name = session.get('user_name', 'Usuario')
        local = session.get('user_local', 'El Mekatiadero')
        es_venta_real = data.get('es_venta_real', False)

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)

        hora_colombia = get_colombia_time()
        fecha_hora_str = format_datetime_for_db(hora_colombia)
        
        # Obtener información del QR
        cursor.execute("SELECT qr_name, turnPackageId FROM qrcode WHERE code = %s", (qr_code,))
        qr_data = cursor.fetchone()
        qr_name = qr_data['qr_name'] if qr_data and 'qr_name' in qr_data else None
        
        # **NUEVA LÓGICA: Verificar si YA EXISTE una venta real para este QR**
        cursor.execute("""
            SELECT COUNT(*) as ventas_existentes
            FROM qrhistory 
            WHERE qr_code = %s 
            AND es_venta_real = TRUE
        """, (qr_code,))
        
        existe_venta_anterior = cursor.fetchone()['ventas_existentes'] > 0
        
        tiene_paquete = qr_data and qr_data['turnPackageId'] is not None and qr_data['turnPackageId'] != 1
        
        # **REGLA ACTUALIZADA: Es venta SOLO si:
        # 1. Es marcado como venta real (es_venta_real=True)
        # 2. Tiene paquete asignado
        # 3. NO existe ya una venta anterior para este mismo QR
        es_venta = False
        if es_venta_real and tiene_paquete and not existe_venta_anterior:
            es_venta = True
        elif es_venta_real and existe_venta_anterior:
            # Si ya existe venta anterior, marcar como duplicada
            es_venta = False
            app.logger.warning(f"Intento de registrar venta duplicada para QR: {qr_code}")
        
        # Insertar en historial
        cursor.execute("""
            INSERT INTO qrhistory (qr_code, user_id, user_name, local, fecha_hora, qr_name, es_venta_real)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (qr_code, user_id, user_name, local, fecha_hora_str, qr_name, es_venta_real))
        
        connection.commit()
        
        # Solo actualizar contador diario si es una NUEVA venta real
        if es_venta:
            actualizar_contador_diario(hora_colombia.strftime('%Y-%m-%d'))
            app.logger.info(f"VENTA NUEVA registrada: {qr_code} por {user_name}")
            mensaje = "Venta registrada"
        elif existe_venta_anterior and es_venta_real:
            app.logger.info(f"VENTA DUPLICADA (ya existía): {qr_code} por {user_name}")
            mensaje = "Este QR ya tenía una venta registrada anteriormente"
        else:
            # Si es escaneo de consulta, NO actualizar contador diario
            app.logger.info(f"Escaneo de CONSULTA: {qr_code} por {user_name}")
            mensaje = "Escaneo de consulta registrado"
        
        return api_response(
            'S006',
            status='success',
            data={
                'qr_name': qr_name,
                'es_venta': es_venta,
                'es_venta_real': es_venta_real,
                'tiene_paquete': tiene_paquete,
                'existe_venta_anterior': existe_venta_anterior,
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
        
        # Verificar si ya existe venta real para este QR
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
        
        # Obtener información básica del QR
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
    """Guardar múltiples QR con paquete asignado desde el inicio"""
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
            return api_response('E005', http_status=400, data={'message': 'Lista de QR vacía'})

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
            
            # Registrar en historial marcando si es venta real
            cursor.execute("""
                INSERT INTO qrhistory (qr_code, user_id, user_name, local, fecha_hora, qr_name, es_venta_real)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (qr_code, user_id, user_name, local, fecha_hora_str, nombre, es_venta_real))

        connection.commit()
        
        # Si es una venta real, actualizar el contador diario
        if es_venta_real and qr_codes:
            actualizar_contador_diario(hora_colombia.strftime('%Y-%m-%d'))
        
        total_qrs = qrs_creados + qrs_actualizados
        
        app.logger.info(f"{total_qrs} QR generados y asignados al paquete {paquete_nombre}")

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
                'es_venta_real': es_venta_real
            }
        )
        
    except Exception as e:
        app.logger.error(f"Error guardando múltiples QR con paquete: {e}")
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
            
            # Registrar en historial
            cursor.execute("""
                INSERT INTO qrhistory (qr_code, user_id, user_name, local, fecha_hora, qr_name, es_venta_real)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (qr_code, user_id, user_name, local, fecha_hora_str, nombre, es_venta_real))

        connection.commit()
        
        # Si es una venta real, actualizar el contador diario
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
        app.logger.error(f"Error guardando múltiples QR: {e}")
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
    """Obtener estadísticas en tiempo real (sin cache)"""
    connection = None
    cursor = None
    try:
        fecha = request.args.get('fecha', get_colombia_time().strftime('%Y-%m-%d'))
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        # QR vendidos hoy (con paquetes)
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
        
        # QR escaneados hoy (todos)
        cursor.execute("""
            SELECT COUNT(*) as escaneados_hoy
            FROM qrhistory
            WHERE DATE(fecha_hora) = %s
        """, (fecha,))
        
        escaneados = cursor.fetchone()
        
        # Turnos utilizados hoy
        cursor.execute("""
            SELECT COUNT(*) as turnos_hoy
            FROM turnusage
            WHERE DATE(usedAt) = %s
        """, (fecha,))
        
        turnos = cursor.fetchone()
        
        # QR generados hoy (nuevos)
        cursor.execute("""
            SELECT COUNT(*) as qr_generados_hoy
            FROM qrcode
            WHERE DATE(createdAt) = %s
        """, (fecha,))
        
        generados = cursor.fetchone()
        
        # Contador global actual
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
        app.logger.error(f"Error obteniendo estadísticas tiempo real: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# ==================== APIS PARA HISTORIAL ====================

@app.route('/api/historial-completo', methods=['GET'])
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
                WHERE h.user_id = %s OR h.local = %s
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

@app.route('/api/historial-qr/<qr_code>', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
def obtener_historial_qr(qr_code):
    """Obtener historial específico de un código QR"""
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
    """Obtener ventas del día"""
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
        """, (fecha,))
        
        resultado = cursor.fetchone()
        
        app.logger.info(f"Ventas del día {fecha}: {resultado['total_ventas']} ventas")
        
        return jsonify({
            'total_ventas': resultado['total_ventas'] or 0,
            'valor_total': float(resultado['valor_total'] or 0)
        })
    except Exception as e:
        app.logger.error(f"Error obteniendo ventas del día: {e}")
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
        
        # 1. Obtener ventas detalladas
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
        
        # 2. Estadísticas generales
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
        
        # 3. Ventas por paquete para gráfico
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
        
        # 4. Ventas por hora para gráfico
        cursor.execute("""
            SELECT 
                HOUR(qh.fecha_hora) as hora,
                COUNT(DISTINCT qh.qr_code) as cantidad
            FROM qrhistory qh
            JOIN qrcode qr ON qr.code = qh.qr_code
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
            AND qr.turnPackageId IS NOT NULL
            AND qr.turnPackageId != 1
            AND qh.es_venta_real = TRUE
            GROUP BY HOUR(qh.fecha_hora)
            ORDER BY hora
        """, (fecha_inicio, fecha_fin))
        
        ventas_por_hora = cursor.fetchall()
        
        # 5. Calcular tendencias vs ayer
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
        
        # Calcular porcentajes de tendencia
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
        
        # Calcular eficiencia (conversión de ventas)
        # Esto es solo un ejemplo - ajusta según tu lógica de negocio
        eficiencia = 85  # Por defecto
        
        # Preparar datos para gráficos
        graficos = {
            'paquetes': {
                'labels': [item['paquete'] for item in ventas_por_paquete],
                'data': [item['cantidad'] for item in ventas_por_paquete]
            },
            'evolucion': {
                'labels': [f"{item['hora']}:00" for item in ventas_por_hora],
                'data': [item['cantidad'] for item in ventas_por_hora],
                'tipo': 'horas'
            }
        }
        
        # Formatear fechas en las ventas
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
        # Por ahora, simplemente redirigir a la API de ventas
        # En una implementación real, aquí generarías un PDF
        
        fecha_inicio = request.args.get('fecha_inicio', get_colombia_time().strftime('%Y-%m-%d'))
        fecha_fin = request.args.get('fecha_fin', get_colombia_time().strftime('%Y-%m-%d'))
        
        app.logger.info(f"Exportando ventas a PDF: {fecha_inicio} - {fecha_fin}")
        
        # Por ahora, devolver un mensaje informativo
        # En producción, implementar generación real de PDF con reportlab o similar
        
        return jsonify({
            'status': 'success',
            'message': 'Función de exportación PDF en desarrollo',
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
    """Reportar falla en una máquina"""
    connection = None
    cursor = None
    
    try:
        data = request.get_json()
        machine_id = data['machine_id']
        description = data['description'].strip()
        problem_type = data.get('problem_type', 'mantenimiento')
        user_id = session.get('user_id', 1)
        
        connection = mysql.connector.connect(
             host=os.getenv("DB_HOST", "mysql"),
    user=os.getenv("DB_USER", "myuser"),
    password=os.getenv("DB_PASSWORD", "mypassword"),
    database=os.getenv("DB_NAME", "maquinasmedellin"),
    port=3306,
    auth_plugin="mysql_native_password"
)
        cursor = connection.cursor(dictionary=True)
        
        # Verificar máquina
        cursor.execute("SELECT id, name FROM machine WHERE id = %s", (machine_id,))
        maquina = cursor.fetchone()
        
        if not maquina:
            return api_response('M001', http_status=404, data={'machine_id': machine_id})
        
        # Determinar estado
        nuevo_estado = 'mantenimiento' if problem_type == 'mantenimiento' else 'inactiva'
        
        # Insertar reporte
        cursor.execute("""
            INSERT INTO errorreport 
            (machineId, userId, description, reportedAt, isResolved)
            VALUES (%s, %s, %s, NOW(), FALSE)
        """, (machine_id, user_id, description))
        
        error_report_id = cursor.lastrowid
        
        # Actualizar máquina
        cursor.execute("""
            UPDATE machine 
            SET status = %s, 
                errorNote = %s,
                dailyFailedTurns = COALESCE(dailyFailedTurns, 0) + 1
            WHERE id = %s
        """, (nuevo_estado, description, machine_id))
        
        connection.commit()
        
        app.logger.info(f"Falla reportada - Máquina: {maquina['name']}, Reporte: #{error_report_id}")
        
        return api_response(
            'S002',
            status='success',
            data={
                'machine_id': machine_id,
                'machine_name': maquina['name'],
                'new_status': nuevo_estado,
                'error_report_id': error_report_id
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
        app.logger.info(f"=== INICIANDO RESOLUCIÓN DE REPORTE {reporte_id} ===")
        
        data = request.get_json()
        comentarios = data.get('comentarios', '')
        user_id = session.get('user_id')
        user_name = session.get('user_name')
        user_role = session.get('user_role')
        
        # DEPURACIÓN DETALLADA
        app.logger.info(f"DEPURACIÓN - user_id: {user_id}, user_name: {user_name}, user_role: {user_role}")
        app.logger.info(f"Datos recibidos: {data}")
        app.logger.info(f"Comentarios: '{comentarios}'")
        
        if not user_id:
            app.logger.error("Usuario no autenticado - Sesión inválida")
            return api_response('E003', http_status=401, data={'message': 'Usuario no autenticado'})
        
        if user_role != 'admin':
            app.logger.error(f"Usuario {user_name} no es admin, es {user_role}")
            return api_response('E004', http_status=403, data={'message': 'Solo administradores pueden resolver reportes'})
        
        connection = get_db_connection()
        if not connection:
            app.logger.error("No se pudo conectar a la BD")
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        # Test de conexión
        cursor.execute("SELECT 1 as test")
        test_result = cursor.fetchone()
        app.logger.info(f"Conexión BD test: {test_result}")
        
        # Verificar que el reporte existe
        cursor.execute("SELECT id FROM errorreport WHERE id = %s", (reporte_id,))
        reporte_existe = cursor.fetchone()
        
        if not reporte_existe:
            app.logger.error(f"Reporte {reporte_id} no encontrado")
            return api_response('M007', http_status=404, data={'message': 'Reporte no encontrado'})
        
        # Obtener información completa del reporte
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
        
        app.logger.info(f"Máquina asociada: id={machine_id}, nombre={machine_name}")
        
        try:
            # 1. Marcar reporte como resuelto en ErrorReport
            app.logger.info("Actualizando ErrorReport...")
            
            query_update_er = """
                UPDATE errorreport 
                SET isResolved = TRUE, resolved_at = NOW()
                WHERE id = %s
            """
            
            cursor.execute(query_update_er, (reporte_id,))
            app.logger.info(f"ErrorReport actualizado: {cursor.rowcount} filas afectadas")
            
            # 2. Crear registro en confirmation_logs (VERSIÓN SIMPLIFICADA)
            app.logger.info("Insertando en confirmation_logs...")
            
            # Insertar sin foreign keys primero (para debug)
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
                # Si falla, intentar sin comments
                cursor.execute("""
                    INSERT INTO confirmation_logs 
                    (fault_report_id, admin_id, confirmation_status)
                    VALUES (%s, %s, %s)
                """, (reporte_id, user_id, 'resuelta'))
                confirmation_id = cursor.lastrowid
                app.logger.info(f"Registro creado (sin comments) con ID: {confirmation_id}")
            
            # 3. Cambiar estado de la máquina si es necesario
            if machine_id:
                app.logger.info(f"Actualizando estado de máquina {machine_id}...")
                
                cursor.execute("""
                    UPDATE machine 
                    SET status = 'activa'
                    WHERE id = %s AND status IN ('mantenimiento', 'inactiva')
                """, (machine_id,))
                
                if cursor.rowcount > 0:
                    app.logger.info(f"Máquina {machine_id} cambiada a estado 'activa'")
                else:
                    app.logger.info(f"Máquina {machine_id} no cambió de estado (ya estaba activa o no aplica)")
            
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
                    'resolved_by': user_name
                }
            )
            
        except Exception as trans_error:
            app.logger.error(f"Error en transacción: {trans_error}", exc_info=True)
            connection.rollback()
            
            # Dar más detalles del error
            error_msg = str(trans_error)
            
            # Verificar errores específicos
            if "confirmation_logs" in error_msg:
                # Probar estructura de la tabla
                app.logger.info("Verificando estructura de confirmation_logs...")
                try:
                    cursor.execute("DESCRIBE confirmation_logs")
                    estructura = cursor.fetchall()
                    app.logger.info(f"Estructura: {estructura}")
                except Exception as e:
                    app.logger.error(f"Error verificando estructura: {e}")
            
            raise Exception(f"Error en transacción: {error_msg}")
            
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
        
        # 1. Ver estructura
        cursor.execute("DESCRIBE confirmation_logs")
        estructura = cursor.fetchall()
        
        # 2. Ver valores ENUM
        cursor.execute("SHOW COLUMNS FROM confirmation_logs LIKE 'confirmation_status'")
        enum_info = cursor.fetchone()
        
        # 3. Intentar insertar manualmente
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

# ==================== RUTAS PARA EL PANEL DE ADMINISTRACIÓN ====================

@app.route('/admin')
@require_login(['admin'])
def mostrar_admin():
    """Mostrar panel de administración"""
    hora_colombia = get_colombia_time()
    return render_template('admin/index.html',
                           nombre_usuario=session.get('user_name', 'Administrador'),
                           local_usuario=session.get('user_local', 'Sistema'),
                           hora_actual=hora_colombia.strftime('%H:%M:%S'),
                           fecha_actual=hora_colombia.strftime('%Y-%m-%d'))

@app.route('/admin/usuarios/gestionusuarios')
@require_login(['admin'])
def mostrar_gestion_usuarios():
    """Mostrar gestión de usuarios"""
    return render_template('admin/usuarios/gestionusuarios.html',
                           nombre_usuario=session.get('user_name', 'Administrador'),
                           local_usuario=session.get('user_local', 'Sistema'))

@app.route('/admin/paquetes/gestionpaquetes')
@require_login(['admin'])
def mostrar_gestion_paquetes():
    """Mostrar gestión de paquetes"""
    return render_template('admin/paquetes/gestionpaquetes.html',
                           nombre_usuario=session.get('user_name', 'Administrador'),
                           local_usuario=session.get('user_local', 'Sistema'))

@app.route('/admin/locales/gestionlocales')
@require_login(['admin'])
def mostrar_gestion_locales():
    """Mostrar gestión de locales"""
    return render_template('admin/locales/gestionlocales.html',
                           nombre_usuario=session.get('user_name', 'Administrador'),
                           local_usuario=session.get('user_local', 'Sistema'))

@app.route('/admin/maquinas/gestionmaquinas')
@require_login(['admin'])
def mostrar_gestion_maquinas():
    """Mostrar gestión de máquinas"""
    return render_template('admin/maquinas/gestionmaquinas.html',
                           nombre_usuario=session.get('user_name', 'Administrador'),
                           local_usuario=session.get('user_local', 'Sistema'))

@app.route('/admin/ventas/liquidaciones')
@require_login(['admin'])
def mostrar_liquidaciones():
    """Mostrar liquidaciones"""
    hora_colombia = get_colombia_time()
    return render_template('ventas/liquidaciones.html',
                         nombre_usuario=session.get('user_name', 'Administrador'),
                         local_usuario=session.get('user_local', 'Sistema'),
                         hora_actual=hora_colombia.strftime('%H:%M:%S'),
                         fecha_actual=hora_colombia.strftime('%Y-%m-%d'))

@app.route('/admin/ventas/reportes')
@require_login(['admin'])
def mostrar_reportes():
    """Mostrar reportes"""
    hora_colombia = get_colombia_time()
    return render_template('ventas/reportes.html',
                         nombre_usuario=session.get('user_name', 'Administrador'),
                         local_usuario=session.get('user_local', 'Sistema'),
                         hora_actual=hora_colombia.strftime('%H:%M:%S'),
                         fecha_actual=hora_colombia.strftime('%Y-%m-%d'))

@app.route('/admin/mensajes/gestionmensajes')
@require_login(['admin'])
def mostrar_gestion_mensajes():
    """Mostrar gestión de mensajes"""
    hora_colombia = get_colombia_time()
    return render_template('admin/mensajes/gestionmensajes.html',
                         nombre_usuario=session.get('user_name', 'Administrador'),
                         local_usuario=session.get('user_local', 'Sistema'),
                         hora_actual=hora_colombia.strftime('%H:%M:%S'),
                         fecha_actual=hora_colombia.strftime('%Y-%m-%d'))

# ==================== APIS PARA GESTIÓN DE USUARIOS ====================

@app.route('/debug/usuarios')
@require_login(['admin'])
def debug_usuarios():
    """Debug: Ver usuarios en formato crudo"""
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
        
        # Convertir datetime a string
        usuarios_formateados = []
        for u in usuarios:
            usuario_dict = dict(u)
            for key, value in usuario_dict.items():
                if hasattr(value, 'isoformat'):
                    usuario_dict[key] = value.isoformat()
            usuarios_formateados.append(usuario_dict)
        
        return jsonify({
            'count': len(usuarios_formateados),
            'data': usuarios_formateados
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()
            
@app.route('/api/usuarios', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_usuarios():
    """Obtener todos los usuarios"""
    app.logger.info(f"API Usuarios llamada por: {session.get('user_name')}")
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("""
            SELECT 
                u.*, 
                creador.name as creador_nombre,
                COALESCE(u.isActive, TRUE) as isActive  -- Si no existe, usar TRUE por defecto
            FROM users u
            LEFT JOIN users creador ON u.createdBy = creador.id
            ORDER BY u.createdAt DESC
        """)
        
        usuarios = cursor.fetchall()
        
        usuarios_formateados = []
        for usuario in usuarios:
            # Asegurar que isActive existe
            is_active = usuario.get('isActive', True)
            if is_active is None:
                is_active = True
                
            usuarios_formateados.append({
                'id': usuario['id'],
                'name': usuario['name'],
                'role': usuario['role'],
                'local': usuario.get('local', 'El Mekatiadero'),
                'createdBy': usuario['createdBy'],
                'creador': {'name': usuario['creador_nombre']} if usuario['creador_nombre'] else None,
                'createdAt': usuario['createdAt'].isoformat() if usuario.get('createdAt') else None,
                'notes': usuario.get('notes', ''),
                'isActive': is_active
            })
        
        return jsonify(usuarios_formateados)
        
    except Exception as e:
        app.logger.error(f"Error obteniendo usuarios: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@app.route('/api/usuarios/<int:usuario_id>', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_usuario(usuario_id):
    """Obtener un usuario específico"""
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
            'id': usuario['id'],
            'name': usuario['name'],
            'role': usuario['role'],
            'createdBy': usuario['createdBy'],
            'createdAt': usuario['createdAt'],
            'notes': usuario['notes']
        })
        
    except Exception as e:
        app.logger.error(f"Error obteniendo usuario: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/usuarios', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
@validate_required_fields(['name', 'password', 'role'])
def crear_usuario():
    """Crear un nuevo usuario"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        name = data['name']
        password = data['password']
        role = data['role']
        notes = data.get('notes', '')
        
        # Validaciones
        if len(password) < 6:
            return api_response('U003', http_status=400)
        
        if role not in ['admin', 'cajero', 'admin_restaurante', 'socio']:
            return api_response('U004', http_status=400)
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        # Verificar si el usuario ya existe
        cursor.execute("SELECT id FROM users WHERE name = %s", (name,))
        if cursor.fetchone():
            return api_response('U002', http_status=400, data={'name': name})
        
        # Crear usuario
        cursor.execute("""
            INSERT INTO users (name, password, role, createdBy, notes)
            VALUES (%s, %s, %s, %s, %s)
        """, (name, password, role, session.get('user_id'), notes))
        
        connection.commit()
        
        app.logger.info(f"Usuario creado: {name} ({role})")
        
        return api_response(
            'S002',
            status='success',
            data={'usuario_id': cursor.lastrowid}
        )
        
    except Exception as e:
        app.logger.error(f"Error creando usuario: {e}")
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/usuarios/<int:usuario_id>', methods=['PUT'])
@handle_api_errors
@require_login(['admin'])
@validate_required_fields(['name', 'role'])
def actualizar_usuario(usuario_id):
    """Actualizar un usuario existente"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        name = data['name']
        password = data.get('password')
        role = data['role']
        notes = data.get('notes')
        
        if role not in ['admin', 'cajero', 'admin_restaurante', 'socio']:
            return api_response('U004', http_status=400)
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        # Verificar que el usuario existe
        cursor.execute("SELECT id FROM users WHERE id = %s", (usuario_id,))
        if not cursor.fetchone():
            return api_response('U001', http_status=404, data={'usuario_id': usuario_id})
        
        # Verificar nombre duplicado
        cursor.execute("SELECT id FROM users WHERE name = %s AND id != %s", (name, usuario_id))
        if cursor.fetchone():
            return api_response('U002', http_status=400, data={'name': name})
        
        # Actualizar usuario
        if password:
            cursor.execute("""
                UPDATE users 
                SET name = %s, password = %s, role = %s, notes = %s
                WHERE id = %s
            """, (name, password, role, notes, usuario_id))
        else:
            cursor.execute("""
                UPDATE users 
                SET name = %s, role = %s, notes = %s
                WHERE id = %s
            """, (name, role, notes, usuario_id))
        
        connection.commit()
        
        app.logger.info(f"Usuario actualizado: {name} (ID: {usuario_id})")
        
        return api_response('S003', status='success')
        
    except Exception as e:
        app.logger.error(f"Error actualizando usuario: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/usuarios/<int:usuario_id>', methods=['DELETE'])
@handle_api_errors
@require_login(['admin'])
def eliminar_usuario(usuario_id):
    """Eliminar un usuario"""
    if usuario_id == session.get('user_id'):
        return api_response('U005', http_status=400)
    
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        # Verificar que el usuario existe
        cursor.execute("SELECT name FROM users WHERE id = %s", (usuario_id,))
        usuario = cursor.fetchone()
        if not usuario:
            return api_response('U001', http_status=404, data={'usuario_id': usuario_id})
        
        # Eliminar usuario
        cursor.execute("DELETE FROM users WHERE id = %s", (usuario_id,))
        connection.commit()
        
        app.logger.info(f"Usuario eliminado: {usuario['name']} (ID: {usuario_id})")
        
        return api_response('S004', status='success')
        
    except Exception as e:
        app.logger.error(f"Error eliminando usuario: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# ==================== APIS PARA GESTIÓN DE PAQUETES ====================

@app.route('/api/paquetes/<int:paquete_id>', methods=['GET'])
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
        
        # Validaciones
        if turns < 1:
            return api_response('E005', http_status=400, data={'message': 'Turnos debe ser mayor a 0'})
        
        if price < 1000:
            return api_response('E005', http_status=400, data={'message': 'Precio debe ser mayor a $1,000'})
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        # Verificar si el paquete ya existe
        cursor.execute("SELECT id FROM turnpackage WHERE name = %s", (name,))
        if cursor.fetchone():
            return api_response('E007', http_status=400, data={'message': 'Paquete ya existe'})
        
        # Crear paquete
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
        
        # Validaciones
        if turns < 1:
            return api_response('E005', http_status=400, data={'message': 'Turnos debe ser mayor a 0'})
        
        if price < 1000:
            return api_response('E005', http_status=400, data={'message': 'Precio debe ser mayor a $1,000'})
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        # Verificar que el paquete existe
        cursor.execute("SELECT id FROM turnpackage WHERE id = %s", (paquete_id,))
        if not cursor.fetchone():
            return api_response('Q004', http_status=404, data={'paquete_id': paquete_id})
        
        # Verificar nombre duplicado
        cursor.execute("SELECT id FROM turnpackage WHERE name = %s AND id != %s", (name, paquete_id))
        if cursor.fetchone():
            return api_response('E007', http_status=400, data={'message': 'Nombre de paquete ya existe'})
        
        # Actualizar paquete
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
        
        # Verificar que el paquete existe
        cursor.execute("SELECT name FROM turnpackage WHERE id = %s", (paquete_id,))
        paquete = cursor.fetchone()
        if not paquete:
            return api_response('Q004', http_status=404, data={'paquete_id': paquete_id})
        
        # Verificar si el paquete está en uso
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
                    'message': f'Paquete en uso por {uso_count} códigos QR',
                    'uso_count': uso_count
                }
            )
        
        # Eliminar paquete
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

# ==================== APIS PARA GESTIÓN DE LOCALES ====================

@app.route('/api/locales', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_locales():
    """Obtener todos los locales con estadísticas"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("""
            SELECT l.*, 
                   COUNT(m.id) as maquinas_count,
                   SUM(CASE WHEN m.status = 'activa' THEN 1 ELSE 0 END) as maquinas_activas
            FROM location l
            LEFT JOIN machine m ON l.id = m.location_id
            GROUP BY l.id
            ORDER BY l.name
        """)
        
        locales = cursor.fetchall()
        return jsonify(locales)
        
    except Exception as e:
        app.logger.error(f"Error obteniendo locales: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/locales/<int:local_id>', methods=['GET'])
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
        
        # Verificar si el local ya existe
        cursor.execute("SELECT id FROM location WHERE name = %s", (name,))
        if cursor.fetchone():
            return api_response('E007', http_status=400, data={'message': 'Local ya existe'})
        
        # Crear local
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
        
        # Verificar que el local existe
        cursor.execute("SELECT id FROM location WHERE id = %s", (local_id,))
        if not cursor.fetchone():
            return api_response('E002', http_status=404, data={'local_id': local_id})
        
        # Verificar nombre duplicado
        cursor.execute("SELECT id FROM location WHERE name = %s AND id != %s", (name, local_id))
        if cursor.fetchone():
            return api_response('E007', http_status=400, data={'message': 'Nombre de local ya existe'})
        
        # Actualizar local
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
        
        # Verificar que el local existe
        cursor.execute("SELECT name FROM location WHERE id = %s", (local_id,))
        local = cursor.fetchone()
        if not local:
            return api_response('E002', http_status=404, data={'local_id': local_id})
        
        # Verificar si el local tiene máquinas asignadas
        cursor.execute("SELECT COUNT(*) as maquinas_count FROM machine WHERE location_id = %s", (local_id,))
        maquinas_count = cursor.fetchone()['maquinas_count']
        
        if maquinas_count > 0:
            return api_response(
                'W005',
                status='warning',
                http_status=400,
                data={
                    'message': f'Local tiene {maquinas_count} máquinas asignadas',
                    'maquinas_count': maquinas_count
                }
            )
        
        # Eliminar local
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

# ==================== APIS PARA GESTIÓN DE MÁQUINAS ====================

@app.route('/api/maquinas', methods=['GET'])
@handle_api_errors
def obtener_maquinas():
    """Obtener todas las máquinas con información completa"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
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
                l.name as location_name,
                COALESCE(mpr.porcentaje_restaurante, 35.00) as porcentaje_restaurante
            FROM machine m
            LEFT JOIN location l ON m.location_id = l.id
            LEFT JOIN maquinaporcentajerestaurante mpr ON m.id = mpr.maquina_id
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
                'info_propietarios': info_propietarios
            })
        
        return jsonify(maquinas_formateadas)
        
    except Exception as e:
        app.logger.error(f"Error obteniendo máquinas: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/maquinas/<int:maquina_id>', methods=['GET'])
@handle_api_errors
def obtener_maquina(maquina_id):
    """Obtener una máquina específica"""
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
                COALESCE(mpr.porcentaje_restaurante, 35.00) as porcentaje_restaurante
            FROM machine m
            LEFT JOIN location l ON m.location_id = l.id
            LEFT JOIN maquinaporcentajerestaurante mpr ON m.id = mpr.maquina_id
            WHERE m.id = %s
        """, (maquina_id,))
        
        maquina = cursor.fetchone()
        
        if not maquina:
            return api_response('M001', http_status=404, data={'machine_id': maquina_id})
        
        # Obtener información de propietarios
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
        
        info_propietarios = ", ".join([
            f"{p['nombre']} ({p['porcentaje_propiedad']}%)" for p in propietarios
        ]) if propietarios else "Sin propietarios"
        
        return jsonify({
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
            'info_propietarios': info_propietarios
        })
        
    except Exception as e:
        app.logger.error(f"Error obteniendo máquina: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/maquinas', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
@validate_required_fields(['name', 'type', 'location_id'])
def crear_maquina():
    """Crear una nueva máquina"""
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
        
        # Validaciones
        if type not in ['simulador', 'arcade', 'peluchera']:
            return api_response('E005', http_status=400, data={'message': 'Tipo de máquina inválido'})
        
        if status not in ['activa', 'mantenimiento', 'inactiva']:
            return api_response('E005', http_status=400, data={'message': 'Estado inválido'})
        
        if not (0 <= float(porcentaje_restaurante) <= 100):
            return api_response('E005', http_status=400, data={'message': 'Porcentaje debe estar entre 0 y 100'})
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        # Verificar si la máquina ya existe
        cursor.execute("SELECT id FROM machine WHERE name = %s", (name,))
        if cursor.fetchone():
            return api_response('E007', http_status=400, data={'message': 'Máquina ya existe'})
        
        # Verificar que el local existe
        cursor.execute("SELECT id FROM location WHERE id = %s", (location_id,))
        if not cursor.fetchone():
            return api_response('E002', http_status=404, data={'local_id': location_id})
        
        # Crear máquina
        cursor.execute("""
            INSERT INTO machine (name, type, status, location_id, errorNote)
            VALUES (%s, %s, %s, %s, %s)
        """, (name, type, status, location_id, errorNote))
        
        maquina_id = cursor.lastrowid
        
        # Guardar porcentaje del restaurante si es diferente al default
        if float(porcentaje_restaurante) != 35.00:
            cursor.execute("""
                INSERT INTO MaquinaPorcentajeRestaurante (maquina_id, porcentaje_restaurante)
                VALUES (%s, %s)
            """, (maquina_id, porcentaje_restaurante))
        
        connection.commit()
        
        app.logger.info(f"Máquina creada: {name} (ID: {maquina_id})")
        
        return api_response(
            'S002',
            status='success',
            data={'maquina_id': maquina_id}
        )
        
    except Exception as e:
        app.logger.error(f"Error creando máquina: {e}")
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/maquinas/<int:maquina_id>', methods=['PUT'])
@handle_api_errors
@require_login(['admin'])
@validate_required_fields(['name', 'type', 'status', 'location_id'])
def actualizar_maquina(maquina_id):
    """Actualizar una máquina existente"""
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
        
        # Validaciones
        if type not in ['simulador', 'arcade', 'peluchera']:
            return api_response('E005', http_status=400, data={'message': 'Tipo de máquina inválido'})
        
        if status not in ['activa', 'mantenimiento', 'inactiva']:
            return api_response('E005', http_status=400, data={'message': 'Estado inválido'})
        
        if not (0 <= float(porcentaje_restaurante) <= 100):
            return api_response('E005', http_status=400, data={'message': 'Porcentaje debe estar entre 0 y 100'})
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        # Verificar que la máquina existe
        cursor.execute("SELECT name FROM machine WHERE id = %s", (maquina_id,))
        maquina = cursor.fetchone()
        if not maquina:
            return api_response('M001', http_status=404, data={'machine_id': maquina_id})
        
        # Verificar nombre duplicado
        cursor.execute("SELECT id FROM machine WHERE name = %s AND id != %s", (name, maquina_id))
        if cursor.fetchone():
            return api_response('E007', http_status=400, data={'message': 'Nombre de máquina ya existe'})
        
        # Verificar que el local existe
        cursor.execute("SELECT id FROM location WHERE id = %s", (location_id,))
        if not cursor.fetchone():
            return api_response('E002', http_status=404, data={'local_id': location_id})
        
        # Actualizar máquina
        cursor.execute("""
            UPDATE machine 
            SET name = %s, type = %s, status = %s, location_id = %s, errorNote = %s
            WHERE id = %s
        """, (name, type, status, location_id, errorNote, maquina_id))
        
        # Actualizar porcentaje del restaurante
        if float(porcentaje_restaurante) != 35.00:
            cursor.execute("""
                INSERT INTO MaquinaPorcentajeRestaurante (maquina_id, porcentaje_restaurante)
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE porcentaje_restaurante = %s
            """, (maquina_id, porcentaje_restaurante, porcentaje_restaurante))
        else:
            cursor.execute("DELETE FROM MaquinaPorcentajeRestaurante WHERE maquina_id = %s", (maquina_id,))
        
        connection.commit()
        
        app.logger.info(f"Máquina actualizada: {name} (ID: {maquina_id})")
        
        return api_response('S003', status='success')
        
    except Exception as e:
        app.logger.error(f"Error actualizando máquina: {e}")
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
    """Eliminar una máquina"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        # Verificar que la máquina existe
        cursor.execute("SELECT name FROM machine WHERE id = %s", (maquina_id,))
        maquina = cursor.fetchone()
        if not maquina:
            return api_response('M001', http_status=404, data={'machine_id': maquina_id})
        
        # Verificar si la máquina tiene uso histórico
        cursor.execute("SELECT COUNT(*) as uso_count FROM turnusage WHERE machineId = %s", (maquina_id,))
        uso_count = cursor.fetchone()['uso_count']
        
        if uso_count > 0:
            return api_response(
                'W004',
                status='warning',
                http_status=400,
                data={
                    'message': f'Máquina tiene {uso_count} usos registrados',
                    'uso_count': uso_count,
                    'machine_name': maquina['name']
                }
            )
        
        # Eliminar registros relacionados
        cursor.execute("DELETE FROM MaquinaPorcentajeRestaurante WHERE maquina_id = %s", (maquina_id,))
        cursor.execute("DELETE FROM MaquinaPropietario WHERE maquina_id = %s", (maquina_id,))
        cursor.execute("DELETE FROM errorreport WHERE machineId = %s", (maquina_id,))
        
        # Eliminar máquina
        cursor.execute("DELETE FROM machine WHERE id = %s", (maquina_id,))
        
        connection.commit()
        
        app.logger.info(f"Máquina eliminada: {maquina['name']} (ID: {maquina_id})")
        
        return api_response('S004', status='success')
        
    except Exception as e:
        app.logger.error(f"Error eliminando máquina: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

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
        
        # Formatear fechas
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
        
        # Validar tipo de mensaje
        if message_type not in ['error', 'success', 'warning', 'info']:
            return api_response('E005', http_status=400, data={'field': 'message_type'})
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        # Verificar si el código ya existe para este idioma
        cursor.execute("""
            SELECT id FROM system_messages 
            WHERE message_code = %s AND language_code = %s
        """, (message_code, language_code))
        
        if cursor.fetchone():
            return api_response(
                'E007',
                http_status=400,
                data={
                    'message': f'El código {message_code} ya existe para el idioma {language_code}'
                }
            )
        
        # Crear mensaje
        cursor.execute("""
            INSERT INTO system_messages 
            (message_code, message_type, message_text, language_code)
            VALUES (%s, %s, %s, %s)
        """, (message_code, message_type, message_text, language_code))
        
        connection.commit()
        
        # Limpiar cache
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
        
        # Verificar que el mensaje existe
        cursor.execute("SELECT message_code FROM system_messages WHERE id = %s", (mensaje_id,))
        mensaje = cursor.fetchone()
        
        if not mensaje:
            return api_response('E002', http_status=404, data={'mensaje_id': mensaje_id})
        
        # Construir consulta dinámica
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
        
        # Limpiar cache
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
        
        # Verificar que el mensaje existe
        cursor.execute("SELECT message_code FROM system_messages WHERE id = %s", (mensaje_id,))
        mensaje = cursor.fetchone()
        
        if not mensaje:
            return api_response('E002', http_status=404, data={'mensaje_id': mensaje_id})
        
        # No permitir eliminar mensajes del sistema esenciales
        codigos_esenciales = ['E001', 'E002', 'A001', 'S001']
        if mensaje['message_code'] in codigos_esenciales:
            return api_response(
                'E007',
                http_status=400,
                data={'message': 'No se pueden eliminar mensajes del sistema esenciales'}
            )
        
        # Eliminar mensaje
        cursor.execute("DELETE FROM system_messages WHERE id = %s", (mensaje_id,))
        connection.commit()
        
        # Limpiar cache
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
        
        # Construir consulta dinámica
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
        
        # Formatear fechas
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
    """Validar si un código de mensaje está disponible"""
    connection = None
    cursor = None
    try:
        import re
        
        # Validar formato
        if not re.match(r'^[A-Z][0-9]{3}$', codigo):
            return jsonify({
                'valido': False,
                'mensaje': 'Formato inválido. Debe ser letra mayúscula seguida de 3 números (ej: E001)'
            })
        
        connection = get_db_connection()
        if not connection:
            return jsonify({
                'valido': False,
                'mensaje': 'Error de conexión a la base de datos'
            })
            
        cursor = get_db_cursor(connection)
        
        # Verificar si existe en español
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
                'mensaje': 'Código disponible para todos los idiomas'
            })
        
        # Determinar idiomas disponibles
        idiomas_existentes = [m['language_code'] for m in mensajes]
        idiomas_disponibles = ['es', 'en']
        idiomas_faltantes = [idioma for idioma in idiomas_disponibles if idioma not in idiomas_existentes]
        
        if not idiomas_faltantes:
            return jsonify({
                'valido': True,
                'disponible': False,
                'mensaje': f'Código ya existe en todos los idiomas (es, en)',
                'detalles': mensajes
            })
        
        return jsonify({
            'valido': True,
            'disponible': True,
            'mensaje': f'Código disponible para idiomas: {", ".join(idiomas_faltantes)}',
            'idiomas_faltantes': idiomas_faltantes,
            'detalles': mensajes
        })
        
    except Exception as e:
        app.logger.error(f"Error validando código: {e}")
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
@require_login(['admin', 'cajero', 'admin_restaurante'])
def obtener_estadisticas_dashboard():
    """Obtener estadísticas principales para el dashboard"""
    connection = None
    cursor = None
    try:
        fecha_inicio = request.args.get('fecha_inicio', get_colombia_time().strftime('%Y-%m-%d'))
        fecha_fin = request.args.get('fecha_fin', get_colombia_time().strftime('%Y-%m-%d'))
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        # 1. Ingresos totales en el período
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
        
        # 2. Máquinas activas vs total
        cursor.execute("""
            SELECT 
                COUNT(CASE WHEN status = 'activa' THEN 1 END) as maquinas_activas,
                COUNT(*) as maquinas_totales
            FROM machine
        """)
        
        maquinas = cursor.fetchone()
        
        # 3. Ticket promedio
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
        
        # 4. Comparación con período anterior para tendencias
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
        
        # Calcular tendencias porcentuales
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
        app.logger.error(f"Error obteniendo estadísticas dashboard: {e}")
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
    """Obtener datos para gráficas del dashboard"""
    connection = None
    cursor = None
    try:
        fecha_inicio = request.args.get('fecha_inicio', get_colombia_time().strftime('%Y-%m-%d'))
        fecha_fin = request.args.get('fecha_fin', get_colombia_time().strftime('%Y-%m-%d'))
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        # 1. Evolución de ventas por día
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
        
        # 2. Ventas por paquete
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
        
        # 3. Rendimiento por máquina
        cursor.execute("""
            SELECT 
                m.name as maquina,
                COUNT(DISTINCT qh.qr_code) as ventas,
                COALESCE(SUM(tp.price), 0) as ingresos,
                COUNT(DISTINCT tu.id) as usos
            FROM machine m
            LEFT JOIN turnusage tu ON tu.machineId = m.id AND DATE(tu.usedAt) BETWEEN %s AND %s
            LEFT JOIN qrhistory qh ON DATE(qh.fecha_hora) BETWEEN %s AND %s
            LEFT JOIN qrcode qr ON qr.code = qh.qr_code
            LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE qh.fecha_hora IS NOT NULL
            GROUP BY m.id, m.name
            ORDER BY ingresos DESC
            LIMIT 10
        """, (fecha_inicio, fecha_fin, fecha_inicio, fecha_fin))
        
        maquinas_data = cursor.fetchall()
        
        rendimiento_maquinas = {
            'labels': [item['maquina'] for item in maquinas_data],
            'data': [float(item['ingresos']) for item in maquinas_data]
        }
        
        # 4. Estado de máquinas
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
        app.logger.error(f"Error obteniendo gráficas dashboard: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/dashboard/top-maquinas', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
def obtener_top_maquinas():
    """Obtener top 5 máquinas por rendimiento"""
    connection = None
    cursor = None
    try:
        fecha_hoy = get_colombia_time().strftime('%Y-%m-%d')
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("""
            SELECT 
                m.name as nombre,
                COUNT(DISTINCT qh.qr_code) as ventas,
                COALESCE(SUM(tp.price), 0) as ingresos,
                COUNT(DISTINCT tu.id) as usos
            FROM machine m
            LEFT JOIN machine tu ON tu.machineId = m.id AND DATE(tu.usedAt) = %s
            LEFT JOIN qrhistory qh ON DATE(qh.fecha_hora) = %s
            LEFT JOIN qrcode qr ON qr.code = qh.qr_code
            LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE qh.fecha_hora IS NOT NULL
            GROUP BY m.id, m.name
            ORDER BY ingresos DESC
            LIMIT 5
        """, (fecha_hoy, fecha_hoy))
        
        top_maquinas = cursor.fetchall()
        
        # Formatear respuesta
        maquinas_formateadas = []
        for maquina in top_maquinas:
            maquinas_formateadas.append({
                'nombre': maquina['nombre'],
                'ventas': maquina['ventas'] or 0,
                'ingresos': float(maquina['ingresos'] or 0),
                'usos': maquina['usos'] or 0
            })
        
        return jsonify(maquinas_formateadas)
        
    except Exception as e:
        app.logger.error(f"Error obteniendo top máquinas: {e}")
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
    """Obtener las últimas 5 ventas"""
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
            LIMIT 5
        """)
        
        ventas = cursor.fetchall()
        
        # Formatear respuesta
        ventas_formateadas = []
        for venta in ventas:
            # Formatear fecha/hora
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
#@validate_required_fields(['isActive'])
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
        
        # Verificar que el usuario existe
        cursor.execute("SELECT name FROM users WHERE id = %s", (usuario_id,))
        usuario = cursor.fetchone()
        if not usuario:
            return api_response('U001', http_status=404, data={'usuario_id': usuario_id})
        
        # Actualizar estado
        cursor.execute("""
            UPDATE users 
            SET isActive = %s,
                updatedAt = NOW()
            WHERE id = %s
        """, (is_active, usuario_id))
        
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

# ==================== APIS PARA ESTADÍSTICAS DE USUARIOS ====================

@app.route('/api/usuarios/estadisticas', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_estadisticas_usuarios():
    """Obtener estadísticas de usuarios"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        # Obtener conteos por rol y estado
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
        app.logger.error(f"Error obteniendo estadísticas de usuarios: {e}")
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
        return "Esto no debería mostrarse"
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return f"✅ Error capturado y enviado a Sentry: {str(e)}"

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

# ==================== RUTAS PARA ESP32 Y TFT ====================

@app.route('/api/esp32/status', methods=['GET'])
def esp32_status():
    """Endpoint para verificar estado del servidor desde ESP32"""
    return jsonify({
        'status': 'online',
        'message': 'Servidor funcionando correctamente',
        'timestamp': get_colombia_time().isoformat()
    })

@app.route('/api/esp32/registrar-uso', methods=['POST'])
@handle_api_errors
@validate_required_fields(['qr_code', 'machine_id'])
def esp32_registrar_uso():
    """Registrar uso de máquina desde ESP32"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        qr_code = data['qr_code']
        machine_id = data['machine_id']
        
        app.logger.info(f"ESP32: Registrando uso - QR: {qr_code}, Máquina: {machine_id}")
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        # Verificar que el QR existe y tiene turnos
        cursor.execute("SELECT id FROM qrcode WHERE code = %s", (qr_code,))
        qr_data = cursor.fetchone()
        if not qr_data:
            return api_response('Q001', http_status=404)
        
        qr_id = qr_data['id']
        cursor.execute("SELECT turns_remaining FROM userturns WHERE qr_code_id = %s", (qr_id,))
        turnos_data = cursor.fetchone()
        
        if not turnos_data or turnos_data['turns_remaining'] <= 0:
            return api_response('Q003', http_status=400)
        
        # Registrar uso
        cursor.execute("INSERT INTO turnusage (qrCodeId, machineId) VALUES (%s, %s)", (qr_id, machine_id))
        cursor.execute("UPDATE userturns SET turns_remaining = turns_remaining - 1 WHERE qr_code_id = %s", (qr_id,))
        
        # Actualizar última fecha de uso de la máquina
        cursor.execute("UPDATE machine SET dateLastQRUsed = NOW() WHERE id = %s", (machine_id,))
        
        connection.commit()
        
        # Obtener información actualizada
        cursor.execute("""
            SELECT ut.turns_remaining, tp.name as package_name 
            FROM userturns ut 
            JOIN qrcode qr ON qr.id = ut.qr_code_id
            LEFT JOIN turnpackage tp ON ut.package_id = tp.id
            WHERE ut.qr_code_id = %s
        """, (qr_id,))
        
        info_actualizada = cursor.fetchone()
        
        app.logger.info(f"ESP32: Uso registrado - QR: {qr_code}, Turnos restantes: {info_actualizada['turns_remaining']}")
        
        return api_response(
            'S010',
            status='success',
            data={
                'turns_remaining': info_actualizada['turns_remaining'],
                'package_name': info_actualizada['package_name'],
                'machine_id': machine_id
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

@app.route('/api/tft/machine-status/<machine_id>', methods=['GET'])
def tft_machine_status(machine_id):
    """Obtener estado de máquina para pantalla TFT"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión'}), 500
            
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
                'location': 'Sin ubicación',
                'usos_hoy': 0,
                'message': 'Máquina no registrada'
            }), 200
        
        # Determinar mensaje según estado
        status_messages = {
            'activa': 'Disponible para jugar',
            'mantenimiento': 'En mantenimiento',
            'inactiva': 'Máquina desactivada'
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
        app.logger.error(f"Error estado máquina TFT: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# ==================== RUTAS DE REDIRECCIÓN ====================

@app.route('/admin/usuarios/lista')
def mostrar_lista_usuarios():
    """Redirigir a la gestión de usuarios"""
    return redirect(url_for('mostrar_gestion_usuarios'))

@app.route('/admin/paquetes/lista')
def mostrar_lista_paquetes():
    """Redirigir a la gestión de paquetes"""
    return redirect(url_for('mostrar_gestion_paquetes'))

@app.route('/admin/locales/listalocales')
def mostrar_lista_locales():
    """Redirigir a la gestión de locales"""
    return redirect(url_for('mostrar_gestion_locales'))

@app.route('/admin/maquinas/inventario')
def mostrar_inventario_maquinas():
    """Redirigir a la gestión de máquinas"""
    return redirect(url_for('mostrar_gestion_maquinas'))

# ==================== APIS PARA CONTADORES GLOBALES ====================

@app.route('/api/contador-global-vendidos', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
def obtener_contador_global_vendidos():
    """Obtener contador global de QR vendidos (con paquetes)"""
    connection = None
    cursor = None
    try:
        fecha = request.args.get('fecha', get_colombia_time().strftime('%Y-%m-%d'))
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        # Obtener QR vendidos (con paquetes) hoy
        cursor.execute("""
            SELECT COUNT(DISTINCT qh.qr_code) as total_vendidos
            FROM qrhistory qh
            JOIN qrcode qr ON qr.code = qh.qr_code
            WHERE DATE(qh.fecha_hora) = %s
            AND qr.turnPackageId IS NOT NULL
            AND qr.turnPackageId != 1
        """, (fecha,))
        
        resultado = cursor.fetchone()
        
        # Obtener total de ventas del día
        cursor.execute("""
            SELECT COALESCE(SUM(tp.price), 0) as valor_total
            FROM qrhistory qh
            JOIN qrcode qr ON qr.code = qh.qr_code
            JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) = %s
            AND qr.turnPackageId IS NOT NULL
            AND qr.turnPackageId != 1
        """, (fecha,))
        
        ventas_resultado = cursor.fetchone()
        
        app.logger.info(f"Contador global vendidos: {resultado['total_vendidos'] or 0} QR vendidos hoy")
        
        return jsonify({
            'total_vendidos': resultado['total_vendidos'] or 0,
            'valor_total': float(ventas_resultado['valor_total'] or 0),
            'fecha': fecha,
            'timestamp': get_colombia_time().isoformat()
        })
        
    except Exception as e:
        app.logger.error(f"Error obteniendo contador global vendidos: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/contador-global-escaneados', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
def obtener_contador_global_escaneados():
    """Obtener contador global de QR escaneados (todos)"""
    connection = None
    cursor = None
    try:
        fecha = request.args.get('fecha', get_colombia_time().strftime('%Y-%m-%d'))
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        # Obtener total QR escaneados hoy
        cursor.execute("""
            SELECT COUNT(*) as total_escaneados
            FROM qrhistory
            WHERE DATE(fecha_hora) = %s
        """, (fecha,))
        
        resultado = cursor.fetchone()
        
        # Obtener desglose por tipo
        cursor.execute("""
            SELECT 
                COUNT(CASE WHEN qr.turnPackageId IS NOT NULL AND qr.turnPackageId != 1 THEN 1 END) as con_paquete,
                COUNT(CASE WHEN qr.turnPackageId IS NULL OR qr.turnPackageId = 1 THEN 1 END) as sin_paquete
            FROM qrhistory qh
            LEFT JOIN qrcode qr ON qr.code = qh.qr_code
            WHERE DATE(qh.fecha_hora) = %s
        """, (fecha,))
        
        desglose = cursor.fetchone()
        
        app.logger.info(f"Contador global escaneados: {resultado['total_escaneados'] or 0} QR escaneados hoy")
        
        return jsonify({
            'total_escaneados': resultado['total_escaneados'] or 0,
            'con_paquete': desglose['con_paquete'] or 0,
            'sin_paquete': desglose['sin_paquete'] or 0,
            'fecha': fecha,
            'timestamp': get_colombia_time().isoformat()
        })
        
    except Exception as e:
        app.logger.error(f"Error obteniendo contador global escaneados: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/contador-global-turnos', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
def obtener_contador_global_turnos():
    """Obtener contador global de turnos utilizados"""
    connection = None
    cursor = None
    try:
        fecha = request.args.get('fecha', get_colombia_time().strftime('%Y-%m-%d'))
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        # Obtener turnos utilizados hoy
        cursor.execute("""
            SELECT COUNT(*) as turnos_utilizados
            FROM turnusage
            WHERE DATE(usedAt) = %s
        """, (fecha,))
        
        resultado = cursor.fetchone()
        
        # Obtener turnos por máquina
        cursor.execute("""
            SELECT 
                m.name as maquina_nombre,
                COUNT(tu.id) as turnos
            FROM turnusage tu
            JOIN machine m ON tu.machineId = m.id
            WHERE DATE(tu.usedAt) = %s
            GROUP BY m.id, m.name
            ORDER BY turnos DESC
        """, (fecha,))
        
        por_maquina = cursor.fetchall()
        
        app.logger.info(f"Contador global turnos: {resultado['turnos_utilizados'] or 0} turnos utilizados hoy")
        
        return jsonify({
            'turnos_utilizados': resultado['turnos_utilizados'] or 0,
            'por_maquina': por_maquina,
            'fecha': fecha,
            'timestamp': get_colombia_time().isoformat()
        })
        
    except Exception as e:
        app.logger.error(f"Error obteniendo contador global turnos: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/contador-global-resumen', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
def obtener_contador_global_resumen():
    """Obtener resumen completo de contadores globales"""
    connection = None
    cursor = None
    try:
        fecha = request.args.get('fecha', get_colombia_time().strftime('%Y-%m-%d'))
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        # 1. QR vendidos y valor
        cursor.execute("""
            SELECT 
                COUNT(DISTINCT qh.qr_code) as total_vendidos,
                COALESCE(SUM(tp.price), 0) as valor_total
            FROM qrhistory qh
            JOIN qrcode qr ON qr.code = qh.qr_code
            LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) = %s
            AND qr.turnPackageId IS NOT NULL
            AND qr.turnPackageId != 1
        """, (fecha,))
        
        ventas = cursor.fetchone()
        
        # 2. QR escaneados total
        cursor.execute("""
            SELECT COUNT(*) as total_escaneados
            FROM qrhistory
            WHERE DATE(fecha_hora) = %s
        """, (fecha,))
        
        escaneados = cursor.fetchone()
        
        # 3. Turnos utilizados
        cursor.execute("""
            SELECT COUNT(*) as turnos_utilizados
            FROM turnusage
            WHERE DATE(usedAt) = %s
        """, (fecha,))
        
        turnos = cursor.fetchone()
        
        # 4. Fallas reportadas
        cursor.execute("""
            SELECT COUNT(*) as fallas_reportadas
            FROM machinefailures
            WHERE DATE(reported_at) = %s
        """, (fecha,))
        
        fallas = cursor.fetchone()
        
        # 5. Reportes de máquinas
        cursor.execute("""
            SELECT COUNT(*) as reportes_maquinas
            FROM errorreport
            WHERE DATE(reportedAt) = %s
        """, (fecha,))
        
        reportes = cursor.fetchone()
        
        app.logger.info(f"Resumen global: {ventas['total_vendidos'] or 0} vendidos, {turnos['turnos_utilizados'] or 0} turnos")
        
        return jsonify({
            'fecha': fecha,
            'ventas': {
                'total_vendidos': ventas['total_vendidos'] or 0,
                'valor_total': float(ventas['valor_total'] or 0)
            },
            'escaneados': {
                'total_escaneados': escaneados['total_escaneados'] or 0
            },
            'turnos': {
                'turnos_utilizados': turnos['turnos_utilizados'] or 0
            },
            'fallas': {
                'fallas_reportadas': fallas['fallas_reportadas'] or 0
            },
            'reportes': {
                'reportes_maquinas': reportes['reportes_maquinas'] or 0
            },
            'timestamp': get_colombia_time().isoformat()
        })
        
    except Exception as e:
        app.logger.error(f"Error obteniendo resumen global: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# ==================== APIS PARA ESTADÍSTICAS HISTÓRICAS ====================

@app.route('/api/estadisticas/rango-fechas', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
def obtener_estadisticas_rango_fechas():
    """Obtener estadísticas por rango de fechas"""
    connection = None
    cursor = None
    try:
        fecha_inicio = request.args.get('fecha_inicio', get_colombia_time().strftime('%Y-%m-%d'))
        fecha_fin = request.args.get('fecha_fin', get_colombia_time().strftime('%Y-%m-%d'))
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        # Estadísticas por día en el rango
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
        
        # Máquinas más utilizadas en el rango
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
        
        # Paquetes más vendidos en el rango
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
        
        app.logger.info(f"Estadísticas rango {fecha_inicio} a {fecha_fin}: {totales['total_vendidos'] or 0} vendidos")
        
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
        app.logger.error(f"Error obteniendo estadísticas por rango: {e}")
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
        
        # Estadísticas de hoy
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
        
        # Estadísticas de ayer
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
        
        # Máquinas activas/inactivas
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
        
        # Últimas ventas (5)
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

# ==================== FUNCIÓN PARA ACTUALIZAR CONTADORES DIARIOS ====================

def actualizar_contador_diario(fecha=None):
    """Actualizar o crear registro de contador diario"""
    if fecha is None:
        fecha = get_colombia_time().strftime('%Y-%m-%d')
    
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return False
            
        cursor = get_db_cursor(connection)
        
        # Verificar si existe tabla ContadorDiario, si no crearla
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
        
        cursor.execute("""
            INSERT INTO ContadorDiario (fecha, qr_vendidos, valor_ventas, qr_escaneados, turnos_utilizados, fallas_reportadas)
            SELECT 
                %s as fecha,
                COUNT(DISTINCT CASE WHEN qr.turnPackageId IS NOT NULL AND qr.turnPackageId != 1 THEN qh.qr_code END) as qr_vendidos,
                COALESCE(SUM(CASE WHEN qr.turnPackageId IS NOT NULL AND qr.turnPackageId != 1 THEN tp.price END), 0) as valor_ventas,
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

# ==================== RUTA PARA ACTUALIZAR CONTADOR DIARIO ====================

@app.route('/api/actualizar-contador-diario', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
def api_actualizar_contador_diario():
    """Forzar actualización del contador diario"""
    try:
        data = request.get_json()
        fecha = data.get('fecha', get_colombia_time().strftime('%Y-%m-%d'))
        
        resultado = actualizar_contador_diario(fecha)
        
        if resultado:
            return api_response('S003', status='success', data={
                'mensaje': f'Contador diario actualizado para {fecha}',
                'fecha': fecha
            })
        else:
            return api_response('E001', http_status=500, data={'mensaje': 'Error actualizando contador'})
            
    except Exception as e:
        app.logger.error(f"Error en API de actualización contador diario: {e}")
        return api_response('E001', http_status=500)

# ==================== APIS PARA REPARACIÓN DE BD TEMPORAL ====================

@app.route('/api/reparar-bd-temporal', methods=['GET'])
@handle_api_errors
def reparar_bd_temporal():
    """Reparar tablas temporales si es necesario"""
    try:
        # Esto es solo un placeholder - ajusta según tus necesidades
        
        return api_response(
            'S001',
            status='success',
            data={'message': 'Base de datos verificada correctamente'}
        )
    except Exception as e:
        app.logger.error(f"Error reparando BD: {e}")
        return api_response('E001', http_status=500)

@app.route('/api/reportar-falla-funcional', methods=['POST'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
@validate_required_fields(['machine_id', 'description'])
def reportar_falla_funcional():
    """Alternativa simplificada para reportar falla"""
    # Reutilizar la función existente
    return reportar_falla_maquina()

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
        
        # Verificar si tiene máquinas asociadas
        cursor.execute("SELECT COUNT(*) as count FROM MaquinaPropietario WHERE propietario_id = %s", (propietario_id,))
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

# ==================== APIS PARA REPORTES DE MÁQUINAS ====================

@app.route('/api/maquinas/<int:maquina_id>/reportes', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_reportes_maquina(maquina_id):
    """Obtener reportes de fallas de una máquina específica"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        # Verificar que la máquina existe
        cursor.execute("SELECT name FROM machine WHERE id = %s", (maquina_id,))
        maquina = cursor.fetchone()
        if not maquina:
            return api_response('M001', http_status=404, data={'machine_id': maquina_id})
        
        # Obtener reportes de la máquina
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
        app.logger.error(f"Error obteniendo reportes de máquina: {e}")
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
    """Obtener estadísticas de una máquina"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        # Verificar que la máquina existe
        cursor.execute("SELECT name, status FROM machine WHERE id = %s", (maquina_id,))
        maquina = cursor.fetchone()
        if not maquina:
            return api_response('M001', http_status=404, data={'machine_id': maquina_id})
        
        # Estadísticas de uso
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
        
        # Usos por día (últimos 30 días)
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
        
        # Últimos reportes (5)
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
        app.logger.error(f"Error obteniendo estadísticas de máquina: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# ==================== APIS PARA GESTIÓN DE ROLES ====================

@app.route('/api/roles/sistema', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_roles_sistema():
    """Obtener los roles actuales del sistema desde la definición de la tabla"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        # Obtener definición de la columna role
        cursor.execute("""
            SELECT COLUMN_TYPE 
            FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_SCHEMA = DATABASE() 
            AND TABLE_NAME = 'users' 
            AND COLUMN_NAME = 'role'
        """)
        
        resultado = cursor.fetchone()
        
        if not resultado:
            return api_response('E002', http_status=404, data={'message': 'No se encontró la columna role'})
        
        # Extraer los valores del ENUM
        enum_str = resultado['COLUMN_TYPE']
        # El formato es: enum('valor1','valor2','valor3')
        roles = enum_str.replace("enum('", "").replace("')", "").replace("'", "").split(',')
        
        # Descripciones de los roles
        descripciones = {
            'admin': 'Acceso completo a todas las funciones del sistema',
            'cajero': 'Puede registrar ventas y gestionar códigos QR',
            'admin_restaurante': 'Gestión del restaurante y reportes específicos',
            'socio': 'Acceso a reportes financieros y estadísticas de inversión'
        }
        
        # Nombres amigables
        nombres = {
            'admin': 'Administrador',
            'cajero': 'Cajero',
            'admin_restaurante': 'Administrador Restaurante',
            'socio': 'Socio'
        }
        
        # Colores para UI
        colores = {
            'admin': 'purple',
            'cajero': 'blue',
            'admin_restaurante': 'teal',
            'socio': 'pink'
        }
        
        # Iconos
        iconos = {
            'admin': 'user-shield',
            'cajero': 'cash-register',
            'admin_restaurante': 'store',
            'socio': 'user-tie'
        }
        
        roles_detallados = []
        for rol in roles:
            roles_detallados.append({
                'id': rol,
                'nombre': nombres.get(rol, rol.capitalize()),
                'descripcion': descripciones.get(rol, 'Rol del sistema'),
                'color': colores.get(rol, 'gray'),
                'icono': iconos.get(rol, 'user'),
                'total_usuarios': 0
            })
        
        # Contar usuarios por rol
        for rol_info in roles_detallados:
            cursor.execute("SELECT COUNT(*) as count FROM users WHERE role = %s", (rol_info['id'],))
            count_result = cursor.fetchone()
            rol_info['total_usuarios'] = count_result['count'] if count_result else 0
        
        return jsonify({
            'roles': roles_detallados,
            'total_roles': len(roles),
            'timestamp': get_colombia_time().isoformat()
        })
        
    except Exception as e:
        app.logger.error(f"Error obteniendo roles del sistema: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/roles/agregar-automatico', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
def agregar_nuevo_rol_automatico():
    """Agregar nuevo rol automáticamente (PELIGROSO - solo para desarrollo)"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        nuevo_rol = data.get('nuevo_rol', '').strip().lower()
        
        if not nuevo_rol:
            return api_response('E005', http_status=400, data={'message': 'Nombre del rol requerido'})
        
        # Validar formato
        if not re.match(r'^[a-z_]+$', nuevo_rol):
            return api_response('E005', http_status=400, data={'message': 'Nombre inválido. Solo letras minúsculas y guiones bajos'})
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        # Obtener definición de la columna role
        cursor.execute("""
            SELECT COLUMN_TYPE 
            FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_SCHEMA = DATABASE() 
            AND TABLE_NAME = 'users' 
            AND COLUMN_NAME = 'role'
        """)
        
        resultado = cursor.fetchone()
        
        if not resultado:
            return api_response('E002', http_status=404, data={'message': 'No se encontró la columna role'})
        
        # Extraer roles actuales
        enum_str = resultado['COLUMN_TYPE']
        roles_actuales = enum_str.replace("enum('", "").replace("')", "").replace("'", "").split(',')
        
        # Verificar si el rol ya existe
        if nuevo_rol in roles_actuales:
            return api_response('E007', http_status=400, data={'message': 'El rol ya existe'})
        
        # Agregar nuevo rol a la lista
        roles_actuales.append(nuevo_rol)
        
        # Generar y ejecutar SQL automáticamente
        sql = f"ALTER TABLE users MODIFY COLUMN role ENUM('{','.join(roles_actuales)}') NOT NULL;"
        
        try:
            cursor.execute(sql)
            connection.commit()
            
            # Verificar que se aplicó
            cursor.execute("""
                SELECT COLUMN_TYPE 
                FROM INFORMATION_SCHEMA.COLUMNS 
                WHERE TABLE_SCHEMA = DATABASE() 
                AND TABLE_NAME = 'users' 
                AND COLUMN_NAME = 'role'
            """)
            
            resultado_final = cursor.fetchone()
            
            return jsonify({
                'success': True,
                'sql_ejecutado': sql,
                'nuevo_rol': nuevo_rol,
                'roles_actuales': roles_actuales,
                'message': f'Rol "{nuevo_rol}" agregado exitosamente a la base de datos',
                'advertencia': 'Se modificó la estructura de la tabla. Asegúrate de tener un backup.'
            })
            
        except mysql.connector.Error as db_error:
            connection.rollback()
            app.logger.error(f"Error SQL ejecutando ALTER TABLE: {db_error}")
            return api_response('E001', http_status=500, data={
                'message': f'Error de base de datos: {str(db_error)}',
                'sql': sql
            })
        
    except Exception as e:
        app.logger.error(f"Error agregando nuevo rol automático: {e}")
        if connection:
            connection.rollback()
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# ==================== RUTAS PARA SOCIOS ====================

@app.route('/socios')
@require_login(['admin', 'socio'])
def mostrar_panel_socio():
    """Mostrar panel personalizado del socio"""
    # Verificar si el usuario es socio o admin
    if session.get('user_role') == 'socio':
        # Cargar datos específicos del socio
        socio_id = session.get('socio_id')
    else:
        # Admin viendo panel general
        socio_id = request.args.get('socio_id')
    
    hora_colombia = get_colombia_time()
    return render_template('socios.html',
                         nombre_usuario=session.get('user_name', 'Socio'),
                         hora_actual=hora_colombia.strftime('%H:%M:%S'),
                         fecha_actual=hora_colombia.strftime('%Y-%m-%d'))

@app.route('/admin/inversores/gestionsocios')
@require_login(['admin'])
def mostrar_gestion_socios():
    """Mostrar gestión completa de socios"""
    hora_colombia = get_colombia_time()
    return render_template('admin/inversores/gestionsocios.html',
                         nombre_usuario=session.get('user_name', 'Administrador'),
                         local_usuario=session.get('user_local', 'Sistema'),
                         hora_actual=hora_colombia.strftime('%H:%M:%S'),
                         fecha_actual=hora_colombia.strftime('%Y-%m-%d'))

# ==================== APIS ESPECÍFICAS PARA SOCIOS ====================

@app.route('/api/socio/actual', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'socio'])
def obtener_socio_actual():
    """Obtener datos del socio actual (para su panel)"""
    connection = None
    cursor = None
    try:
        user_id = session.get('user_id')
        user_role = session.get('user_role')
        
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
            
        cursor = get_db_cursor(connection)
        
        if user_role == 'socio':
            # Buscar socio por user_id
            cursor.execute("SELECT * FROM socios WHERE user_id = %s", (user_id,))
        else:
            # Admin puede especificar socio
            socio_id = request.args.get('socio_id')
            cursor.execute("SELECT * FROM socios WHERE id = %s", (socio_id,))
        
        socio = cursor.fetchone()
        if not socio:
            return api_response('E002', http_status=404, data={'message': 'Socio no encontrado'})
        
        return jsonify(socio)
        
    except Exception as e:
        app.logger.error(f"Error obteniendo socio actual: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# ==================== INICIAR SERVIDOR ====================

if __name__ == '__main__':
    app.logger.info("🚀 Iniciando servidor Flask en http://127.0.0.1:5000")
    app.run(debug=True, port=5000, host='0.0.0.0')