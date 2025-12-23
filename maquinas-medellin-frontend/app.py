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

# ==================== CONFIGURACIÓN DE ZONA HORARIA ====================
# Configurar la zona horaria de Colombia
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

# Configuración del pool de conexiones CON ZONA HORARIA
try:
    db_config = {
        "host": "localhost",
        "user": "root",
        "password": "" , 
        "database": "base datos mm",
        "port": 3306,
        "pool_name": "maquinas_pool",
        "pool_size": 5
    }

    print("🔧 Intentando crear pool de conexiones...")
    print(f"   Host: {db_config['host']}")
    print(f"   User: {db_config['user']}")
    print(f"   Database: {db_config['database']}")
    print(f"   Port: {db_config['port']}")
    
    # Probar conexión simple primero
    test_conn = mysql.connector.connect(
        host=db_config["host"],
        user=db_config["user"], 
        password=db_config["password"],
        database=db_config["database"],
        port=db_config["port"]
    )
    print("✅ Conexión simple exitosa")
    test_conn.close()
    
    # Ahora intentar el pool
    connection_pool = pooling.MySQLConnectionPool(**db_config)
    print("✅ Pool de conexiones creado exitosamente")
    
except mysql.connector.Error as e:
    print(f"❌ Error MySQL específico: {e}")
    print(f"   Error number: {e.errno}")
    print(f"   SQL state: {e.sqlstate}")
    connection_pool = None
except Exception as e:
    print(f"❌ Error general creando pool: {e}")
    import traceback
    traceback.print_exc()
    connection_pool = None

# Función para obtener conexión CON ZONA HORARIA
def get_db_connection():
    try:
        if connection_pool:
            connection = connection_pool.get_connection()
            cursor = connection.cursor()
            cursor.execute("SET time_zone = '-05:00'")
            cursor.close()
            return connection
        else:
            # Conexión de respaldo con la MISMA contraseña
            connection = mysql.connector.connect(
                host="localhost",
                user="root",
                password="", 
                database="maquinasmedellin",
                port=3306
            )
            cursor = connection.cursor()
            cursor.execute("SET time_zone = '-05:00'")
            cursor.close()
            return connection
    except Exception as e:
        print(f"❌ Error obteniendo conexión: {e}")
        return None

# Función para obtener cursor
def get_db_cursor(connection):
    try:
        cursor = connection.cursor(dictionary=True)
        return cursor
    except Exception as e:
        print(f"❌ Error obteniendo cursor: {e}")
        return None

# Crear tablas si no existen (solo una vez al inicio)
def create_tables():
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return False
            
        cursor = get_db_cursor(connection)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS QRHistory (
                id INT AUTO_INCREMENT PRIMARY KEY,
                qr_code VARCHAR(255) NOT NULL,
                user_id INT NULL,
                user_name VARCHAR(100) NULL,
                local VARCHAR(100) NOT NULL,
                fecha_hora DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        connection.commit()
        print("✅ Tabla QRHistory verificada/creada")
        return True
    except Exception as e:
        print(f"❌ Error creando tabla QRHistory: {e}")
        return False
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# Crear tablas al iniciar
create_tables()

# Rutas
@app.route('/')
def mostrar_login():
    session.clear()
    return render_template('login.html')

# Procesa login
@app.route('/login', methods=['POST'])
def procesar_login():
    connection = None
    cursor = None
    try:
        data = request.get_json()
        codigo = data.get('codigo')
        print(f"📨 Código recibido: {codigo}")

        if not codigo:
            return jsonify({"valido": False, "error": "no_codigo"}), 400

        connection = get_db_connection()
        if not connection:
            return jsonify({"valido": False, "error": "db_connection"}), 500
            
        cursor = get_db_cursor(connection)
        cursor.execute("SELECT * FROM Users WHERE password = %s", (codigo,))
        usuario = cursor.fetchone()
        print(f"👤 Usuario encontrado: {usuario}")

        if usuario:
            session['user_id'] = usuario['id']
            session['user_name'] = usuario['name']
            session['user_role'] = usuario['role']
            session['user_local'] = usuario.get('local', 'El Mekatiadero')
            session['logged_in'] = True
            
            print(f"💾 Sesión creada: {session}")
            
            return jsonify({
                "valido": True,
                "nombre": usuario.get("name", "Usuario"),
                "role": usuario.get("role", "Cajero"),
                "local": usuario.get("local", "El Mekatiadero")
            }), 200
        else:
            return jsonify({"valido": False}), 200

    except Exception as e:
        print(f"❌ Error en /login: {e}")
        return jsonify({"valido": False, "error": "server_error", "message": str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# Interfaz principal
@app.route('/local')
def mostrar_local():
    if not session.get('logged_in'):
        return redirect(url_for('mostrar_login'))
    
    # Obtener hora actual de Colombia
    hora_colombia = get_colombia_time()
    
    return render_template('local.html',
                           nombre_usuario=session.get('user_name', 'Usuario'),
                           local_usuario=session.get('user_local', 'El Mekatiadero'),
                           hora_actual=hora_colombia.strftime('%H:%M:%S'),
                           fecha_actual=hora_colombia.strftime('%Y-%m-%d'))

# Interfaz paquetes
@app.route('/package')
def mostrar_package():
    if not session.get('logged_in'):
        return redirect(url_for('mostrar_login'))
    return render_template('package.html',
                           nombre_usuario=session.get('user_name', 'Usuario'),
                           local_usuario=session.get('user_local', 'El Mekatiadero'))

# Reporte de paquete
@app.route('/package/failure')
def mostrar_package_failure():
    if not session.get('logged_in'):
        return redirect(url_for('mostrar_login'))
    return render_template('packfailure.html',
                           nombre_usuario=session.get('user_name', 'Usuario'),
                           local_usuario=session.get('user_local', 'El Mekatiadero'))

# Reporte de máquina
@app.route('/machinereport')
def mostrar_machine_report():
    if not session.get('logged_in'):
        return redirect(url_for('mostrar_login'))
    return render_template('machinereport.html',
                           nombre_usuario=session.get('user_name', 'Usuario'),
                           local_usuario=session.get('user_local', 'El Mekatiadero'))

# Logout
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('mostrar_login'))

# Redireccionar Login.html
@app.route('/Login.html')
def redirect_login():
    return redirect('/')

# Debug
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

# Obtener paquetes
@app.route('/api/paquetes', methods=['GET'])
def obtener_paquetes():
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        cursor.execute("SELECT * FROM TurnPackage ORDER BY id")
        return jsonify(cursor.fetchall())
    except Exception as e:
        print(f"❌ Error obteniendo paquetes: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# Asignar paquete a QR 
@app.route('/api/asignar-paquete', methods=['POST'])
def asignar_paquete():
    connection = None
    cursor = None
    try:
        data = request.get_json()
        codigo_qr = data.get('codigo_qr')
        paquete_id = data.get('paquete_id')
        
        if not codigo_qr or not paquete_id:
            return jsonify({'error': 'Faltan datos requeridos'}), 400
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        # PRIMERO: Verificar si el QR ya tiene un paquete asignado (y no es el paquete por defecto)
        cursor.execute("SELECT turnPackageId FROM QRCode WHERE code = %s", (codigo_qr,))
        qr_existente = cursor.fetchone()
        
        if qr_existente and qr_existente['turnPackageId'] is not None and qr_existente['turnPackageId'] != 1:
            # El QR ya tiene un paquete asignado (que no es el paquete por defecto)
            cursor.execute("SELECT name FROM TurnPackage WHERE id = %s", (qr_existente['turnPackageId'],))
            paquete_actual = cursor.fetchone()
            paquete_nombre = paquete_actual['name'] if paquete_actual else 'Desconocido'
            
            return jsonify({
                'error': True,
                'message': f'Este QR ya tiene asignado el paquete "{paquete_nombre}". No se pueden asignar más paquetes.',
                'paquete_actual': paquete_nombre
            }), 400
        
        cursor.execute("SELECT turns, price FROM TurnPackage WHERE id = %s", (paquete_id,))
        paquete = cursor.fetchone()
        if not paquete:
            return jsonify({'error': 'Paquete no encontrado'}), 404
        
        turns, price = paquete['turns'], paquete['price']

        cursor.execute("SELECT id FROM QRCode WHERE code = %s", (codigo_qr,))
        qr_existente = cursor.fetchone()
        
        if not qr_existente:
            # QR nuevo, asignar paquete
            cursor.execute("""
                INSERT INTO QRCode (code, remainingTurns, isActive, turnPackageId)
                VALUES (%s, %s, 1, %s)
            """, (codigo_qr, turns, paquete_id))
            connection.commit()
            qr_id = cursor.lastrowid
        else:
            # QR existente, verificar si puede recibir el paquete
            qr_id = qr_existente['id']
            
            # Si el QR no tiene paquete o tiene el paquete por defecto (ID 1), asignar el nuevo
            cursor.execute("""
                UPDATE QRCode
                SET remainingTurns = remainingTurns + %s,
                    turnPackageId = %s
                WHERE id = %s
            """, (turns, paquete_id, qr_id))
            connection.commit()
        
        # Actualizar o crear en UserTurns
        cursor.execute("""
            INSERT INTO UserTurns (qr_code_id, turns_remaining, total_turns, package_id)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE 
                turns_remaining = turns_remaining + %s,
                total_turns = total_turns + %s,
                package_id = %s
        """, (qr_id, turns, turns, paquete_id, turns, turns, paquete_id))
        
        connection.commit()
        
        return jsonify({
            'success': True,
            'message': f'Paquete P{paquete_id} asignado correctamente',
            'turns': turns,
            'price': price,
            'qr_id': qr_id
        })
        
    except Exception as e:
        print(f"❌ Error asignando paquete: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# Verificar QR
@app.route('/api/verificar-qr/<qr_code>', methods=['GET'])
def verificar_qr(qr_code):
    connection1 = None
    connection2 = None
    cursor1 = None
    cursor2 = None
    try:
        print(f"🔍 Verificando QR: {qr_code}")
        
        # PRIMERA CONEXIÓN: Verificar si el QR existe
        connection1 = get_db_connection()
        if not connection1:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor1 = get_db_cursor(connection1)
        cursor1.execute("SELECT id, code, remainingTurns, isActive, turnPackageId FROM QRCode WHERE code = %s", (qr_code,))
        qr_data = cursor1.fetchone()
        
        if not qr_data:
            print(f"❌ QR no encontrado en tabla QRCode: {qr_code}")
            return jsonify({'existe': False})
        
        print(f"✅ QR encontrado en base de datos: {qr_data}")
        
        qr_id = qr_data['id']
        tiene_paquete = qr_data['turnPackageId'] is not None and qr_data['turnPackageId'] != 1
        
        # Cerrar completamente la primera conexión
        cursor1.close()
        connection1.close()
        
        # SEGUNDA CONEXIÓN: Obtener información de turnos
        connection2 = get_db_connection()
        if not connection2:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor2 = get_db_cursor(connection2)
        cursor2.execute("""
            SELECT ut.*, tp.name as package_name, tp.turns, tp.price
            FROM UserTurns ut
            LEFT JOIN TurnPackage tp ON ut.package_id = tp.id
            WHERE ut.qr_code_id = %s
        """, (qr_id,))
        resultado = cursor2.fetchone()
        
        if resultado:
            response_data = {
                'existe': True,
                'tiene_paquete': tiene_paquete,
                'turns_remaining': resultado['turns_remaining'],
                'total_turns': resultado['total_turns'],
                'package_name': resultado['package_name'],
                'package_turns': resultado['turns'],
                'package_price': resultado['price'],
                'qr_code': qr_code,
                'turnPackageId': qr_data['turnPackageId']
            }
        else:
            response_data = {
                'existe': True,
                'tiene_paquete': tiene_paquete,
                'turns_remaining': 0,
                'total_turns': 0,
                'package_name': 'Sin paquete',
                'package_turns': 0,
                'package_price': 0,
                'qr_code': qr_code,
                'turnPackageId': qr_data['turnPackageId']
            }
        
        return jsonify(response_data)
            
    except Exception as e:
        print(f"❌ Error verificando QR: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        # Asegurarse de cerrar todo
        if cursor1:
            try:
                cursor1.close()
            except:
                pass
        if connection1:
            try:
                connection1.close()
            except:
                pass
        if cursor2:
            try:
                cursor2.close()
            except:
                pass
        if connection2:
            try:
                connection2.close()
            except:
                pass

# Registrar uso turno
@app.route('/api/registrar-uso', methods=['POST'])
def registrar_uso():
    connection = None
    cursor = None
    try:
        data = request.get_json()
        qr_code = data.get('qr_code')
        machine_id = data.get('machine_id')
        
        if not qr_code or not machine_id:
            return jsonify({'error': 'Faltan datos requeridos'}), 400
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("SELECT id FROM QRCode WHERE code = %s", (qr_code,))
        qr_data = cursor.fetchone()
        if not qr_data:
            return jsonify({'error': 'Código QR no encontrado'}), 404
        
        qr_id = qr_data['id']
        cursor.execute("SELECT turns_remaining FROM UserTurns WHERE qr_code_id = %s", (qr_id,))
        turnos_data = cursor.fetchone()
        
        if not turnos_data or turnos_data['turns_remaining'] <= 0:
            return jsonify({'error': 'No hay turnos disponibles'}), 400
        
        cursor.execute("INSERT INTO TurnUsage (qrCodeId, machineId) VALUES (%s, %s)", (qr_id, machine_id))
        cursor.execute("UPDATE UserTurns SET turns_remaining = turns_remaining - 1 WHERE qr_code_id = %s", (qr_id,))
        connection.commit()
        
        return jsonify({
            'success': True,
            'message': 'Turno utilizado correctamente',
            'turns_remaining': turnos_data['turns_remaining'] - 1
        })
        
    except Exception as e:
        print(f"❌ Error registrando uso: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# Reportar falla
@app.route('/api/reportar-falla', methods=['POST'])
def reportar_falla():
    connection = None
    cursor = None
    try:
        data = request.get_json()
        qr_code = data.get('qr_code')
        machine_id = data.get('machine_id')
        machine_name = data.get('machine_name')
        turnos_devueltos = data.get('turnos_devueltos')
        
        if not all([qr_code, machine_id, turnos_devueltos]):
            return jsonify({'error': 'Faltan datos requeridos'}), 400
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("SELECT id FROM QRCode WHERE code = %s", (qr_code,))
        qr_data = cursor.fetchone()
        if not qr_data:
            return jsonify({'error': 'Código QR no encontrado'}), 404
        
        qr_id = qr_data['id']
        cursor.execute("SELECT turns_remaining FROM UserTurns WHERE qr_code_id = %s", (qr_id,))
        turnos_data = cursor.fetchone()
        if not turnos_data:
            return jsonify({'error': 'No hay turnos asignados a este QR'}), 400
        
        cursor.execute("""
            INSERT INTO MachineFailures (qr_code_id, machine_id, machine_name, turnos_devueltos)
            VALUES (%s, %s, %s, %s)
        """, (qr_id, machine_id, machine_name, turnos_devueltos))
        
        cursor.execute("UPDATE UserTurns SET turns_remaining = turns_remaining + %s WHERE qr_code_id = %s",
                       (turnos_devueltos, qr_id))
        connection.commit()
        
        return jsonify({
            'success': True,
            'message': f'Falla reportada y {turnos_devueltos} turnos devueltos correctamente',
            'nuevos_turnos': turnos_data['turns_remaining'] + turnos_devueltos
        })
        
    except Exception as e:
        print(f"❌ Error reportando falla: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# Historial fallas
@app.route('/api/historial-fallas', methods=['GET'])
def obtener_historial_fallas():
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        cursor.execute("""
            SELECT mf.*, qr.code as qr_code, ut.turns_remaining, ut.total_turns
            FROM MachineFailures mf
            JOIN QRCode qr ON mf.qr_code_id = qr.id
            JOIN UserTurns ut ON mf.qr_code_id = ut.qr_code_id
            ORDER BY mf.reported_at DESC
            LIMIT 50
        """)
        return jsonify(cursor.fetchall())
    except Exception as e:
        print(f"❌ Error obteniendo historial de fallas: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# Guardar QR en historial
@app.route('/api/guardar-qr', methods=['POST'])
def guardar_qr():
    connection = None
    cursor = None
    try:
        data = request.get_json()
        qr_code = data.get('qr_code')
        user_id = session.get('user_id')
        user_name = session.get('user_name', 'Usuario')
        local = session.get('user_local', 'El Mekatiadero')
        
        es_venta_real = data.get('es_venta_real', False)  # False por defecto

        if not qr_code:
            return jsonify({'error': 'QR vacío'}), 400

        print(f"💾 Guardando QR en historial: {qr_code} por usuario {user_name}, es_venta_real: {es_venta_real}")

        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)

        # Usar hora actual de Colombia
        hora_colombia = get_colombia_time()
        
        # Buscar si el QR tiene un nombre asociado y si tiene paquete
        cursor.execute("SELECT qr_name, turnPackageId FROM QRCode WHERE code = %s", (qr_code,))
        qr_data = cursor.fetchone()
        qr_name = qr_data['qr_name'] if qr_data and 'qr_name' in qr_data else None
        
        es_venta = es_venta_real and qr_data and qr_data['turnPackageId'] is not None and qr_data['turnPackageId'] != 1
        
        cursor.execute("""
            INSERT INTO QRHistory (qr_code, user_id, user_name, local, fecha_hora, qr_name)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (qr_code, user_id, user_name, local, format_datetime_for_db(hora_colombia), qr_name))
        connection.commit()

        return jsonify({
            'success': True, 
            'message': 'QR guardado en historial', 
            'qr_name': qr_name,
            'es_venta': es_venta
        })
    except Exception as e:
        print(f"❌ Error guardando QR en historial: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# Agregar QR generados en lote al historial
@app.route('/api/guardar-multiples-qr', methods=['POST'])
def guardar_multiples_qr():
    connection = None
    cursor = None
    try:
        data = request.get_json()
        qr_codes = data.get('qr_codes', [])
        nombre = data.get('nombre', '')
        user_id = session.get('user_id')
        user_name = session.get('user_name', 'Usuario')
        local = session.get('user_local', 'El Mekatiadero')

        if not qr_codes:
            return jsonify({'error': 'Lista de QR vacía'}), 400

        print(f"💾 Guardando {len(qr_codes)} QR en el sistema con nombre: {nombre}")

        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)

        # Usar hora actual de Colombia
        hora_colombia = get_colombia_time()
        fecha_hora_str = format_datetime_for_db(hora_colombia)

        for qr_code in qr_codes:
            cursor.execute("SELECT id FROM QRCode WHERE code = %s", (qr_code,))
            qr_existente = cursor.fetchone()
            
            if not qr_existente:
                print(f"➕ Insertando nuevo QR: {qr_code} con nombre: {nombre}")
                cursor.execute("""
                    INSERT INTO QRCode (code, remainingTurns, isActive, turnPackageId, qr_name)
                    VALUES (%s, %s, %s, %s, %s)
                """, (qr_code, 0, 1, 1, nombre))
            else:
                print(f"✅ QR ya existe: {qr_code}")
                # Actualizar el nombre si ya existe
                cursor.execute("""
                    UPDATE QRCode SET qr_name = %s WHERE code = %s
                """, (nombre, qr_code))
            
            cursor.execute("""
                INSERT INTO QRHistory (qr_code, user_id, user_name, local, fecha_hora, qr_name)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (qr_code, user_id, user_name, local, fecha_hora_str, nombre))

        connection.commit()
        print(f"✅ {len(qr_codes)} QR guardados exitosamente con nombre: {nombre}")

        return jsonify({
            'success': True, 
            'message': f'{len(qr_codes)} QR guardados en el sistema',
            'count': len(qr_codes),
            'nombre': nombre
        })
        
    except Exception as e:
        print(f"❌ Error guardando múltiples QR: {e}")
        sentry_sdk.capture_exception(e)
        if connection:
            connection.rollback()
        return jsonify({'error': str(e), 'message': 'Error al guardar los códigos QR'}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/debug-qr/<qr_code>', methods=['GET'])
def debug_qr(qr_code):
    connection = None
    cursor = None
    try:
        print(f"🔧 Debug QR: {qr_code}")
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("SELECT * FROM QRCode WHERE code = %s", (qr_code,))
        qr_data = cursor.fetchone()
        print(f"📊 QR en tabla QRCode: {qr_data}")
        
        cursor.execute("SELECT * FROM QRHistory WHERE qr_code = %s ORDER BY fecha_hora DESC", (qr_code,))
        history_data = cursor.fetchall()
        print(f"📊 QR en historial: {history_data}")
        
        if qr_data:
            cursor.execute("SELECT * FROM UserTurns WHERE qr_code_id = %s", (qr_data['id'],))
            turns_data = cursor.fetchone()
            print(f"📊 QR en UserTurns: {turns_data}")
        
        return jsonify({
            'qr_code': qr_code,
            'en_qrcode': bool(qr_data),
            'en_historial': len(history_data) > 0,
            'datos_qrcode': qr_data,
            'historial': history_data
        })
        
    except Exception as e:
        print(f"❌ Error en debug: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# Contador global de QR
@app.route('/api/contador-global', methods=['GET'])
def contador_global():
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        cursor.execute("SELECT COUNT(*) as total_qr FROM QRCode")
        resultado = cursor.fetchone()
        return jsonify({'total_qr': resultado['total_qr']})
    except Exception as e:
        print(f"❌ Error obteniendo contador global: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# Contador global de QR vendidos (no escaneados)
@app.route('/api/contador-global-vendidos', methods=['GET'])
def contador_global_vendidos():
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        # Contar QR que tienen un paquete asignado (vendidos) y no son el paquete por defecto
        cursor.execute("""
            SELECT COUNT(*) as total_qr 
            FROM QRCode 
            WHERE turnPackageId IS NOT NULL 
            AND turnPackageId != 1  -- Excluir el paquete por defecto
            AND turnPackageId IS NOT NULL
        """)
        resultado = cursor.fetchone()
        
        print(f"📊 Total QR vendidos (con paquete): {resultado['total_qr']}")
        
        return jsonify({'total_qr': resultado['total_qr']})
    except Exception as e:
        print(f"❌ Error obteniendo contador global vendidos: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# Modificar la ruta existente de historial QR para incluir precio
@app.route('/api/historial-completo', methods=['GET'])
def obtener_historial_completo():
    """Obtener historial completo de QR escaneados por el usuario actual - VERSIÓN CORREGIDA"""
    connection = None
    cursor = None
    try:
        if not session.get('logged_in'):
            return jsonify({'error': 'No autenticado'}), 401
        
        user_id = session.get('user_id')
        local = session.get('user_local', 'El Mekatiadero')
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
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
                FROM QRHistory h
                LEFT JOIN QRCode qr ON qr.code = h.qr_code
                LEFT JOIN UserTurns ut ON ut.qr_code_id = qr.id
                LEFT JOIN TurnPackage tp ON tp.id = qr.turnPackageId
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
                FROM QRHistory h
                LEFT JOIN QRCode qr ON qr.code = h.qr_code
                LEFT JOIN UserTurns ut ON ut.qr_code_id = qr.id
                LEFT JOIN TurnPackage tp ON tp.id = qr.turnPackageId
                WHERE h.user_id = %s OR h.local = %s
                ORDER BY h.fecha_hora DESC
                LIMIT 50
            """, (user_id, local))
        
        historial = cursor.fetchall()
        
        # Formatear fechas para el frontend
        for item in historial:
            if item['fecha_hora']:
                try:
                    fecha_colombia = parse_db_datetime(item['fecha_hora'])
                    item['fecha_hora'] = fecha_colombia.strftime('%Y-%m-%d %H:%M:%S')
                except Exception as e:
                    print(f"⚠️ Error formateando fecha: {e}")
                    # Si hay error, dejar el formato original pero asegurar string
                    item['fecha_hora'] = str(item['fecha_hora'])
           
            item['es_venta'] = item['turnPackageId'] is not None and item['turnPackageId'] != 1
        
        print(f"✅ Historial obtenido: {len(historial)} registros")
        return jsonify(historial)
        
    except Exception as e:
        print(f"❌ Error obteniendo historial completo: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# Historial específico de un QR
@app.route('/api/historial-qr/<qr_code>', methods=['GET'])
def obtener_historial_qr(qr_code):
    """Obtener historial específico de un código QR"""
    connection = None
    cursor = None
    try:
        if not session.get('logged_in'):
            return jsonify({'error': 'No autenticado'}), 401
        
        print(f"🔍 Obteniendo historial para QR: {qr_code}")
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
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
            FROM QRHistory h
            LEFT JOIN QRCode qr ON qr.code = h.qr_code
            LEFT JOIN UserTurns ut ON ut.qr_code_id = qr.id
            LEFT JOIN TurnPackage tp ON tp.id = qr.turnPackageId
            WHERE h.qr_code = %s
            ORDER BY h.fecha_hora DESC
            LIMIT 20
        """, (qr_code,))
        
        historial = cursor.fetchall()
        
        # Formatear fechas para el frontend
        for item in historial:
            if item['fecha_hora']:
                try:
                    fecha_colombia = parse_db_datetime(item['fecha_hora'])
                    item['fecha_hora'] = fecha_colombia.strftime('%Y-%m-%d %H:%M:%S')
                except Exception as e:
                    print(f"⚠️ Error formateando fecha: {e}")
                    item['fecha_hora'] = str(item['fecha_hora'])
           
            item['es_venta'] = item['turnPackageId'] is not None and item['turnPackageId'] != 1
        
        print(f"✅ Historial obtenido para {qr_code}: {len(historial)} registros")
        
        if not historial:
            return jsonify({'message': 'No hay historial para este QR', 'qr_code': qr_code})
        
        return jsonify(historial)
        
    except Exception as e:
        print(f"❌ Error obteniendo historial del QR: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@app.route('/api/registrar-venta', methods=['POST'])
def registrar_venta():
    """Registrar una venta REAL (solo desde package.html)"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        qr_code = data.get('qr_code')
        paquete_id = data.get('paquete_id')
        precio = data.get('precio')
        
        if not qr_code or not paquete_id:
            return jsonify({'error': 'Datos incompletos'}), 400
        
        user_id = session.get('user_id')
        user_name = session.get('user_name', 'Usuario')
        local = session.get('user_local', 'El Mekatiadero')
        
        print(f"💰 REGISTRANDO VENTA REAL: QR={qr_code}, Paquete={paquete_id}, Precio={precio}")
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        # Usar hora actual de Colombia
        hora_colombia = get_colombia_time()
        
        # 1. Guardar en historial ESPECIAL para ventas
        cursor.execute("""
            INSERT INTO QRHistory (qr_code, user_id, user_name, local, fecha_hora, qr_name, es_venta_real)
            VALUES (%s, %s, %s, %s, %s, 
                    (SELECT qr_name FROM QRCode WHERE code = %s LIMIT 1),
                    TRUE)
        """, (qr_code, user_id, user_name, local, format_datetime_for_db(hora_colombia), qr_code))
        
        connection.commit()
        
        return jsonify({
            'success': True,
            'message': 'Venta registrada correctamente',
            'timestamp': hora_colombia.strftime('%Y-%m-%d %H:%M:%S')
        })
        
    except Exception as e:
        print(f"❌ Error registrando venta: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# Ventas del día
@app.route('/api/ventas-dia', methods=['GET'])
def ventas_dia():
    connection = None
    cursor = None
    try:
        fecha = request.args.get('fecha', get_colombia_time().strftime('%Y-%m-%d'))
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        
        cursor.execute("""
            SELECT 
                COUNT(DISTINCT qh.qr_code) as total_ventas,
                COALESCE(SUM(tp.price), 0) as valor_total
            FROM QRHistory qh
            JOIN QRCode qr ON qr.code = qh.qr_code
            JOIN TurnPackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) = %s
            AND qr.turnPackageId IS NOT NULL
            AND qr.turnPackageId != 1  -- Excluir el paquete por defecto
            -- ⚠️ FILTRAMOS POR USUARIO QUE ASIGNÓ EL PAQUETE (opcional)
            -- AND qh.user_id IN (SELECT id FROM Users WHERE role = 'cajero' OR role = 'admin')
        """, (fecha,))
        
        resultado = cursor.fetchone()
        
        print(f"📊 Ventas del día {fecha}: {resultado['total_ventas']} ventas, valor: {resultado['valor_total']}")
        
        return jsonify({
            'total_ventas': resultado['total_ventas'] or 0,
            'valor_total': float(resultado['valor_total'] or 0)
        })
    except Exception as e:
        print(f"❌ Error obteniendo ventas del día: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()
            
@app.route('/test-sentry-activo')
def test_sentry_activo():
    try:
        resultado = 10 / 0
        return "Esto no debería mostrarse"
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return f"✅ Error capturado y enviado a Sentry: {str(e)}"
    
    # Falla maquina
@app.route('/api/reportar-falla-maquina', methods=['POST'])
def reportar_falla_maquina():
    """Reportar falla de máquina y marcarla en mantenimiento"""
    connection = None
    cursor = None

    try:
        data = request.get_json()
        machine_id = data.get('machine_id')
        machine_name = data.get('machine_name')
        description = data.get('description', 'Falla reportada sin descripción adicional')
        user_id = session.get('user_id')

        if not machine_id or not machine_name:
            return jsonify({'error': 'Faltan datos requeridos'}), 400

        if not user_id:
            return jsonify({'error': 'Usuario no autenticado'}), 401

        connection = get_db_connection()
        cursor = get_db_cursor(connection)

        # 🔎 Validar usuario
        cursor.execute("SELECT id FROM user WHERE id = %s", (user_id,))
        if not cursor.fetchone():
            return jsonify({'error': 'Usuario inválido'}), 400

        # 🔎 Validar máquina
        cursor.execute("SELECT id FROM machine WHERE id = %s", (machine_id,))
        if not cursor.fetchone():
            return jsonify({'error': 'Máquina no encontrada'}), 404

        # 🔒 Transacción
        connection.start_transaction()

        # 1️⃣ Insertar reporte de falla
        cursor.execute("""
            INSERT INTO ErrorReport
            (machineId, userId, description, reportedAt, isResolved)
            VALUES (%s, %s, %s, NOW(), FALSE)
        """, (machine_id, user_id, description))

        # 2️⃣ Cambiar estado de la máquina a mantenimiento
        cursor.execute("""
            UPDATE machine
            SET status = 'mantenimiento'
            WHERE id = %s
        """, (machine_id,))

        connection.commit()

        return jsonify({
            'success': True,
            'message': f'Falla reportada en {machine_name}. La máquina fue puesta en mantenimiento.'
        })

    except Exception as e:
        if connection:
            connection.rollback()
        print(f"❌ Error reportando falla de máquina: {e}")
        return jsonify({'error': 'Error interno del servidor'}), 500

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/sales')
def mostrar_ventas():
    if not session.get('logged_in'):
        return redirect(url_for('mostrar_login'))
    
    hora_colombia = get_colombia_time()
    return render_template('sales.html',
                           nombre_usuario=session.get('user_name', 'Usuario'),
                           local_usuario=session.get('user_local', 'El Mekatiadero'),
                           hora_actual=hora_colombia.strftime('%H:%M:%S'),
                           fecha_actual=hora_colombia.strftime('%Y-%m-%d'))

@app.route('/api/ventas')
def obtener_ventas():
    """Obtiene datos de ventas para el panel CON HORA COLOMBIA Y NOMBRE QR"""
    connection = None
    cursor = None
    try:
        fecha_inicio = request.args.get('fecha_inicio', get_colombia_time().strftime('%Y-%m-%d'))
        fecha_fin = request.args.get('fecha_fin', get_colombia_time().strftime('%Y-%m-%d'))
        
        print(f"📊 Solicitando ventas desde {fecha_inicio} hasta {fecha_fin}")
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        # Obtener ventas del rango de fechas CON NOMBRE QR
        cursor.execute("""
            SELECT 
                tp.name as paquete,
                tp.price as precio,
                tp.turns as turnos,
                u.name as vendedor,
                qh.fecha_hora,
                qh.qr_name,
                DATE(qh.fecha_hora) as fecha,
                DATE_FORMAT(qh.fecha_hora, '%%H:%%i') as hora,
                qh.qr_code
            FROM QRHistory qh
            JOIN Users u ON qh.user_id = u.id
            JOIN QRCode qr ON qr.code = qh.qr_code
            LEFT JOIN TurnPackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
            ORDER BY qh.fecha_hora DESC
        """, (fecha_inicio, fecha_fin))
        
        ventas = cursor.fetchall()
        print(f"✅ Encontradas {len(ventas)} ventas")
        
        # Formatear fechas con zona horaria Colombia
        for venta in ventas:
            if venta['fecha_hora']:
                fecha_colombia = parse_db_datetime(venta['fecha_hora'])
                venta['fecha_hora_formateada'] = fecha_colombia.strftime('%d/%m/%Y %H:%M:%S')
                venta['fecha'] = fecha_colombia.strftime('%d/%m/%Y')
                venta['hora'] = fecha_colombia.strftime('%H:%M:%S')  # Hora completa con segundos
        
        # Calcular estadísticas
        total_ventas = 0
        total_paquetes = len(ventas)
        
        for venta in ventas:
            if venta['precio']:
                total_ventas += venta['precio']
        
        ticket_promedio = total_ventas / total_paquetes if total_paquetes > 0 else 0
        
        # Ventas por paquete para el gráfico
        cursor.execute("""
            SELECT 
                COALESCE(tp.name, 'Sin paquete') as paquete,
                COUNT(*) as cantidad,
                COALESCE(SUM(tp.price), 0) as total
            FROM QRHistory qh
            JOIN QRCode qr ON qr.code = qh.qr_code
            LEFT JOIN TurnPackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
            GROUP BY tp.name
        """, (fecha_inicio, fecha_fin))
        
        ventas_por_paquete = cursor.fetchall()
        
        # DETERMINAR TIPO DE GRÁFICA SEGÚN EL RANGO
        fecha_inicio_dt = datetime.strptime(fecha_inicio, '%Y-%m-%d')
        fecha_fin_dt = datetime.strptime(fecha_fin, '%Y-%m-%d')
        dias_rango = (fecha_fin_dt - fecha_inicio_dt).days
        
        graficos_horas = {'labels': [], 'data': [], 'tipo': 'horas'}
        graficos_dias = {'labels': [], 'data': [], 'tipo': 'dias'}
        
        if dias_rango == 0:
            # UN DÍA: Gráfica por horas
            cursor.execute("""
                SELECT 
                    HOUR(qh.fecha_hora) as hora,
                    COUNT(*) as cantidad
                FROM QRHistory qh
                WHERE DATE(qh.fecha_hora) = %s
                GROUP BY HOUR(qh.fecha_hora)
                ORDER BY hora
            """, (fecha_inicio,))
            
            ventas_por_hora = cursor.fetchall()
            
            # Crear array completo de horas (0-23)
            horas_completas = []
            for hora in range(0, 24):
                venta_hora = next((v for v in ventas_por_hora if v['hora'] == hora), None)
                horas_completas.append({
                    'hora': hora,
                    'cantidad': venta_hora['cantidad'] if venta_hora else 0
                })
            
            graficos_horas = {
                'labels': [f"{v['hora']:02d}:00" for v in horas_completas],
                'data': [v['cantidad'] for v in horas_completas],
                'tipo': 'horas'
            }
            
        elif dias_rango <= 31:
            # HASTA 31 DÍAS: Gráfica por días
            cursor.execute("""
                SELECT 
                    DATE(qh.fecha_hora) as fecha,
                    COUNT(*) as cantidad,
                    SUM(tp.price) as total
                FROM QRHistory qh
                JOIN QRCode qr ON qr.code = qh.qr_code
                LEFT JOIN TurnPackage tp ON qr.turnPackageId = tp.id
                WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
                GROUP BY DATE(qh.fecha_hora)
                ORDER BY fecha
            """, (fecha_inicio, fecha_fin))
            
            ventas_por_dia = cursor.fetchall()
            
            # Crear array completo de días en el rango
            from datetime import timedelta
            fechas_completas = []
            current_date = fecha_inicio_dt
            
            while current_date <= fecha_fin_dt:
                venta_dia = next((v for v in ventas_por_dia 
                                if v['fecha'] and v['fecha'].strftime('%Y-%m-%d') == current_date.strftime('%Y-%m-%d')), None)
                fechas_completas.append({
                    'fecha': current_date.strftime('%Y-%m-%d'),
                    'cantidad': venta_dia['cantidad'] if venta_dia else 0,
                    'total': venta_dia['total'] if venta_dia else 0
                })
                current_date += timedelta(days=1)
            
            graficos_dias = {
                'labels': [fecha['fecha'] for fecha in fechas_completas],
                'data': [fecha['cantidad'] for fecha in fechas_completas],
                'tipo': 'dias'
            }
            
        else:
            # MÁS DE 31 DÍAS: Gráfica por semanas
            cursor.execute("""
                SELECT 
                    YEARWEEK(qh.fecha_hora) as semana,
                    COUNT(*) as cantidad,
                    SUM(tp.price) as total
                FROM QRHistory qh
                JOIN QRCode qr ON qr.code = qh.qr_code
                LEFT JOIN TurnPackage tp ON qr.turnPackageId = tp.id
                WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
                GROUP BY YEARWEEK(qh.fecha_hora)
                ORDER BY semana
            """, (fecha_inicio, fecha_fin))
            
            ventas_por_semana = cursor.fetchall()
            
            graficos_dias = {
                'labels': [f"Semana {v['semana']}" for v in ventas_por_semana],
                'data': [v['cantidad'] for v in ventas_por_semana],
                'tipo': 'semanas'
            }
        
        # Preparar datos para gráficos
        graficos = {
            'paquetes': {
                'labels': [v['paquete'] for v in ventas_por_paquete],
                'data': [v['cantidad'] for v in ventas_por_paquete]
            },
            'evolucion': graficos_horas if dias_rango == 0 else graficos_dias
        }
        
        # Preparar datos para tabla CON NOMBRE QR
        ventas_detalle = []
        for venta in ventas:
            fecha_str = venta['fecha'] or ""
            hora_str = venta['hora'] or ""
            nombre_qr = venta['qr_name'] or 'Sin nombre'
            
            ventas_detalle.append({
                'fecha': fecha_str,
                'hora': hora_str,
                'paquete': venta['paquete'] or 'Sin paquete',
                'precio': venta['precio'] or 0,
                'turnos': venta['turnos'] or 0,
                'vendedor': venta['vendedor'] or 'Desconocido',
                'qr_nombre': nombre_qr,
                'qr_codigo': venta['qr_code'] or '',
                'estado': 'completada'
            })
        
        # Calcular tendencias (ejemplo simple)
        tendencia_ventas = 0
        tendencia_paquetes = 0
        
        # Intentar calcular tendencia comparando con período anterior
        try:
            # Período anterior (misma duración)
            dias_duracion = (fecha_fin_dt - fecha_inicio_dt).days
            fecha_inicio_anterior = (fecha_inicio_dt - timedelta(days=dias_duracion + 1)).strftime('%Y-%m-%d')
            fecha_fin_anterior = (fecha_inicio_dt - timedelta(days=1)).strftime('%Y-%m-%d')
            
            cursor.execute("""
                SELECT COUNT(*) as total
                FROM QRHistory 
                WHERE fecha_hora BETWEEN %s AND %s
            """, (fecha_inicio_anterior, fecha_fin_anterior))
            
            ventas_anterior = cursor.fetchone()
            if ventas_anterior and ventas_anterior['total'] > 0:
                crecimiento = ((total_paquetes - ventas_anterior['total']) / ventas_anterior['total']) * 100
                tendencia_paquetes = round(crecimiento, 1)
                tendencia_ventas = round(crecimiento, 1)
        except Exception as e:
            print(f"⚠️ Error calculando tendencia: {e}")
        
        return jsonify({
            'estadisticas': {
                'total_ventas': total_ventas,
                'total_paquetes': total_paquetes,
                'ticket_promedio': round(ticket_promedio, 2),
                'eficiencia': min(100, total_paquetes * 10),
                'tendencia_ventas': tendencia_ventas,
                'tendencia_paquetes': tendencia_paquetes,
                'dias_rango': dias_rango
            },
            'graficos': graficos,
            'ventas': ventas_detalle
        })
        
    except Exception as e:
        print(f"❌ Error obteniendo ventas: {e}")
        import traceback
        traceback.print_exc()
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/exportar-ventas')
def exportar_ventas():
    """Exporta reporte de ventas en CSV"""
 
    fecha = request.args.get('fecha', datetime.now().strftime('%Y-%m-%d'))
    return redirect(f'/api/ventas?fecha={fecha}')
    
# ==================== RUTAS PARA EL PANEL DE ADMINISTRACIÓN ====================

# RUTAS DE INTERFAZ ADMINISTRADOR

# Mostrar panel de administración
@app.route('/admin')
def mostrar_admin():
    if not session.get('logged_in'):
        return redirect(url_for('mostrar_login'))
    
    if session.get('user_role') != 'admin':
        return redirect(url_for('mostrar_local'))
    
    hora_colombia = get_colombia_time()
    return render_template('admin/index.html',
                           nombre_usuario=session.get('user_name', 'Administrador'),
                           local_usuario=session.get('user_local', 'Sistema'),
                           hora_actual=hora_colombia.strftime('%H:%M:%S'),
                           fecha_actual=hora_colombia.strftime('%Y-%m-%d'))

# Mostrar gestión de usuarios
@app.route('/admin/usuarios/lista')
def mostrar_lista_usuarios():
    """Redirigir a la gestión de usuarios"""
    return redirect(url_for('mostrar_gestion_usuarios'))

# Mostrar gestión de paquetes
@app.route('/admin/paquetes/gestionpaquetes')
def mostrar_gestion_paquetes():
    if not session.get('logged_in'):
        return redirect(url_for('mostrar_login'))
    
    # Verificar que el usuario sea admin
    if session.get('user_role') != 'admin':
        return redirect(url_for('mostrar_local'))
    
    return render_template('admin/paquetes/gestionpaquetes.html',
                           nombre_usuario=session.get('user_name', 'Administrador'),
                           local_usuario=session.get('user_local', 'Sistema'))

# Mostrar gestión de locales
@app.route('/admin/locales/gestionlocales')
def mostrar_gestion_locales():
    if not session.get('logged_in'):
        return redirect(url_for('mostrar_login'))
    
    # Verificar que el usuario sea admin
    if session.get('user_role') != 'admin':
        return redirect(url_for('mostrar_local'))
    
    return render_template('admin/locales/gestionlocales.html',
                           nombre_usuario=session.get('user_name', 'Administrador'),
                           local_usuario=session.get('user_local', 'Sistema'))

#LLAMADAS DE APIS ADMINISTRADOR
@app.route('/api/dashboard/estadisticas')
def dashboard_estadisticas():
    """Estadísticas principales para el dashboard"""
    connection = None
    cursor = None
    try:
        fecha_inicio = request.args.get('fecha_inicio', datetime.now().strftime('%Y-%m-%d'))
        fecha_fin = request.args.get('fecha_fin', datetime.now().strftime('%Y-%m-%d'))
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        # Ingresos totales del período
        cursor.execute("""
            SELECT COALESCE(SUM(tp.price), 0) as ingresos_totales
            FROM QRHistory qh
            JOIN QRCode qr ON qr.code = qh.qr_code
            LEFT JOIN TurnPackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
        """, (fecha_inicio, fecha_fin))
        ingresos_totales = cursor.fetchone()['ingresos_totales']
        
        # Paquetes vendidos
        cursor.execute("""
            SELECT COUNT(*) as paquetes_vendidos
            FROM QRHistory 
            WHERE DATE(fecha_hora) BETWEEN %s AND %s
        """, (fecha_inicio, fecha_fin))
        paquetes_vendidos = cursor.fetchone()['paquetes_vendidos']
        
        # Estado de máquinas
        cursor.execute("SELECT COUNT(*) as maquinas_totales FROM Machine")
        maquinas_totales = cursor.fetchone()['maquinas_totales']
        
        cursor.execute("SELECT COUNT(*) as maquinas_activas FROM Machine WHERE status = 'activa'")
        maquinas_activas = cursor.fetchone()['maquinas_activas']
        
        # Ticket promedio
        ticket_promedio = ingresos_totales / paquetes_vendidos if paquetes_vendidos > 0 else 0
        
        # Calcular tendencias vs período anterior
        fecha_inicio_dt = datetime.strptime(fecha_inicio, '%Y-%m-%d')
        fecha_fin_dt = datetime.strptime(fecha_fin, '%Y-%m-%d')
        dias_rango = (fecha_fin_dt - fecha_inicio_dt).days
        
        tendencia_ingresos = 0
        tendencia_paquetes = 0
        
        # Calcular período anterior para comparación
        if dias_rango > 0:
            fecha_inicio_anterior = (fecha_inicio_dt - timedelta(days=dias_rango + 1)).strftime('%Y-%m-%d')
            fecha_fin_anterior = (fecha_inicio_dt - timedelta(days=1)).strftime('%Y-%m-%d')
            
            # Ingresos período anterior
            cursor.execute("""
                SELECT COALESCE(SUM(tp.price), 0) as ingresos_anterior
                FROM QRHistory qh
                JOIN QRCode qr ON qr.code = qh.qr_code
                LEFT JOIN TurnPackage tp ON qr.turnPackageId = tp.id
                WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
            """, (fecha_inicio_anterior, fecha_fin_anterior))
            ingresos_anterior = cursor.fetchone()['ingresos_anterior']
            
            # Paquetes período anterior
            cursor.execute("""
                SELECT COUNT(*) as paquetes_anterior
                FROM QRHistory 
                WHERE DATE(fecha_hora) BETWEEN %s AND %s
            """, (fecha_inicio_anterior, fecha_fin_anterior))
            paquetes_anterior = cursor.fetchone()['paquetes_anterior']
            
            # Calcular tendencias
            if ingresos_anterior > 0:
                tendencia_ingresos = round(((ingresos_totales - ingresos_anterior) / ingresos_anterior) * 100, 1)
            if paquetes_anterior > 0:
                tendencia_paquetes = round(((paquetes_vendidos - paquetes_anterior) / paquetes_anterior) * 100, 1)
        
        return jsonify({
            'ingresos_totales': ingresos_totales,
            'paquetes_vendidos': paquetes_vendidos,
            'maquinas_totales': maquinas_totales,
            'maquinas_activas': maquinas_activas,
            'ticket_promedio': round(ticket_promedio, 2),
            'tendencias': {
                'ingresos': tendencia_ingresos,
                'paquetes': tendencia_paquetes
            }
        })
        
    except Exception as e:
        print(f"❌ Error obteniendo estadísticas dashboard: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/dashboard/graficas')
def dashboard_graficas():
    """Datos para todas las gráficas del dashboard"""
    connection = None
    cursor = None
    try:
        fecha_inicio = request.args.get('fecha_inicio', datetime.now().strftime('%Y-%m-%d'))
        fecha_fin = request.args.get('fecha_fin', datetime.now().strftime('%Y-%m-%d'))
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        # 1. EVOLUCIÓN DE VENTAS (por día)
        cursor.execute("""
            SELECT 
                DATE(fecha_hora) as fecha,
                COUNT(*) as cantidad_ventas,
                COALESCE(SUM(tp.price), 0) as ingresos
            FROM QRHistory qh
            JOIN QRCode qr ON qr.code = qh.qr_code
            LEFT JOIN TurnPackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
            GROUP BY DATE(fecha_hora)
            ORDER BY fecha
        """, (fecha_inicio, fecha_fin))
        
        evolucion_data = cursor.fetchall()
        evolucion_ventas = {
            'labels': [item['fecha'].strftime('%d/%m') for item in evolucion_data],
            'data': [item['cantidad_ventas'] for item in evolucion_data]
        }
        
        # 2. VENTAS POR PAQUETE
        cursor.execute("""
            SELECT 
                COALESCE(tp.name, 'Sin paquete') as paquete,
                COUNT(*) as cantidad,
                COALESCE(SUM(tp.price), 0) as total
            FROM QRHistory qh
            JOIN QRCode qr ON qr.code = qh.qr_code
            LEFT JOIN TurnPackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
            GROUP BY tp.name
            ORDER BY cantidad DESC
        """, (fecha_inicio, fecha_fin))
        
        paquetes_data = cursor.fetchall()
        ventas_paquetes = {
            'labels': [item['paquete'] for item in paquetes_data],
            'data': [item['cantidad'] for item in paquetes_data]
        }
        
        # 3. RENDIMIENTO POR MÁQUINA (top 10)
        cursor.execute("""
            SELECT 
                m.name as maquina,
                COUNT(tu.id) as usos,
                COALESCE(SUM(tp.price), 0) as ingresos
            FROM Machine m
            LEFT JOIN TurnUsage tu ON tu.machineId = m.id
            LEFT JOIN QRCode qr ON qr.id = tu.qrCodeId
            LEFT JOIN TurnPackage tp ON tp.id = qr.turnPackageId
            WHERE DATE(tu.usedAt) BETWEEN %s AND %s OR tu.id IS NULL
            GROUP BY m.id, m.name
            ORDER BY usos DESC
            LIMIT 10
        """, (fecha_inicio, fecha_fin))
        
        maquinas_data = cursor.fetchall()
        rendimiento_maquinas = {
            'labels': [item['maquina'] for item in maquinas_data],
            'data': [item['usos'] for item in maquinas_data]
        }
        
        # 4. ESTADO DE MÁQUINAS
        cursor.execute("""
            SELECT 
                status,
                COUNT(*) as cantidad
            FROM Machine
            GROUP BY status
        """)
        
        estado_data = cursor.fetchall()
        estado_maquinas = [0, 0, 0]  # [activas, mantenimiento, inactivas]
        
        for item in estado_data:
            if item['status'] == 'activa':
                estado_maquinas[0] = item['cantidad']
            elif item['status'] == 'mantenimiento':
                estado_maquinas[1] = item['cantidad']
            else:
                estado_maquinas[2] = item['cantidad']
        
        return jsonify({
            'evolucion_ventas': evolucion_ventas,
            'ventas_paquetes': ventas_paquetes,
            'rendimiento_maquinas': rendimiento_maquinas,
            'estado_maquinas': estado_maquinas
        })
        
    except Exception as e:
        print(f"❌ Error obteniendo datos para gráficas: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/dashboard/top-maquinas')
def dashboard_top_maquinas():
    """Top 5 máquinas más rentables"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("""
            SELECT 
                m.name,
                COUNT(tu.id) as ventas,
                COALESCE(SUM(tp.price), 0) as ingresos
            FROM Machine m
            LEFT JOIN TurnUsage tu ON tu.machineId = m.id
            LEFT JOIN QRCode qr ON qr.id = tu.qrCodeId
            LEFT JOIN TurnPackage tp ON tp.id = qr.turnPackageId
            WHERE tu.usedAt >= DATE_SUB(NOW(), INTERVAL 7 DAY)
            GROUP BY m.id, m.name
            ORDER BY ingresos DESC
            LIMIT 5
        """)
        
        top_maquinas = cursor.fetchall()
        return jsonify(top_maquinas)
        
    except Exception as e:
        print(f"❌ Error obteniendo top máquinas: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify([]), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/dashboard/ventas-recientes')
def dashboard_ventas_recientes():
    """Ventas más recientes"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("""
            SELECT 
                qh.qr_code,
                COALESCE(tp.name, 'Sin paquete') as paquete,
                COALESCE(tp.price, 0) as precio,
                DATE_FORMAT(qh.fecha_hora, '%%H:%%i') as hora,
                u.name as vendedor
            FROM QRHistory qh
            LEFT JOIN Users u ON qh.user_id = u.id
            LEFT JOIN QRCode qr ON qr.code = qh.qr_code
            LEFT JOIN TurnPackage tp ON tp.id = qr.turnPackageId
            ORDER BY qh.fecha_hora DESC
            LIMIT 10
        """)
        
        ventas_recientes = cursor.fetchall()
        return jsonify(ventas_recientes)
        
    except Exception as e:
        print(f"❌ Error obteniendo ventas recientes: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify([]), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/dashboard/alertas')
def dashboard_alertas():
    """Alertas del sistema"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        # Máquinas con fallos recientes
        cursor.execute("""
            SELECT 
                m.name as titulo,
                CONCAT('Reportada por: ', u.name) as descripcion,
                'error' as tipo,
                'Pendiente' as estado
            FROM ErrorReport er
            JOIN Machine m ON er.machineId = m.id
            JOIN Users u ON er.userId = u.id
            WHERE er.isResolved = FALSE
            ORDER BY er.reportedAt DESC
            LIMIT 5
        """)
        
        alertas = cursor.fetchall()
        
        # Si no hay alertas, agregar una de ejemplo
        if not alertas:
            alertas = [{
                'titulo': 'Sistema funcionando correctamente',
                'descripcion': 'No hay alertas activas en este momento',
                'tipo': 'info',
                'estado': 'Resuelto'
            }]
        
        return jsonify(alertas)
        
    except Exception as e:
        print(f"❌ Error obteniendo alertas: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify([]), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/dashboard/metricas')
def dashboard_metricas():
    """Métricas de rendimiento del sistema"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        # Ocupación (porcentaje de máquinas usadas hoy)
        cursor.execute("""
            SELECT COUNT(DISTINCT machineId) as maquinas_usadas
            FROM TurnUsage 
            WHERE DATE(usedAt) = CURDATE()
        """)
        maquinas_usadas = cursor.fetchone()['maquinas_usadas']
        
        cursor.execute("SELECT COUNT(*) as total_maquinas FROM Machine WHERE status = 'activa'")
        total_maquinas = cursor.fetchone()['total_maquinas']
        
        ocupacion = round((maquinas_usadas / total_maquinas * 100) if total_maquinas > 0 else 0, 1)
        
        # Eficiencia (ventas por hora)
        cursor.execute("""
            SELECT COUNT(*) as ventas_hoy
            FROM QRHistory 
            WHERE DATE(fecha_hora) = CURDATE()
        """)
        ventas_hoy = cursor.fetchone()['ventas_hoy']
        
        hora_actual = datetime.now().hour
        eficiencia = round((ventas_hoy / max(1, hora_actual)) * 10, 1) if hora_actual > 0 else 0
        
        # Conversión (estimada)
        conversion = min(100, round(ventas_hoy * 2.5, 1))
        
        # Satisfacción (estimada basada en fallos)
        cursor.execute("""
            SELECT COUNT(*) as fallos_hoy
            FROM ErrorReport 
            WHERE DATE(reportedAt) = CURDATE()
        """)
        fallos_hoy = cursor.fetchone()['fallos_hoy']
        
        satisfaccion = max(0, 100 - (fallos_hoy * 5))
        
        return jsonify({
            'ocupacion': ocupacion,
            'eficiencia': eficiencia,
            'conversion': conversion,
            'satisfaccion': round(satisfaccion, 1)
        })
        
    except Exception as e:
        print(f"❌ Error obteniendo métricas: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({
            'ocupacion': 0,
            'eficiencia': 0,
            'conversion': 0,
            'satisfaccion': 0
        }), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

            # ==================== APIS PARA GESTIÓN DE USUARIOS ====================

@app.route('/admin/usuarios/gestionusuarios')
def mostrar_gestion_usuarios():
    if not session.get('logged_in'):
        return redirect(url_for('mostrar_login'))
    
    # Verificar que el usuario sea admin
    if session.get('user_role') != 'admin':
        return redirect(url_for('mostrar_local'))
    
    return render_template('admin/usuarios/gestionusuarios.html',
                           nombre_usuario=session.get('user_name', 'Administrador'),
                           local_usuario=session.get('user_local', 'Sistema'))

@app.route('/api/usuarios', methods=['GET'])
def obtener_usuarios():
    """Obtener todos los usuarios"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("""
            SELECT u.*, creador.name as creador_nombre
            FROM Users u
            LEFT JOIN Users creador ON u.createdBy = creador.id
            ORDER BY u.createdAt DESC
        """)
        
        usuarios = cursor.fetchall()
        
        # Formatear los datos para el frontend
        usuarios_formateados = []
        for usuario in usuarios:
            usuarios_formateados.append({
                'id': usuario['id'],
                'name': usuario['name'],
                'role': usuario['role'],
                'createdBy': usuario['createdBy'],
                'creador': {'name': usuario['creador_nombre']} if usuario['creador_nombre'] else None,
                'createdAt': usuario['createdAt'],
                'notes': usuario['notes']
            })
        
        return jsonify(usuarios_formateados)
        
    except Exception as e:
        print(f"❌ Error obteniendo usuarios: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/usuarios/<int:usuario_id>', methods=['GET'])
def obtener_usuario(usuario_id):
    """Obtener un usuario específico"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("SELECT * FROM Users WHERE id = %s", (usuario_id,))
        usuario = cursor.fetchone()
        
        if not usuario:
            return jsonify({'error': 'Usuario no encontrado'}), 404
        
        return jsonify({
            'id': usuario['id'],
            'name': usuario['name'],
            'role': usuario['role'],
            'createdBy': usuario['createdBy'],
            'createdAt': usuario['createdAt'],
            'notes': usuario['notes']
        })
        
    except Exception as e:
        print(f"❌ Error obteniendo usuario: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/usuarios', methods=['POST'])
def crear_usuario():
    """Crear un nuevo usuario"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        name = data.get('name')
        password = data.get('password')
        role = data.get('role')
        local = data.get('local', 'El Mekatiadero')
        notes = data.get('notes', '')
        
        # Validaciones
        if not name or not password or not role:
            return jsonify({'error': 'Nombre, contraseña y rol son obligatorios'}), 400
        
        if len(password) < 6:
            return jsonify({'error': 'La contraseña debe tener al menos 6 caracteres'}), 400
        
        if role not in ['admin', 'cajero', 'admin_restaurante']:
            return jsonify({'error': 'Rol inválido'}), 400
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        # Verificar si el usuario ya existe
        cursor.execute("SELECT id FROM Users WHERE name = %s", (name,))
        if cursor.fetchone():
            return jsonify({'error': 'Ya existe un usuario con ese nombre'}), 400
        
        # Crear usuario
        cursor.execute("""
            INSERT INTO Users (name, password, role, createdBy, notes)
            VALUES (%s, %s, %s, %s, %s)
        """, (name, password, role, session.get('user_id'), notes))
        
        connection.commit()
        
        return jsonify({
            'success': True,
            'message': 'Usuario creado correctamente',
            'usuario_id': cursor.lastrowid
        })
        
    except Exception as e:
        print(f"❌ Error creando usuario: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/usuarios/<int:usuario_id>', methods=['PUT'])
def actualizar_usuario(usuario_id):
    """Actualizar un usuario existente"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        name = data.get('name')
        password = data.get('password')
        role = data.get('role')
        local = data.get('local')
        notes = data.get('notes')
        
        # Validaciones
        if not name or not role:
            return jsonify({'error': 'Nombre y rol son obligatorios'}), 400
        
        if role not in ['admin', 'cajero', 'admin_restaurante']:
            return jsonify({'error': 'Rol inválido'}), 400
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        # Verificar que el usuario existe
        cursor.execute("SELECT id FROM Users WHERE id = %s", (usuario_id,))
        if not cursor.fetchone():
            return jsonify({'error': 'Usuario no encontrado'}), 404
        
        # Verificar nombre duplicado
        cursor.execute("SELECT id FROM Users WHERE name = %s AND id != %s", (name, usuario_id))
        if cursor.fetchone():
            return jsonify({'error': 'Ya existe otro usuario con ese nombre'}), 400
        
        # Actualizar usuario
        if password:
            cursor.execute("""
                UPDATE Users 
                SET name = %s, password = %s, role = %s, notes = %s
                WHERE id = %s
            """, (name, password, role, notes, usuario_id))
        else:
            cursor.execute("""
                UPDATE Users 
                SET name = %s, role = %s, notes = %s
                WHERE id = %s
            """, (name, role, notes, usuario_id))
        
        connection.commit()
        
        return jsonify({
            'success': True,
            'message': 'Usuario actualizado correctamente'
        })
        
    except Exception as e:
        print(f"❌ Error actualizando usuario: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/usuarios/<int:usuario_id>', methods=['DELETE'])
def eliminar_usuario(usuario_id):
    """Eliminar un usuario"""
    connection = None
    cursor = None
    try:
        # No permitir eliminar el usuario actual
        if usuario_id == session.get('user_id'):
            return jsonify({'error': 'No puedes eliminar tu propio usuario'}), 400
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        # Verificar que el usuario existe
        cursor.execute("SELECT id FROM Users WHERE id = %s", (usuario_id,))
        if not cursor.fetchone():
            return jsonify({'error': 'Usuario no encontrado'}), 404
        
        # Eliminar usuario
        cursor.execute("DELETE FROM Users WHERE id = %s", (usuario_id,))
        connection.commit()
        
        return jsonify({
            'success': True,
            'message': 'Usuario eliminado correctamente'
        })
        
    except Exception as e:
        print(f"❌ Error eliminando usuario: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# ==================== APIS PARA GESTIÓN DE PAQUETES ====================
@app.route('/api/paquetes/<int:paquete_id>', methods=['GET'])
def obtener_paquete(paquete_id):
    """Obtener un paquete específico"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("SELECT * FROM TurnPackage WHERE id = %s", (paquete_id,))
        paquete = cursor.fetchone()
        
        if not paquete:
            return jsonify({'error': 'Paquete no encontrado'}), 404
        
        return jsonify(paquete)
        
    except Exception as e:
        print(f"❌ Error obteniendo paquete: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/paquetes', methods=['POST'])
def crear_paquete():
    """Crear un nuevo paquete"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        name = data.get('name')
        turns = data.get('turns')
        price = data.get('price')
        isActive = data.get('isActive', True)
        
        # Validaciones
        if not name or not turns or not price:
            return jsonify({'error': 'Nombre, turnos y precio son obligatorios'}), 400
        
        if turns < 1:
            return jsonify({'error': 'El número de turnos debe ser mayor a 0'}), 400
        
        if price < 1000:
            return jsonify({'error': 'El precio debe ser mayor a $1,000'}), 400
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        # Verificar si el paquete ya existe
        cursor.execute("SELECT id FROM TurnPackage WHERE name = %s", (name,))
        if cursor.fetchone():
            return jsonify({'error': 'Ya existe un paquete con ese nombre'}), 400
        
        # Crear paquete
        cursor.execute("""
            INSERT INTO TurnPackage (name, turns, price, isActive)
            VALUES (%s, %s, %s, %s)
        """, (name, turns, price, isActive))
        
        connection.commit()
        
        return jsonify({
            'success': True,
            'message': 'Paquete creado correctamente',
            'paquete_id': cursor.lastrowid
        })
        
    except Exception as e:
        print(f"❌ Error creando paquete: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/paquetes/<int:paquete_id>', methods=['PUT'])
def actualizar_paquete(paquete_id):
    """Actualizar un paquete existente"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        name = data.get('name')
        turns = data.get('turns')
        price = data.get('price')
        isActive = data.get('isActive')
        
        # Validaciones
        if not name or not turns or not price:
            return jsonify({'error': 'Nombre, turnos y precio son obligatorios'}), 400
        
        if turns < 1:
            return jsonify({'error': 'El número de turnos debe ser mayor a 0'}), 400
        
        if price < 1000:
            return jsonify({'error': 'El precio debe ser mayor a $1,000'}), 400
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        # Verificar que el paquete existe
        cursor.execute("SELECT id FROM TurnPackage WHERE id = %s", (paquete_id,))
        if not cursor.fetchone():
            return jsonify({'error': 'Paquete no encontrado'}), 404
        
        # Verificar nombre duplicado
        cursor.execute("SELECT id FROM TurnPackage WHERE name = %s AND id != %s", (name, paquete_id))
        if cursor.fetchone():
            return jsonify({'error': 'Ya existe otro paquete con ese nombre'}), 400
        
        # Actualizar paquete
        cursor.execute("""
            UPDATE TurnPackage 
            SET name = %s, turns = %s, price = %s, isActive = %s
            WHERE id = %s
        """, (name, turns, price, isActive, paquete_id))
        
        connection.commit()
        
        return jsonify({
            'success': True,
            'message': 'Paquete actualizado correctamente'
        })
        
    except Exception as e:
        print(f"❌ Error actualizando paquete: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/paquetes/<int:paquete_id>', methods=['DELETE'])
def eliminar_paquete(paquete_id):
    """Eliminar un paquete"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        # Verificar que el paquete existe
        cursor.execute("SELECT id FROM TurnPackage WHERE id = %s", (paquete_id,))
        if not cursor.fetchone():
            return jsonify({'error': 'Paquete no encontrado'}), 404
        
        # Verificar si el paquete está en uso
        cursor.execute("""
            SELECT COUNT(*) as uso_count 
            FROM QRCode 
            WHERE turnPackageId = %s
        """, (paquete_id,))
        uso_count = cursor.fetchone()['uso_count']
        
        if uso_count > 0:
            return jsonify({
                'error': f'No se puede eliminar el paquete. Está siendo usado por {uso_count} códigos QR.'
            }), 400
        
        # Eliminar paquete
        cursor.execute("DELETE FROM TurnPackage WHERE id = %s", (paquete_id,))
        connection.commit()
        
        return jsonify({
            'success': True,
            'message': 'Paquete eliminado correctamente'
        })
        
    except Exception as e:
        print(f"❌ Error eliminando paquete: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/admin/paquetes/lista')
def mostrar_lista_paquetes():
    """Redirigir a la gestión de paquetes"""
    return redirect(url_for('mostrar_gestion_paquetes'))

# ==================== APIS PARA GESTIÓN DE LOCALES ====================
@app.route('/api/locales', methods=['GET'])
def obtener_locales():
    """Obtener todos los locales con estadísticas"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        # Primero verificar si las columnas adicionales existen
        cursor.execute("""
            SELECT COLUMN_NAME 
            FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_NAME = 'Location' AND COLUMN_NAME = 'telefono'
        """)
        tiene_telefono = cursor.fetchone() is not None
        
        if tiene_telefono:
            cursor.execute("""
                SELECT l.*, 
                       COUNT(m.id) as maquinas_count,
                       SUM(CASE WHEN m.status = 'activa' THEN 1 ELSE 0 END) as maquinas_activas
                FROM Location l
                LEFT JOIN Machine m ON l.id = m.location_id
                GROUP BY l.id
                ORDER BY l.name
            """)
        else:
            # Si no tiene las columnas adicionales, usar solo las básicas
            cursor.execute("""
                SELECT l.id, l.name, l.address, l.city, l.status,
                       COUNT(m.id) as maquinas_count,
                       SUM(CASE WHEN m.status = 'activa' THEN 1 ELSE 0 END) as maquinas_activas
                FROM Location l
                LEFT JOIN Machine m ON l.id = m.location_id
                GROUP BY l.id
                ORDER BY l.name
            """)
        
        locales = cursor.fetchall()
        return jsonify(locales)
        
    except Exception as e:
        print(f"❌ Error obteniendo locales: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/locales/<int:local_id>', methods=['GET'])
def obtener_local(local_id):
    """Obtener un local específico"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        # Verificar si las columnas adicionales existen
        cursor.execute("""
            SELECT COLUMN_NAME 
            FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_NAME = 'Location' AND COLUMN_NAME = 'telefono'
        """)
        tiene_telefono = cursor.fetchone() is not None
        
        if tiene_telefono:
            cursor.execute("SELECT * FROM Location WHERE id = %s", (local_id,))
        else:
            cursor.execute("SELECT id, name, address, city, status FROM Location WHERE id = %s", (local_id,))
        
        local = cursor.fetchone()
        
        if not local:
            return jsonify({'error': 'Local no encontrado'}), 404
        
        # Asegurarse de que todos los campos existan en la respuesta
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
        print(f"❌ Error obteniendo local: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/locales', methods=['POST'])
def crear_local():
    """Crear un nuevo local"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        name = data.get('name')
        address = data.get('address')
        city = data.get('city')
        status = data.get('status', 'activo')
        telefono = data.get('telefono', '')
        horario = data.get('horario', '')
        notas = data.get('notas', '')
        
        # Validaciones
        if not name or not address or not city:
            return jsonify({'error': 'Nombre, dirección y ciudad son obligatorios'}), 400
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        # Verificar si el local ya existe
        cursor.execute("SELECT id FROM Location WHERE name = %s", (name,))
        if cursor.fetchone():
            return jsonify({'error': 'Ya existe un local con ese nombre'}), 400
        
        # Verificar si las columnas adicionales existen
        cursor.execute("""
            SELECT COLUMN_NAME 
            FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_NAME = 'Location' AND COLUMN_NAME = 'telefono'
        """)
        tiene_telefono = cursor.fetchone() is not None
        
        # Crear local
        if tiene_telefono:
            cursor.execute("""
                INSERT INTO Location (name, address, city, status, telefono, horario, notas)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (name, address, city, status, telefono, horario, notas))
        else:
            cursor.execute("""
                INSERT INTO Location (name, address, city, status)
                VALUES (%s, %s, %s, %s)
            """, (name, address, city, status))
        
        connection.commit()
        
        return jsonify({
            'success': True,
            'message': 'Local creado correctamente',
            'local_id': cursor.lastrowid
        })
        
    except Exception as e:
        print(f"❌ Error creando local: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/locales/<int:local_id>', methods=['PUT'])
def actualizar_local(local_id):
    """Actualizar un local existente"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        name = data.get('name')
        address = data.get('address')
        city = data.get('city')
        status = data.get('status')
        telefono = data.get('telefono', '')
        horario = data.get('horario', '')
        notas = data.get('notas', '')
        
        # Validaciones
        if not name or not address or not city:
            return jsonify({'error': 'Nombre, dirección y ciudad son obligatorios'}), 400
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        # Verificar que el local existe
        cursor.execute("SELECT id FROM Location WHERE id = %s", (local_id,))
        if not cursor.fetchone():
            return jsonify({'error': 'Local no encontrado'}), 404
        
        # Verificar nombre duplicado
        cursor.execute("SELECT id FROM Location WHERE name = %s AND id != %s", (name, local_id))
        if cursor.fetchone():
            return jsonify({'error': 'Ya existe otro local con ese nombre'}), 400
        
        # Verificar si las columnas adicionales existen
        cursor.execute("""
            SELECT COLUMN_NAME 
            FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_NAME = 'Location' AND COLUMN_NAME = 'telefono'
        """)
        tiene_telefono = cursor.fetchone() is not None
        
        # Actualizar local
        if tiene_telefono:
            cursor.execute("""
                UPDATE Location 
                SET name = %s, address = %s, city = %s, status = %s, 
                    telefono = %s, horario = %s, notas = %s
                WHERE id = %s
            """, (name, address, city, status, telefono, horario, notas, local_id))
        else:
            cursor.execute("""
                UPDATE Location 
                SET name = %s, address = %s, city = %s, status = %s
                WHERE id = %s
            """, (name, address, city, status, local_id))
        
        connection.commit()
        
        return jsonify({
            'success': True,
            'message': 'Local actualizado correctamente'
        })
        
    except Exception as e:
        print(f"❌ Error actualizando local: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/locales/<int:local_id>', methods=['DELETE'])
def eliminar_local(local_id):
    """Eliminar un local"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        # Verificar que el local existe
        cursor.execute("SELECT id FROM Location WHERE id = %s", (local_id,))
        if not cursor.fetchone():
            return jsonify({'error': 'Local no encontrado'}), 404
        
        # Verificar si el local tiene máquinas asignadas
        cursor.execute("SELECT COUNT(*) as maquinas_count FROM Machine WHERE location_id = %s", (local_id,))
        maquinas_count = cursor.fetchone()['maquinas_count']
        
        if maquinas_count > 0:
            return jsonify({
                'error': f'No se puede eliminar el local. Tiene {maquinas_count} máquinas asignadas.'
            }), 400
        
        # Eliminar local
        cursor.execute("DELETE FROM Location WHERE id = %s", (local_id,))
        connection.commit()
        
        return jsonify({
            'success': True,
            'message': 'Local eliminado correctamente'
        })
        
    except Exception as e:
        print(f"❌ Error eliminando local: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/admin/locales/listalocales')
def mostrar_lista_locales():
    """Redirigir a la gestión de locales"""
    return redirect(url_for('mostrar_gestion_locales'))

# ==================== RUTAS PARA GESTIÓN DE MÁQUINAS ====================

# Mostrar gestión de máquinas
@app.route('/admin/maquinas/gestionmaquinas')
def mostrar_gestion_maquinas():
    if not session.get('logged_in'):
        return redirect(url_for('mostrar_login'))
    
    # Verificar que el usuario sea admin
    if session.get('user_role') != 'admin':
        return redirect(url_for('mostrar_local'))
    
    return render_template('admin/maquinas/gestionmaquinas.html',
                           nombre_usuario=session.get('user_name', 'Administrador'),
                           local_usuario=session.get('user_local', 'Sistema'))

# ==================== FUNCIONES AUXILIARES ====================

def formatear_info_propietarios(propietarios):
    """Formatear la información de propietarios para display en la tabla"""
    if not propietarios:
        return "Sin propietarios"
    
    # Ordenar por porcentaje (mayor a menor)
    propietarios_ordenados = sorted(propietarios, key=lambda x: x['porcentaje_propiedad'], reverse=True)
    
    info = []
    for prop in propietarios_ordenados:
        info.append(f"{prop['nombre']} ({prop['porcentaje_propiedad']}%)")
    
    return ", ".join(info)

# ==================== APIS PARA GESTIÓN DE MÁQUINAS ====================

@app.route('/api/maquinas', methods=['GET'])
def obtener_maquinas():
    """Obtener todas las máquinas con información completa de propietarios"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
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
            FROM Machine m
            LEFT JOIN Location l ON m.location_id = l.id
            LEFT JOIN MaquinaPorcentajeRestaurante mpr ON m.id = mpr.maquina_id
            ORDER BY m.name
        """)
        
        maquinas = cursor.fetchall()
        
        # Obtener información de propietarios para cada máquina
        maquinas_formateadas = []
        for maquina in maquinas:
            cursor.execute("""
                SELECT 
                    p.id,
                    p.nombre,
                    mp.porcentaje_propiedad
                FROM MaquinaPropietario mp
                JOIN Propietarios p ON mp.propietario_id = p.id
                WHERE mp.maquina_id = %s
            """, (maquina['id'],))
            
            propietarios = cursor.fetchall()
            
            # Formatear información de propietarios para display
            info_propietarios = formatear_info_propietarios(propietarios)
            
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
        print(f"❌ Error obteniendo máquinas: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/maquinas/<int:maquina_id>', methods=['GET'])
def obtener_maquina(maquina_id):
    """Obtener una máquina específica con información detallada de propietarios"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("""
            SELECT 
                m.*,
                l.name as location_name,
                COALESCE(mpr.porcentaje_restaurante, 35.00) as porcentaje_restaurante
            FROM Machine m
            LEFT JOIN Location l ON m.location_id = l.id
            LEFT JOIN MaquinaPorcentajeRestaurante mpr ON m.id = mpr.maquina_id
            WHERE m.id = %s
        """, (maquina_id,))
        
        maquina = cursor.fetchone()
        
        if not maquina:
            return jsonify({'error': 'Máquina no encontrada'}), 404
        
        # Obtener información de propietarios
        cursor.execute("""
            SELECT 
                p.id,
                p.nombre,
                mp.porcentaje_propiedad
            FROM MaquinaPropietario mp
            JOIN Propietarios p ON mp.propietario_id = p.id
            WHERE mp.maquina_id = %s
        """, (maquina_id,))
        
        propietarios = cursor.fetchall()
        
        # Formatear información de propietarios
        info_propietarios = formatear_info_propietarios(propietarios)
        
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
        print(f"❌ Error obteniendo máquina: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/maquinas', methods=['POST'])
def crear_maquina():
    """Crear una nueva máquina"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        name = data.get('name')
        type = data.get('type')
        status = data.get('status', 'activa')
        location_id = data.get('location_id')
        errorNote = data.get('errorNote', '')
        porcentaje_restaurante = data.get('porcentaje_restaurante', 35.00)
        
        # Validaciones
        if not name or not type or not location_id:
            return jsonify({'error': 'Nombre, tipo y local son obligatorios'}), 400
        
        if type not in ['simulador', 'arcade', 'peluchera']:
            return jsonify({'error': 'Tipo de máquina inválido'}), 400
        
        if status not in ['activa', 'mantenimiento', 'inactiva']:
            return jsonify({'error': 'Estado inválido'}), 400
        
        if not (0 <= float(porcentaje_restaurante) <= 100):
            return jsonify({'error': 'El porcentaje del restaurante debe estar entre 0 y 100'}), 400
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        # Verificar si la máquina ya existe
        cursor.execute("SELECT id FROM Machine WHERE name = %s", (name,))
        if cursor.fetchone():
            return jsonify({'error': 'Ya existe una máquina con ese nombre'}), 400
        
        # Verificar que el local existe
        cursor.execute("SELECT id FROM Location WHERE id = %s", (location_id,))
        if not cursor.fetchone():
            return jsonify({'error': 'El local especificado no existe'}), 400
        
        # Crear máquina
        cursor.execute("""
            INSERT INTO Machine (name, type, status, location_id, errorNote)
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
        
        return jsonify({
            'success': True,
            'message': 'Máquina creada correctamente',
            'maquina_id': maquina_id
        })
        
    except Exception as e:
        print(f"❌ Error creando máquina: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/maquinas/<int:maquina_id>', methods=['PUT'])
def actualizar_maquina(maquina_id):
    """Actualizar una máquina existente"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        name = data.get('name')
        type = data.get('type')
        status = data.get('status')
        location_id = data.get('location_id')
        errorNote = data.get('errorNote', '')
        porcentaje_restaurante = data.get('porcentaje_restaurante', 35.00)
        
        # Validaciones
        if not name or not type or not status or not location_id:
            return jsonify({'error': 'Nombre, tipo, estado y local son obligatorios'}), 400
        
        if type not in ['simulador', 'arcade', 'peluchera']:
            return jsonify({'error': 'Tipo de máquina inválido'}), 400
        
        if status not in ['activa', 'mantenimiento', 'inactiva']:
            return jsonify({'error': 'Estado inválido'}), 400
        
        if not (0 <= float(porcentaje_restaurante) <= 100):
            return jsonify({'error': 'El porcentaje del restaurante debe estar entre 0 y 100'}), 400
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        # Verificar que la máquina existe
        cursor.execute("SELECT id FROM Machine WHERE id = %s", (maquina_id,))
        if not cursor.fetchone():
            return jsonify({'error': 'Máquina no encontrada'}), 404
        
        # Verificar nombre duplicado
        cursor.execute("SELECT id FROM Machine WHERE name = %s AND id != %s", (name, maquina_id))
        if cursor.fetchone():
            return jsonify({'error': 'Ya existe otra máquina con ese nombre'}), 400
        
        # Verificar que el local existe
        cursor.execute("SELECT id FROM Location WHERE id = %s", (location_id,))
        if not cursor.fetchone():
            return jsonify({'error': 'El local especificado no existe'}), 400
        
        # Actualizar máquina
        cursor.execute("""
            UPDATE Machine 
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
            # Si es el valor por defecto, eliminar el registro específico
            cursor.execute("DELETE FROM MaquinaPorcentajeRestaurante WHERE maquina_id = %s", (maquina_id,))
        
        connection.commit()
        
        return jsonify({
            'success': True,
            'message': 'Máquina actualizada correctamente'
        })
        
    except Exception as e:
        print(f"❌ Error actualizando máquina: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/maquinas/<int:maquina_id>', methods=['DELETE'])
def eliminar_maquina(maquina_id):
    """Eliminar una máquina"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        # Verificar que la máquina existe
        cursor.execute("SELECT name FROM Machine WHERE id = %s", (maquina_id,))
        maquina = cursor.fetchone()
        if not maquina:
            return jsonify({'error': 'Máquina no encontrada'}), 404
        
        # Verificar si la máquina tiene uso histórico (para prevenir eliminación accidental)
        cursor.execute("SELECT COUNT(*) as uso_count FROM TurnUsage WHERE machineId = %s", (maquina_id,))
        uso_count = cursor.fetchone()['uso_count']
        
        if uso_count > 0:
            return jsonify({
                'error': f'No se puede eliminar la máquina "{maquina["name"]}". Tiene {uso_count} usos registrados en el historial.'
            }), 400
        
        # Eliminar registros relacionados primero
        cursor.execute("DELETE FROM MaquinaPorcentajeRestaurante WHERE maquina_id = %s", (maquina_id,))
        cursor.execute("DELETE FROM MaquinaPropietario WHERE maquina_id = %s", (maquina_id,))
        cursor.execute("DELETE FROM ErrorReport WHERE machineId = %s", (maquina_id,))
        
        # Eliminar máquina
        cursor.execute("DELETE FROM Machine WHERE id = %s", (maquina_id,))
        
        connection.commit()
        
        print(f"✅ Máquina eliminada: {maquina['name']} (ID: {maquina_id})")
        
        return jsonify({
            'success': True,
            'message': 'Máquina eliminada correctamente'
        })
        
    except Exception as e:
        print(f"❌ Error eliminando máquina: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/maquinas/<int:maquina_id>/propietarios', methods=['PUT'])
def actualizar_propietarios_maquina(maquina_id):
    """Actualizar la distribución de propietarios de una máquina"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        distribucion = data.get('distribucion', [])  # Lista de {propietario_id, porcentaje_propiedad}
        
        # Validar que la suma de porcentajes sea 100%
        total_porcentaje = sum(item['porcentaje_propiedad'] for item in distribucion)
        if abs(total_porcentaje - 100.00) > 0.01:
            return jsonify({'error': 'La suma de los porcentajes debe ser exactamente 100%'}), 400
        
        # Validar que no se excedan los límites por propietario
        for item in distribucion:
            if not (0 <= item['porcentaje_propiedad'] <= 100):
                return jsonify({'error': f'Porcentaje inválido para propietario {item["propietario_id"]}'}), 400
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        # Verificar que la máquina existe
        cursor.execute("SELECT id, name FROM Machine WHERE id = %s", (maquina_id,))
        maquina = cursor.fetchone()
        if not maquina:
            return jsonify({'error': 'Máquina no encontrada'}), 404
        
        # Verificar que todos los propietarios existen
        propietario_ids = [item['propietario_id'] for item in distribucion]
        if propietario_ids:
            placeholders = ','.join(['%s'] * len(propietario_ids))
            cursor.execute(f"SELECT id FROM Propietarios WHERE id IN ({placeholders})", propietario_ids)
            propietarios_existentes = cursor.fetchall()
            if len(propietarios_existentes) != len(propietario_ids):
                return jsonify({'error': 'Uno o más propietarios no existen'}), 400
        
        # Eliminar distribución anterior
        cursor.execute("DELETE FROM MaquinaPropietario WHERE maquina_id = %s", (maquina_id,))
        
        # Insertar nueva distribución
        for item in distribucion:
            cursor.execute("""
                INSERT INTO MaquinaPropietario (maquina_id, propietario_id, porcentaje_propiedad)
                VALUES (%s, %s, %s)
            """, (maquina_id, item['propietario_id'], item['porcentaje_propiedad']))
        
        connection.commit()
        
        # Registrar el cambio en el log
        print(f"✅ Distribución actualizada para máquina {maquina['name']} (ID: {maquina_id})")
        
        return jsonify({
            'success': True,
            'message': 'Distribución de propietarios actualizada correctamente'
        })
        
    except Exception as e:
        print(f"❌ Error actualizando propietarios de máquina: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# ==================== APIS PARA PROPIETARIOS ====================

@app.route('/api/propietarios', methods=['GET'])
def obtener_propietarios():
    """Obtener todos los propietarios"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("SELECT * FROM Propietarios ORDER BY nombre")
        propietarios = cursor.fetchall()
        
        return jsonify(propietarios)
        
    except Exception as e:
        print(f"❌ Error obteniendo propietarios: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# ==================== APIS PARA LOCALES ====================

@app.route('/api/locales', methods=['GET'])
def obtener_todos_locales():
    """Obtener todos los locales"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("SELECT id, name, address, city, status FROM Location ORDER BY name")
        locales = cursor.fetchall()
        
        return jsonify(locales)
        
    except Exception as e:
        print(f"❌ Error obteniendo locales: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# ==================== APIS PARA ESTADÍSTICAS DE MÁQUINAS ====================

@app.route('/api/maquinas/estadisticas', methods=['GET'])
def obtener_estadisticas_maquinas():
    """Obtener estadísticas para las gráficas de máquinas"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        # Estadísticas por estado
        cursor.execute("""
            SELECT 
                status,
                COUNT(*) as cantidad
            FROM Machine
            GROUP BY status
        """)
        estadisticas_estado = cursor.fetchall()
        
        # Estadísticas por tipo
        cursor.execute("""
            SELECT 
                type,
                COUNT(*) as cantidad
            FROM Machine
            GROUP BY type
        """)
        estadisticas_tipo = cursor.fetchall()
        
        # Convertir a formato para gráficas
        estado_data = {
            'activa': 0,
            'mantenimiento': 0,
            'inactiva': 0
        }
        
        for item in estadisticas_estado:
            estado_data[item['status']] = item['cantidad']
        
        tipo_data = {}
        for item in estadisticas_tipo:
            tipo_data[item['type']] = item['cantidad']
        
        return jsonify({
            'estado': estado_data,
            'tipo': tipo_data
        })
        
    except Exception as e:
        print(f"❌ Error obteniendo estadísticas de máquinas: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# ==================== RUTAS ADICIONALES PARA NAVEGACIÓN ====================

@app.route('/admin/maquinas/inventario')
def mostrar_inventario_maquinas():
    """Redirigir a la gestión de máquinas"""
    return redirect(url_for('mostrar_gestion_maquinas'))

# ==================== RUTAS PARA GESTIÓN DE LIQUIDACIONES ====================

@app.route('/admin/ventas/liquidaciones')
def mostrar_liquidaciones():
    if not session.get('logged_in'):
        return redirect(url_for('mostrar_login'))
    
    # Verificar que el usuario sea admin
    if session.get('user_role') != 'admin':
        return redirect(url_for('mostrar_local'))
    
    hora_colombia = get_colombia_time()
    return render_template('ventas/liquidaciones.html',
                         nombre_usuario=session.get('user_name', 'Administrador'),
                         local_usuario=session.get('user_local', 'Sistema'),
                         hora_actual=hora_colombia.strftime('%H:%M:%S'),
                         fecha_actual=hora_colombia.strftime('%Y-%m-%d'))


# ==================== APIS PARA LIQUIDACIONES ====================

@app.route('/api/ventas-liquidadas', methods=['GET'])
def obtener_ventas_liquidadas():
    """Obtener datos REALES de ventas de paquetes para liquidaciones - VERSIÓN CORREGIDA"""
    connection = None
    cursor = None
    try:
        # Obtener parámetros de filtrado
        fecha_inicio = request.args.get('fechaInicio', datetime.now().strftime('%Y-%m-%d'))
        fecha_fin = request.args.get('fechaFin', datetime.now().strftime('%Y-%m-%d'))
        pagina = int(request.args.get('pagina', 1))
        por_pagina = int(request.args.get('porPagina', 50))
        orden = request.args.get('orden', 'fecha')
        direccion = request.args.get('direccion', 'desc')
        busqueda = request.args.get('busqueda', '')
        
        offset = (pagina - 1) * por_pagina
        
        print(f"📊 Solicitando ventas liquidadas desde {fecha_inicio} hasta {fecha_fin}")
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        # CONSULTA CORREGIDA - Más simple y robusta
        query = """
            SELECT 
                DATE(qh.fecha_hora) as fecha,
                COALESCE(tp.name, 'Sin paquete') as paquete_nombre,
                COALESCE(tp.price, 0) as precio_unitario,
                COUNT(*) as cantidad_paquetes,
                COALESCE(SUM(tp.price), 0) as ingresos_totales,
                COALESCE(u.name, 'Sistema') as vendedor,
                qh.qr_name
            FROM QRHistory qh
            LEFT JOIN Users u ON qh.user_id = u.id
            LEFT JOIN QRCode qr ON qr.code = qh.qr_code
            LEFT JOIN TurnPackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
        """
        params = [fecha_inicio, fecha_fin]
        
        if busqueda:
            query += " AND (tp.name LIKE %s OR u.name LIKE %s OR qh.qr_name LIKE %s)"
            params.extend([f"%{busqueda}%", f"%{busqueda}%", f"%{busqueda}%"])
        
        query += " GROUP BY DATE(qh.fecha_hora), tp.name, tp.price, u.name, qh.qr_name"
        
        # Contar total de registros
        try:
            count_query = "SELECT COUNT(*) as total FROM (" + query + ") as subquery"
            print(f"🔍 Count query: {count_query}")
            print(f"🔍 Params: {params}")
            cursor.execute(count_query, params)
            count_result = cursor.fetchone()
            total_registros = count_result['total'] if count_result else 0
            print(f"✅ Total registros encontrados: {total_registros}")
        except Exception as count_error:
            print(f"⚠️ Error en count query: {count_error}")
            total_registros = 0
        
        # Aplicar ordenación
        order_mapping = {
            'fecha': 'fecha',
            'paquete': 'paquete_nombre', 
            'ingresos': 'ingresos_totales'
        }
        order_field = order_mapping.get(orden, 'fecha')
        query += f" ORDER BY {order_field} {direccion.upper()}"
        
        # Aplicar paginación
        query += " LIMIT %s OFFSET %s"
        params.extend([por_pagina, offset])
        
        print(f"🔍 Main query: {query}")
        print(f"🔍 Params: {params}")
        
        # Ejecutar consulta principal
        cursor.execute(query, params)
        ventas = cursor.fetchall()
        print(f"✅ Ventas encontradas: {len(ventas)}")
        
        # Calcular distribución de ingresos - VERSIÓN SIMPLIFICADA
        datos_liquidados = []
        total_ingresos = 0
        total_ingresos_30 = 0
        total_ingresos_35 = 0
        total_ingresos_restaurante = 0
        total_ingresos_proveedor = 0
        
        for venta in ventas:
            try:
                ingresos_totales = float(venta['ingresos_totales'] or 0)
                total_ingresos += ingresos_totales
                
                # DISTRIBUCIÓN SIMPLIFICADA - Basada en el nombre del paquete
                paquete_nombre = str(venta['paquete_nombre'] or '')
                
                # Si es paquete P1-P10, distribuimos 50/50 entre 30% y 35% como ejemplo
                # En producción esto debería basarse en el uso real de máquinas
                if any(str(i) in paquete_nombre for i in range(1, 11)):
                    # Distribución ejemplo: 50% en máquinas 30%, 50% en máquinas 35%
                    ingresos_30 = ingresos_totales * 0.5
                    ingresos_35 = ingresos_totales * 0.5
                else:
                    # Para otros paquetes, distribución diferente
                    ingresos_30 = ingresos_totales * 0.3
                    ingresos_35 = ingresos_totales * 0.7
                
                ingresos_restaurante = (ingresos_30 * 0.30) + (ingresos_35 * 0.35)
                ingresos_proveedor = ingresos_totales - ingresos_restaurante
                
                total_ingresos_30 += ingresos_30
                total_ingresos_35 += ingresos_35
                total_ingresos_restaurante += ingresos_restaurante
                total_ingresos_proveedor += ingresos_proveedor
                
                datos_liquidados.append({
                    'fecha': venta['fecha'].isoformat() if venta['fecha'] else None,
                    'paquete_nombre': paquete_nombre,
                    'precio_unitario': float(venta['precio_unitario'] or 0),
                    'cantidad_paquetes': int(venta['cantidad_paquetes'] or 0),
                    'ingresos_totales': ingresos_totales,
                    'ingresos_30_porciento': round(ingresos_30),
                    'ingresos_35_porciento': round(ingresos_35),
                    'ingresos_restaurante': round(ingresos_restaurante),
                    'ingresos_proveedor': round(ingresos_proveedor),
                    'vendedor': venta['vendedor'] or 'Sistema',
                    'qr_nombre': venta['qr_name'] or 'Sin nombre'
                })
                
            except Exception as venta_error:
                print(f"⚠️ Error procesando venta: {venta_error}")
                print(f"⚠️ Venta data: {venta}")
                continue
        
        print(f"✅ Datos liquidados procesados: {len(datos_liquidados)}")
        
        return jsonify({
            'datos': datos_liquidados,
            'totalRegistros': total_registros,
            'totalIngresos': round(total_ingresos),
            'totalIngresos30': round(total_ingresos_30),
            'totalIngresos35': round(total_ingresos_35),
            'totalIngresosRestaurante': round(total_ingresos_restaurante),
            'totalIngresosProveedor': round(total_ingresos_proveedor),
            'estadisticas': {
                'totalVentas': len(datos_liquidados),
                'periodo': f"{fecha_inicio} a {fecha_fin}"
            }
        })
        
    except Exception as e:
        print(f"❌ Error crítico en obtener_ventas_liquidadas: {str(e)}")
        import traceback
        traceback.print_exc()
        sentry_sdk.capture_exception(e)
        return jsonify({'error': f'Error interno del servidor: {str(e)}'}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/ventas-liquidadas-simple', methods=['GET'])
def obtener_ventas_liquidadas_simple():
    """Versión simple de ventas liquidadas - SOLO PARA DEBUG"""
    connection = None
    cursor = None
    try:
        fecha_inicio = request.args.get('fechaInicio', datetime.now().strftime('%Y-%m-%d'))
        fecha_fin = request.args.get('fechaFin', datetime.now().strftime('%Y-%m-%d'))
        
        print(f"🔍 DEBUG: Solicitando datos simples desde {fecha_inicio} hasta {fecha_fin}")
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        # Consulta MUY simple para debug
        cursor.execute("""
            SELECT 
                DATE(fecha_hora) as fecha,
                COUNT(*) as total_ventas
            FROM QRHistory 
            WHERE DATE(fecha_hora) BETWEEN %s AND %s
            GROUP BY DATE(fecha_hora)
            ORDER BY fecha DESC
            LIMIT 10
        """, (fecha_inicio, fecha_fin))
        
        ventas_simples = cursor.fetchall()
        
        # Crear datos de ejemplo basados en las ventas reales
        datos_ejemplo = []
        for venta in ventas_simples:
            datos_ejemplo.append({
                'fecha': venta['fecha'].isoformat() if venta['fecha'] else None,
                'paquete_nombre': 'Paquete Ejemplo',
                'precio_unitario': 10000,
                'cantidad_paquetes': venta['total_ventas'],
                'ingresos_totales': venta['total_ventas'] * 10000,
                'ingresos_30_porciento': (venta['total_ventas'] * 10000) * 0.5,
                'ingresos_35_porciento': (venta['total_ventas'] * 10000) * 0.5,
                'ingresos_restaurante': (venta['total_ventas'] * 10000) * 0.325,  # Promedio
                'ingresos_proveedor': (venta['total_ventas'] * 10000) * 0.675,
                'vendedor': 'Sistema',
                'qr_nombre': 'QR Ejemplo'
            })
        
        return jsonify({
            'datos': datos_ejemplo,
            'totalRegistros': len(datos_ejemplo),
            'totalIngresos': sum(item['ingresos_totales'] for item in datos_ejemplo),
            'mensaje': 'Datos de ejemplo para debug'
        })
        
    except Exception as e:
        print(f"❌ Error en versión simple: {str(e)}")
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/debug-tablas', methods=['GET'])
def debug_tablas():
    """Debug: Verificar qué tablas y datos existen"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'No connection'}), 500
            
        cursor = get_db_cursor(connection)
        
        # Verificar tablas existentes
        cursor.execute("SHOW TABLES")
        tablas = cursor.fetchall()
        
        # Verificar datos en QRHistory
        cursor.execute("SELECT COUNT(*) as total FROM QRHistory")
        total_qrhistory = cursor.fetchone()
        
        # Verificar datos en TurnPackage
        cursor.execute("SELECT COUNT(*) as total FROM TurnPackage")
        total_turnpackage = cursor.fetchone()
        
        # Verificar datos en QRCode
        cursor.execute("SELECT COUNT(*) as total FROM QRCode")
        total_qrcode = cursor.fetchone()
        
        # Ejemplo de datos recientes
        cursor.execute("SELECT * FROM QRHistory ORDER BY fecha_hora DESC LIMIT 5")
        ejemplos = cursor.fetchall()
        
        return jsonify({
            'tablas_existentes': tablas,
            'total_qrhistory': total_qrhistory,
            'total_turnpackage': total_turnpackage,
            'total_qrcode': total_qrcode,
            'ejemplos_recientes': ejemplos
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/liquidaciones-avanzadas', methods=['GET'])
def obtener_liquidaciones_avanzadas():
    """Obtener liquidaciones con distribución REAL por máquinas"""
    connection = None
    cursor = None
    try:
        fecha_inicio = request.args.get('fechaInicio', datetime.now().strftime('%Y-%m-%d'))
        fecha_fin = request.args.get('fechaFin', datetime.now().strftime('%Y-%m-%d'))
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        # Consulta para obtener distribución REAL por máquinas
        query = """
            SELECT 
                DATE(qh.fecha_hora) as fecha,
                m.name as maquina_nombre,
                mpr.porcentaje_restaurante,
                COUNT(*) as usos,
                COUNT(DISTINCT qh.qr_code) as paquetes_vendidos,
                SUM(tp.price) as ingresos_totales
            FROM QRHistory qh
            JOIN TurnUsage tu ON tu.qrCodeId = (SELECT id FROM QRCode WHERE code = qh.qr_code LIMIT 1)
            JOIN Machine m ON tu.machineId = m.id
            JOIN QRCode qr ON qr.code = qh.qr_code
            JOIN TurnPackage tp ON qr.turnPackageId = tp.id
            LEFT JOIN MaquinaPorcentajeRestaurante mpr ON m.id = mpr.maquina_id
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
            GROUP BY DATE(qh.fecha_hora), m.name, mpr.porcentaje_restaurante
            ORDER BY fecha DESC, ingresos_totales DESC
        """
        
        cursor.execute(query, (fecha_inicio, fecha_fin))
        datos_maquinas = cursor.fetchall()
        
        # Procesar datos para liquidaciones
        liquidaciones = []
        total_ingresos = 0
        total_restaurante = 0
        total_proveedor = 0
        
        for dato in datos_maquinas:
            ingresos_totales = dato['ingresos_totales'] or 0
            porcentaje_restaurante = dato['porcentaje_restaurante'] or 35.00
            
            ingresos_restaurante = ingresos_totales * (porcentaje_restaurante / 100)
            ingresos_proveedor = ingresos_totales - ingresos_restaurante
            
            total_ingresos += ingresos_totales
            total_restaurante += ingresos_restaurante
            total_proveedor += ingresos_proveedor
            
            liquidaciones.append({
                'fecha': dato['fecha'].isoformat() if dato['fecha'] else None,
                'maquina_nombre': dato['maquina_nombre'],
                'porcentaje_restaurante': porcentaje_restaurante,
                'usos': dato['usos'],
                'paquetes_vendidos': dato['paquetes_vendidos'],
                'ingresos_totales': ingresos_totales,
                'ingresos_restaurante': round(ingresos_restaurante),
                'ingresos_proveedor': round(ingresos_proveedor)
            })
        
        return jsonify({
            'liquidaciones': liquidaciones,
            'estadisticas': {
                'total_ingresos': total_ingresos,
                'total_restaurante': round(total_restaurante),
                'total_proveedor': round(total_proveedor),
                'total_maquinas': len(datos_maquinas),
                'periodo': f"{fecha_inicio} a {fecha_fin}"
            }
        })
        
    except Exception as e:
        print(f"Error al obtener liquidaciones avanzadas: {str(e)}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

    # ==================== APIS PARA LIQUIDACIONES COMPLETAS ====================

@app.route('/api/liquidaciones/calcular', methods=['POST'])
def calcular_liquidacion():
    """Calcular liquidación completa con distribución real por propietarios Y desglose detallado"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        fecha_inicio = data.get('fecha_inicio')
        fecha_fin = data.get('fecha_fin')
        
        if not fecha_inicio or not fecha_fin:
            return jsonify({'error': 'Fechas de inicio y fin son requeridas'}), 400
        
        print(f"🧮 Calculando liquidación desde {fecha_inicio} hasta {fecha_fin}")
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        # 1. OBTENER VENTAS DEL PERÍODO (QR únicos para evitar duplicados)
        # CORREGIDO: Usar GROUP BY en lugar de DISTINCT con ORDER BY problemático
        cursor.execute("""
            SELECT 
                qh.qr_code,
                qr.turnPackageId as paquete_id,
                tp.name as paquete_nombre,
                tp.price as precio_paquete,
                tp.turns as turnos_paquete,
                DATE(qh.fecha_hora) as fecha_venta,
                u.name as vendedor,
                qh.qr_name,
                qh.user_name,  -- Usar user_name en lugar de local que no existe
                MAX(qh.fecha_hora) as ultima_fecha  -- Para ordenar
            FROM QRHistory qh
            JOIN QRCode qr ON qr.code = qh.qr_code
            JOIN TurnPackage tp ON tp.id = qr.turnPackageId
            LEFT JOIN Users u ON qh.user_id = u.id
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
            GROUP BY qh.qr_code, qr.turnPackageId, tp.name, tp.price, tp.turns, 
                     DATE(qh.fecha_hora), u.name, qh.qr_name, qh.user_name
            ORDER BY ultima_fecha DESC
        """, (fecha_inicio, fecha_fin))
        
        ventas = cursor.fetchall()
        print(f"📦 Encontradas {len(ventas)} ventas únicas")
        
        # 2. OBTENER USO DE MÁQUINAS PARA CADA QR CON DESGLOSE
        liquidacion_detallada = []
        desglose_por_fecha = {}
        total_ingresos = 0
        
        for venta in ventas:
            qr_code = venta['qr_code']
            precio_paquete = float(venta['precio_paquete'] or 0)
            fecha_venta = venta['fecha_venta'].isoformat() if venta['fecha_venta'] else 'Sin fecha'
            
            # Inicializar desglose por fecha
            if fecha_venta not in desglose_por_fecha:
                desglose_por_fecha[fecha_venta] = {
                    'total_ventas': 0,
                    'ingresos_totales': 0,
                    'detalles_ventas': []
                }
            
            desglose_por_fecha[fecha_venta]['total_ventas'] += 1
            desglose_por_fecha[fecha_venta]['ingresos_totales'] += precio_paquete
            
            # Obtener máquinas usadas con este QR
            cursor.execute("""
                SELECT 
                    tu.machineId as maquina_id,
                    m.name as maquina_nombre,
                    m.type as tipo_maquina,
                    COUNT(*) as turnos_usados
                FROM TurnUsage tu
                JOIN Machine m ON m.id = tu.machineId
                WHERE tu.qrCodeId = (SELECT id FROM QRCode WHERE code = %s LIMIT 1)
                AND DATE(tu.usedAt) BETWEEN %s AND %s
                GROUP BY tu.machineId, m.name, m.type
            """, (qr_code, fecha_inicio, fecha_fin))
            
            usos_maquinas = cursor.fetchall()
            
            # Si no hay usos registrados, distribuir igualmente entre máquinas activas
            if not usos_maquinas:
                cursor.execute("""
                    SELECT id, name, type 
                    FROM Machine 
                    WHERE status = 'activa'
                """)
                maquinas_activas = cursor.fetchall()
                
                if maquinas_activas:
                    turnos_por_maquina = venta['turnos_paquete'] / len(maquinas_activas)
                    for maquina in maquinas_activas:
                        usos_maquinas.append({
                            'maquina_id': maquina['id'],
                            'maquina_nombre': maquina['name'],
                            'tipo_maquina': maquina['type'],
                            'turnos_usados': turnos_por_maquina
                        })
            
            # Distribuir el precio del paquete proporcionalmente a los turnos usados
            total_turnos_qr = sum(uso['turnos_usados'] for uso in usos_maquinas)
            distribucion_venta = []
            
            if total_turnos_qr > 0:
                for uso in usos_maquinas:
                    proporcion = uso['turnos_usados'] / total_turnos_qr
                    ingresos_maquina = precio_paquete * proporcion
                    
                    detalle = {
                        'qr_code': qr_code,
                        'paquete_nombre': venta['paquete_nombre'],
                        'precio_paquete': precio_paquete,
                        'maquina_id': uso['maquina_id'],
                        'maquina_nombre': uso['maquina_nombre'],
                        'tipo_maquina': uso['tipo_maquina'],
                        'turnos_usados': uso['turnos_usados'],
                        'proporcion': proporcion,
                        'ingresos_maquina': ingresos_maquina,
                        'fecha_venta': fecha_venta,
                        'vendedor': venta['vendedor'],
                        'qr_nombre': venta['qr_name']
                    }
                    
                    liquidacion_detallada.append(detalle)
                    distribucion_venta.append({
                        'maquina_nombre': uso['maquina_nombre'],
                        'tipo_maquina': uso['tipo_maquina'],
                        'turnos_usados': uso['turnos_usados'],
                        'proporcion': proporcion,
                        'ingresos_asignados': ingresos_maquina
                    })
                    
                    total_ingresos += ingresos_maquina
            
            # Agregar al desglose por fecha
            desglose_por_fecha[fecha_venta]['detalles_ventas'].append({
                'qr_code': qr_code,
                'paquete_nombre': venta['paquete_nombre'],
                'precio_paquete': precio_paquete,
                'turnos_paquete': venta['turnos_paquete'],
                'vendedor': venta['vendedor'],
                'qr_nombre': venta['qr_name'],
                'distribucion_maquinas': distribucion_venta
            })
        
        # 3. CALCULAR DISTRIBUCIÓN POR PROPIETARIOS CON DESGLOSE
        distribucion_final = {}
        resumen_maquinas = {}
        total_restaurante = 0
        total_proveedor = 0
        
        for detalle in liquidacion_detallada:
            maquina_id = detalle['maquina_id']
            ingresos_maquina = detalle['ingresos_maquina']
            
            if maquina_id not in resumen_maquinas:
                # Obtener información de la máquina
                cursor.execute("""
                    SELECT 
                        COALESCE(mpr.porcentaje_restaurante, 35.00) as porcentaje_restaurante
                    FROM Machine m
                    LEFT JOIN MaquinaPorcentajeRestaurante mpr ON m.id = mpr.maquina_id
                    WHERE m.id = %s
                """, (maquina_id,))
                
                info_maquina = cursor.fetchone()
                porcentaje_restaurante = float(info_maquina['porcentaje_restaurante']) if info_maquina else 35.00
                
                # Obtener propietarios de la máquina
                cursor.execute("""
                    SELECT 
                        p.id,
                        p.nombre,
                        mp.porcentaje_propiedad
                    FROM MaquinaPropietario mp
                    JOIN Propietarios p ON mp.propietario_id = p.id
                    WHERE mp.maquina_id = %s
                """, (maquina_id,))
                
                propietarios = cursor.fetchall()
                
                resumen_maquinas[maquina_id] = {
                    'maquina_nombre': detalle['maquina_nombre'],
                    'tipo_maquina': detalle['tipo_maquina'],
                    'porcentaje_restaurante': porcentaje_restaurante,
                    'propietarios': propietarios,
                    'ingresos_totales': 0,
                    'ingresos_restaurante': 0,
                    'ingresos_proveedor': 0,
                    'detalles': []
                }
            
            resumen_maquinas[maquina_id]['ingresos_totales'] += ingresos_maquina
            resumen_maquinas[maquina_id]['detalles'].append(detalle)
        
        # Calcular distribución para cada máquina
        for maquina_id, datos_maquina in resumen_maquinas.items():
            ingresos_maquina = datos_maquina['ingresos_totales']
            porcentaje_restaurante = datos_maquina['porcentaje_restaurante']
            
            # Calcular parte del restaurante
            ingresos_restaurante = ingresos_maquina * (porcentaje_restaurante / 100)
            ingresos_proveedor = ingresos_maquina - ingresos_restaurante
            
            datos_maquina['ingresos_restaurante'] = ingresos_restaurante
            datos_maquina['ingresos_proveedor'] = ingresos_proveedor
            
            total_restaurante += ingresos_restaurante
            total_proveedor += ingresos_proveedor
            
            # Distribuir entre propietarios
            for propietario in datos_maquina['propietarios']:
                propietario_nombre = propietario['nombre']
                porcentaje_propiedad = propietario['porcentaje_propiedad']
                
                if propietario_nombre not in distribucion_final:
                    distribucion_final[propietario_nombre] = {
                        'total_ingresos': 0,
                        'detalles_maquinas': []
                    }
                
                monto_propietario = float(ingresos_proveedor) * (float(porcentaje_propiedad) / 100)
                distribucion_final[propietario_nombre]['total_ingresos'] += monto_propietario
                
                distribucion_final[propietario_nombre]['detalles_maquinas'].append({
                    'maquina_id': maquina_id,
                    'maquina_nombre': datos_maquina['maquina_nombre'],
                    'tipo_maquina': datos_maquina['tipo_maquina'],
                    'ingresos_maquina': ingresos_maquina,
                    'porcentaje_propiedad': porcentaje_propiedad,
                    'monto_propietario': monto_propietario,
                    'porcentaje_restaurante': porcentaje_restaurante,
                    'ingresos_restaurante': ingresos_restaurante,
                    'ingresos_proveedor': ingresos_proveedor
                })
        
        # 4. PREPARAR DATOS PARA EL FRONTEND
        datos_simplificados = []
        for detalle in liquidacion_detallada:
            # Buscar el porcentaje de restaurante para esta máquina
            porcentaje_restaurante = resumen_maquinas.get(detalle['maquina_id'], {}).get('porcentaje_restaurante', 35.00)
            
            datos_simplificados.append({
                'fecha': detalle['fecha_venta'],
                'paquete_nombre': detalle['paquete_nombre'],
                'precio_unitario': detalle['precio_paquete'],
                'cantidad_paquetes': 1,
                'ingresos_totales': detalle['precio_paquete'],
                'ingresos_30_porciento': detalle['ingresos_maquina'] * 0.3,  # Para máquinas 30%
                'ingresos_35_porciento': detalle['ingresos_maquina'] * 0.35, # Para máquinas 35%
                'ingresos_restaurante': detalle['ingresos_maquina'] * (porcentaje_restaurante / 100),
                'ingresos_proveedor': detalle['ingresos_maquina'] * (1 - porcentaje_restaurante / 100),
                'vendedor': detalle['vendedor'],
                'qr_nombre': detalle['qr_nombre'],
                'maquina_nombre': detalle['maquina_nombre'],
                'tipo_maquina': detalle['tipo_maquina'],
                'porcentaje_restaurante_real': porcentaje_restaurante
            })
        
        return jsonify({
            'success': True,
            'periodo': {
                'fecha_inicio': fecha_inicio,
                'fecha_fin': fecha_fin,
                'total_ingresos': round(total_ingresos, 2),
                'total_restaurante': round(total_restaurante, 2),
                'total_proveedor': round(total_proveedor, 2),
                'total_ventas': len(ventas),
                'maquinas_utilizadas': len(resumen_maquinas)
            },
            'distribucion_propietarios': distribucion_final,
            'resumen_maquinas': resumen_maquinas,
            'desglose_por_fecha': desglose_por_fecha,
            'datos_tabla': datos_simplificados,
            'detalle_completo': liquidacion_detallada,
            'estadisticas': {
                'ticket_promedio': round(total_ingresos / len(ventas), 2) if ventas else 0,
                'porcentaje_restaurante_promedio': round((total_restaurante / total_ingresos) * 100, 2) if total_ingresos > 0 else 0,
                'maquinas_activas': len(resumen_maquinas)
            }
        })
        
    except Exception as e:
        print(f"❌ Error calculando liquidación: {str(e)}")
        import traceback
        traceback.print_exc()
        sentry_sdk.capture_exception(e)
        return jsonify({'error': f'Error calculando liquidación: {str(e)}'}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# ==================== APIS PARA OBTENER DATOS DE PROPIETARIOS ====================

@app.route('/api/propietarios/maquina/<int:maquina_id>', methods=['GET'])
def obtener_propietarios_maquina(maquina_id):
    """Obtener los propietarios de una máquina específica"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("""
            SELECT 
                p.id,
                p.nombre,
                mp.porcentaje_propiedad
            FROM MaquinaPropietario mp
            JOIN Propietarios p ON mp.propietario_id = p.id
            WHERE mp.maquina_id = %s
            ORDER BY mp.porcentaje_propiedad DESC
        """, (maquina_id,))
        
        propietarios = cursor.fetchall()
        
        return jsonify(propietarios)
        
    except Exception as e:
        print(f"Error al obtener propietarios: {str(e)}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

        # RUTA INTERFAZ DE REPORTES

@app.route('/admin/ventas/reportes')
def mostrar_reportes():
    if not session.get('logged_in'):
        return redirect(url_for('mostrar_login'))
    
    # Verificar que el usuario sea admin
    if session.get('user_role') != 'admin':
        return redirect(url_for('mostrar_local'))
    
    hora_colombia = get_colombia_time()
    return render_template('ventas/reportes.html',
                         nombre_usuario=session.get('user_name', 'Administrador'),
                         local_usuario=session.get('user_local', 'Sistema'),
                         hora_actual=hora_colombia.strftime('%H:%M:%S'),
                         fecha_actual=hora_colombia.strftime('%Y-%m-%d'))

@app.route('/api/reportes/liquidaciones', methods=['POST'])
def obtener_reporte_liquidaciones():
    """Obtener reporte completo de liquidaciones con todos los datos"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        fecha_inicio = data.get('fecha_inicio')
        fecha_fin = data.get('fecha_fin')
        tipo_reporte = data.get('tipo_reporte', 'resumen')
        agrupacion = data.get('agrupacion', 'diario')
        
        if not fecha_inicio or not fecha_fin:
            return jsonify({'error': 'Fechas de inicio y fin son requeridas'}), 400
        
        print(f"📊 Generando reporte desde {fecha_inicio} hasta {fecha_fin}")
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        # 1. OBTENER DATOS BASE
        cursor.execute("""
            SELECT 
                qh.qr_code,
                qr.turnPackageId as paquete_id,
                tp.name as paquete_nombre,
                tp.price as precio_paquete,
                tp.turns as turnos_paquete,
                DATE(qh.fecha_hora) as fecha_venta,
                u.name as vendedor,
                qh.qr_name,
                qh.user_name,
                MAX(qh.fecha_hora) as ultima_fecha
            FROM QRHistory qh
            JOIN QRCode qr ON qr.code = qh.qr_code
            JOIN TurnPackage tp ON tp.id = qr.turnPackageId
            LEFT JOIN Users u ON qh.user_id = u.id
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
            GROUP BY qh.qr_code, qr.turnPackageId, tp.name, tp.price, tp.turns, 
                     DATE(qh.fecha_hora), u.name, qh.qr_name, qh.user_name
            ORDER BY ultima_fecha DESC
        """, (fecha_inicio, fecha_fin))
        
        ventas = cursor.fetchall()
        print(f"📦 Encontradas {len(ventas)} ventas únicas")
        
        # 2. PROCESAR DATOS PARA REPORTE COMPLETO
        reporte_data = procesar_datos_reporte(connection, cursor, ventas, fecha_inicio, fecha_fin, tipo_reporte, agrupacion)
        
        return jsonify({
            'success': True,
            'periodo': {
                'fecha_inicio': fecha_inicio,
                'fecha_fin': fecha_fin,
                'total_ingresos': reporte_data['totales']['ingresos_totales'],
                'total_restaurante': reporte_data['totales']['ganancia_restaurante'],
                'total_proveedor': reporte_data['totales']['ganancia_proveedor'],
                'total_ventas': len(ventas),
                'maquinas_utilizadas': reporte_data['totales']['maquinas_utilizadas']
            },
            'distribucion_propietarios': reporte_data['distribucion_propietarios'],
            'resumen_maquinas': reporte_data['resumen_maquinas'],
            'datos_tabla': reporte_data['datos_tabla'],
            'estadisticas_avanzadas': reporte_data['estadisticas_avanzadas'],
            'agrupacion': agrupacion,
            'tipo_reporte': tipo_reporte
        })
        
    except Exception as e:
        print(f"❌ Error generando reporte: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Error generando reporte: {str(e)}'}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

def procesar_datos_reporte(connection, cursor, ventas, fecha_inicio, fecha_fin, tipo_reporte, agrupacion):
    """Procesar datos para el reporte completo"""
    
    # Estructura base del reporte
    reporte_data = {
        'totales': {
            'ingresos_totales': 0,
            'ganancia_restaurante': 0,
            'ganancia_proveedor': 0,
            'maquinas_utilizadas': 0
        },
        'distribucion_propietarios': {},
        'resumen_maquinas': {},
        'datos_tabla': [],
        'estadisticas_avanzadas': {}
    }
    
    # Procesar cada venta
    for venta in ventas:
        qr_code = venta['qr_code']
        precio_paquete = float(venta['precio_paquete'] or 0)
        
        # Obtener máquinas usadas con este QR
        cursor.execute("""
            SELECT 
                tu.machineId as maquina_id,
                m.name as maquina_nombre,
                m.type as tipo_maquina,
                COUNT(*) as turnos_usados
            FROM TurnUsage tu
            JOIN Machine m ON m.id = tu.machineId
            WHERE tu.qrCodeId = (SELECT id FROM QRCode WHERE code = %s LIMIT 1)
            AND DATE(tu.usedAt) BETWEEN %s AND %s
            GROUP BY tu.machineId, m.name, m.type
        """, (qr_code, fecha_inicio, fecha_fin))
        
        usos_maquinas = cursor.fetchall()
        
        # Si no hay usos registrados, distribuir entre máquinas activas
        if not usos_maquinas:
            cursor.execute("""
                SELECT id, name, type 
                FROM Machine 
                WHERE status = 'activa'
            """)
            maquinas_activas = cursor.fetchall()
            
            if maquinas_activas:
                turnos_por_maquina = venta['turnos_paquete'] / len(maquinas_activas)
                for maquina in maquinas_activas:
                    usos_maquinas.append({
                        'maquina_id': maquina['id'],
                        'maquina_nombre': maquina['name'],
                        'tipo_maquina': maquina['type'],
                        'turnos_usados': turnos_por_maquina
                    })
        
        # Distribuir el precio del paquete
        total_turnos_qr = sum(uso['turnos_usados'] for uso in usos_maquinas)
        
        if total_turnos_qr > 0:
            for uso in usos_maquinas:
                proporcion = uso['turnos_usados'] / total_turnos_qr
                ingresos_maquina = precio_paquete * proporcion
                
                # Obtener información de distribución para esta máquina
                distribucion = obtener_distribucion_maquina(cursor, uso['maquina_id'], ingresos_maquina)
                
                # Actualizar totales
                reporte_data['totales']['ingresos_totales'] += ingresos_maquina
                reporte_data['totales']['ganancia_restaurante'] += distribucion['ingresos_restaurante']
                reporte_data['totales']['ganancia_proveedor'] += distribucion['ingresos_proveedor']
                
                # Actualizar distribución por propietarios
                for propietario, monto in distribucion['distribucion_propietarios'].items():
                    if propietario not in reporte_data['distribucion_propietarios']:
                        reporte_data['distribucion_propietarios'][propietario] = {
                            'total_ingresos': 0,
                            'detalles_maquinas': []
                        }
                    reporte_data['distribucion_propietarios'][propietario]['total_ingresos'] += monto
                    reporte_data['distribucion_propietarios'][propietario]['detalles_maquinas'].append({
                        'maquina_nombre': uso['maquina_nombre'],
                        'monto_propietario': monto
                    })
                
                # Actualizar resumen de máquinas
                maquina_id = uso['maquina_id']
                if maquina_id not in reporte_data['resumen_maquinas']:
                    reporte_data['resumen_maquinas'][maquina_id] = {
                        'maquina_nombre': uso['maquina_nombre'],
                        'tipo_maquina': uso['tipo_maquina'],
                        'ingresos_totales': 0,
                        'ingresos_restaurante': 0,
                        'ingresos_proveedor': 0,
                        'detalles': []
                    }
                
                reporte_data['resumen_maquinas'][maquina_id]['ingresos_totales'] += ingresos_maquina
                reporte_data['resumen_maquinas'][maquina_id]['ingresos_restaurante'] += distribucion['ingresos_restaurante']
                reporte_data['resumen_maquinas'][maquina_id]['ingresos_proveedor'] += distribucion['ingresos_proveedor']
                reporte_data['resumen_maquinas'][maquina_id]['detalles'].append({
                    'qr_code': qr_code,
                    'paquete_nombre': venta['paquete_nombre'],
                    'ingresos_asignados': ingresos_maquina
                })
                
                # Agregar a datos de tabla
                reporte_data['datos_tabla'].append({
                    'fecha': venta['fecha_venta'].isoformat() if venta['fecha_venta'] else 'Sin fecha',
                    'paquete_nombre': venta['paquete_nombre'],
                    'maquina_nombre': uso['maquina_nombre'],
                    'tipo_maquina': uso['tipo_maquina'],
                    'turnos_usados': uso['turnos_usados'],
                    'ingresos_totales': ingresos_maquina,
                    'porcentaje_restaurante': distribucion['porcentaje_restaurante'],
                    'ingresos_restaurante': distribucion['ingresos_restaurante'],
                    'ingresos_proveedor': distribucion['ingresos_proveedor'],
                    'propietario': list(distribucion['distribucion_propietarios'].keys())[0] if distribucion['distribucion_propietarios'] else 'No asignado',
                    'vendedor': venta['vendedor'],
                    'qr_nombre': venta['qr_name']
                })
    
    # Calcular estadísticas avanzadas
    reporte_data['totales']['maquinas_utilizadas'] = len(reporte_data['resumen_maquinas'])
    reporte_data['estadisticas_avanzadas'] = calcular_estadisticas_avanzadas(reporte_data, len(ventas))
    
    return reporte_data

def obtener_distribucion_maquina(cursor, maquina_id, ingresos_maquina):
    """Obtener distribución de ingresos para una máquina específica"""
    
    # Obtener porcentaje de restaurante
    cursor.execute("""
        SELECT 
            COALESCE(mpr.porcentaje_restaurante, 35.00) as porcentaje_restaurante
        FROM Machine m
        LEFT JOIN MaquinaPorcentajeRestaurante mpr ON m.id = mpr.maquina_id
        WHERE m.id = %s
    """, (maquina_id,))
    
    info_maquina = cursor.fetchone()
    porcentaje_restaurante = float(info_maquina['porcentaje_restaurante']) if info_maquina else 35.00
    
    # Calcular distribución restaurante/proveedor
    ingresos_restaurante = ingresos_maquina * (porcentaje_restaurante / 100)
    ingresos_proveedor = ingresos_maquina - ingresos_restaurante
    
    # Obtener propietarios y distribuir
    cursor.execute("""
        SELECT 
            p.nombre,
            mp.porcentaje_propiedad
        FROM MaquinaPropietario mp
        JOIN Propietarios p ON mp.propietario_id = p.id
        WHERE mp.maquina_id = %s
    """, (maquina_id,))
    
    propietarios = cursor.fetchall()
    distribucion_propietarios = {}
    
    for propietario in propietarios:
        monto_propietario = ingresos_proveedor * (float(propietario['porcentaje_propiedad']) / 100)
        distribucion_propietarios[propietario['nombre']] = monto_propietario
    
    return {
        'porcentaje_restaurante': porcentaje_restaurante,
        'ingresos_restaurante': ingresos_restaurante,
        'ingresos_proveedor': ingresos_proveedor,
        'distribucion_propietarios': distribucion_propietarios
    }

def calcular_estadisticas_avanzadas(reporte_data, total_ventas):
    """Calcular estadísticas avanzadas para el reporte"""
    
    totales = reporte_data['totales']
    maquinas = reporte_data['resumen_maquinas']
    
    # Calcular métricas básicas
    ticket_promedio = totales['ingresos_totales'] / total_ventas if total_ventas > 0 else 0
    porcentaje_restaurante_promedio = (totales['ganancia_restaurante'] / totales['ingresos_totales']) * 100 if totales['ingresos_totales'] > 0 else 0
    
    # Calcular métricas por máquina
    ingresos_por_maquina = [maquina['ingresos_totales'] for maquina in maquinas.values()]
    maquina_mas_rentable = max(maquinas.values(), key=lambda x: x['ingresos_totales']) if maquinas else None
    maquina_menos_rentable = min(maquinas.values(), key=lambda x: x['ingresos_totales']) if maquinas else None
    
    # Calcular eficiencia por máquina
    eficiencia_maquinas = {}
    for maquina_id, datos in maquinas.items():
        ventas_maquina = len(datos['detalles'])
        eficiencia = datos['ingresos_totales'] / ventas_maquina if ventas_maquina > 0 else 0
        eficiencia_maquinas[maquina_id] = {
            'nombre': datos['maquina_nombre'],
            'eficiencia': eficiencia,
            'ventas': ventas_maquina
        }
    
    return {
        'ticket_promedio': round(ticket_promedio, 2),
        'porcentaje_restaurante_promedio': round(porcentaje_restaurante_promedio, 2),
        'maquina_mas_rentable': {
            'nombre': maquina_mas_rentable['maquina_nombre'] if maquina_mas_rentable else 'N/A',
            'ingresos': maquina_mas_rentable['ingresos_totales'] if maquina_mas_rentable else 0
        },
        'maquina_menos_rentable': {
            'nombre': maquina_menos_rentable['maquina_nombre'] if maquina_menos_rentable else 'N/A',
            'ingresos': maquina_menos_rentable['ingresos_totales'] if maquina_menos_rentable else 0
        },
        'eficiencia_maquinas': eficiencia_maquinas,
        'distribucion_tipos_maquina': calcular_distribucion_tipos_maquina(maquinas)
    }

def calcular_distribucion_tipos_maquina(maquinas):
    """Calcular distribución de ingresos por tipo de máquina"""
    distribucion = {}
    
    for maquina in maquinas.values():
        tipo = maquina['tipo_maquina']
        if tipo not in distribucion:
            distribucion[tipo] = 0
        distribucion[tipo] += maquina['ingresos_totales']
    
    return distribucion

@app.route('/api/reportes/graficos', methods=['POST'])
def obtener_datos_graficos():
    """Obtener datos específicos para gráficos"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        fecha_inicio = data.get('fecha_inicio')
        fecha_fin = data.get('fecha_fin')
        tipo_grafico = data.get('tipo_grafico', 'distribucion')
        
        if not fecha_inicio or not fecha_fin:
            return jsonify({'error': 'Fechas de inicio y fin son requeridas'}), 400
        
        connection = get_db_connection()
        cursor = get_db_cursor(connection)
        
        datos_grafico = {}
        
        if tipo_grafico == 'distribucion':
            datos_grafico = obtener_datos_distribucion(cursor, fecha_inicio, fecha_fin)
        elif tipo_grafico == 'ventas_diarias':
            datos_grafico = obtener_ventas_diarias(cursor, fecha_inicio, fecha_fin)
        elif tipo_grafico == 'evolucion':
            datos_grafico = obtener_evolucion_ingresos(cursor, fecha_inicio, fecha_fin)
        elif tipo_grafico == 'paquetes':
            datos_grafico = obtener_ventas_paquetes(cursor, fecha_inicio, fecha_fin)
        
        return jsonify({
            'success': True,
            'tipo_grafico': tipo_grafico,
            'datos': datos_grafico
        })
        
    except Exception as e:
        print(f"❌ Error obteniendo datos de gráficos: {str(e)}")
        return jsonify({'error': f'Error obteniendo datos de gráficos: {str(e)}'}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

def obtener_datos_distribucion(cursor, fecha_inicio, fecha_fin):
    """Obtener datos para gráfico de distribución"""
    cursor.execute("""
        SELECT 
            SUM(tp.price) as ingresos_totales,
            COUNT(*) as total_ventas
        FROM QRHistory qh
        JOIN QRCode qr ON qr.code = qh.qr_code
        JOIN TurnPackage tp ON tp.id = qr.turnPackageId
        WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
    """, (fecha_inicio, fecha_fin))
    
    totales = cursor.fetchone()
    ingresos_totales = float(totales['ingresos_totales'] or 0)
    
    # Calcular distribución estimada (35% restaurante, 65% proveedores)
    ingresos_restaurante = ingresos_totales * 0.35
    ingresos_proveedor = ingresos_totales * 0.65
    
    return {
        'labels': ['Restaurante', 'Proveedores'],
        'datasets': [{
            'data': [ingresos_restaurante, ingresos_proveedor],
            'backgroundColor': ['#10b981', '#8b5cf6']
        }]
    }

def obtener_ventas_diarias(cursor, fecha_inicio, fecha_fin):
    """Obtener ventas diarias para gráfico de barras"""
    cursor.execute("""
        SELECT 
            DATE(qh.fecha_hora) as fecha,
            SUM(tp.price) as ingresos,
            COUNT(*) as ventas
        FROM QRHistory qh
        JOIN QRCode qr ON qr.code = qh.qr_code
        JOIN TurnPackage tp ON tp.id = qr.turnPackageId
        WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
        GROUP BY DATE(qh.fecha_hora)
        ORDER BY fecha
    """, (fecha_inicio, fecha_fin))
    
    ventas_diarias = cursor.fetchall()
    
    labels = []
    datos_ingresos = []
    
    for venta in ventas_diarias:
        labels.append(venta['fecha'].strftime('%d/%m'))
        datos_ingresos.append(float(venta['ingresos'] or 0))
    
    return {
        'labels': labels,
        'datasets': [{
            'label': 'Ingresos Diarios',
            'data': datos_ingresos,
            'backgroundColor': 'rgba(59, 130, 246, 0.5)',
            'borderColor': 'rgb(59, 130, 246)',
            'borderWidth': 2
        }]
    }

def obtener_evolucion_ingresos(cursor, fecha_inicio, fecha_fin):
    """Obtener datos para gráfico de evolución"""
    # Agrupar por semanas
    cursor.execute("""
        SELECT 
            YEARWEEK(qh.fecha_hora) as semana,
            SUM(tp.price) as ingresos_totales,
            SUM(tp.price) * 0.35 as ingresos_restaurante,
            SUM(tp.price) * 0.65 as ingresos_proveedor
        FROM QRHistory qh
        JOIN QRCode qr ON qr.code = qh.qr_code
        JOIN TurnPackage tp ON tp.id = qr.turnPackageId
        WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
        GROUP BY YEARWEEK(qh.fecha_hora)
        ORDER BY semana
        LIMIT 8
    """, (fecha_inicio, fecha_fin))
    
    semanas = cursor.fetchall()
    
    labels = []
    datos_totales = []
    datos_proveedor = []
    
    for semana in semanas:
        labels.append(f"Sem {len(labels) + 1}")
        datos_totales.append(float(semana['ingresos_totales'] or 0))
        datos_proveedor.append(float(semana['ingresos_proveedor'] or 0))
    
    return {
        'labels': labels,
        'datasets': [
            {
                'label': 'Ingresos Totales',
                'data': datos_totales,
                'borderColor': 'rgb(59, 130, 246)',
                'backgroundColor': 'rgba(59, 130, 246, 0.1)',
                'tension': 0.4,
                'fill': True
            },
            {
                'label': 'Ganancia Proveedores',
                'data': datos_proveedor,
                'borderColor': 'rgb(139, 92, 246)',
                'backgroundColor': 'rgba(139, 92, 246, 0.1)',
                'tension': 0.4,
                'fill': True
            }
        ]
    }

def obtener_ventas_paquetes(cursor, fecha_inicio, fecha_fin):
    """Obtener ventas por paquete para gráfico de dona"""
    cursor.execute("""
        SELECT 
            tp.name as paquete,
            COUNT(*) as ventas,
            SUM(tp.price) as ingresos
        FROM QRHistory qh
        JOIN QRCode qr ON qr.code = qh.qr_code
        JOIN TurnPackage tp ON tp.id = qr.turnPackageId
        WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
        GROUP BY tp.name, tp.id
        ORDER BY ventas DESC
    """, (fecha_inicio, fecha_fin))
    
    paquetes = cursor.fetchall()
    
    labels = []
    datos_ventas = []
    
    for paquete in paquetes:
        labels.append(paquete['paquete'])
        datos_ventas.append(paquete['ventas'])
    
    return {
        'labels': labels,
        'datasets': [{
            'data': datos_ventas,
            'backgroundColor': [
                '#3b82f6', '#10b981', '#8b5cf6', '#f59e0b', '#ef4444',
                '#06b6d4', '#84cc16', '#f97316', '#6366f1', '#ec4899'
            ]
        }]
    }

@app.route('/api/reportes/estadisticas-tiempo-real', methods=['GET'])
def obtener_estadisticas_tiempo_real():
    """Obtener estadísticas en tiempo real para el dashboard"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = get_db_cursor(connection)
        
        # Ventas del día actual
        from datetime import datetime
        hoy = datetime.now().date()
        cursor.execute("""
            SELECT 
                COUNT(*) as ventas_hoy,
                COALESCE(SUM(tp.price), 0) as ingresos_hoy
            FROM QRHistory qh
            JOIN QRCode qr ON qr.code = qh.qr_code
            JOIN TurnPackage tp ON tp.id = qr.turnPackageId
            WHERE DATE(qh.fecha_hora) = %s
        """, (hoy,))
        
        ventas_hoy = cursor.fetchone()
        
        # Ventas del mes actual
        primer_dia_mes = hoy.replace(day=1)
        cursor.execute("""
            SELECT 
                COUNT(*) as ventas_mes,
                COALESCE(SUM(tp.price), 0) as ingresos_mes
            FROM QRHistory qh
            JOIN QRCode qr ON qr.code = qh.qr_code
            JOIN TurnPackage tp ON tp.id = qr.turnPackageId
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
        """, (primer_dia_mes, hoy))
        
        ventas_mes = cursor.fetchone()
        
        # Máquinas activas
        cursor.execute("""
            SELECT COUNT(*) as maquinas_activas
            FROM Machine 
            WHERE status = 'activa'
        """)
        
        maquinas_activas = cursor.fetchone()
        
        return jsonify({
            'success': True,
            'estadisticas': {
                'ventas_hoy': ventas_hoy['ventas_hoy'] or 0,
                'ingresos_hoy': float(ventas_hoy['ingresos_hoy'] or 0),
                'ventas_mes': ventas_mes['ventas_mes'] or 0,
                'ingresos_mes': float(ventas_mes['ingresos_mes'] or 0),
                'maquinas_activas': maquinas_activas['maquinas_activas'] or 0
            },
            'ultima_actualizacion': datetime.now().isoformat()
        })
        
    except Exception as e:
        print(f"❌ Error obteniendo estadísticas tiempo real: {str(e)}")
        return jsonify({'error': f'Error obteniendo estadísticas: {str(e)}'}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# ==================== APIS PARA ESP32-CAM ====================

@app.route('/api/esp32/status', methods=['GET'])
def esp32_status():
    """Endpoint para verificar estado del servidor desde ESP32"""
    return jsonify({
        'status': 'online',
        'message': 'Servidor funcionando correctamente',
        'timestamp': get_colombia_time().isoformat()
    })

@app.route('/api/esp32/registrar-uso', methods=['POST'])
def esp32_registrar_uso():
    """Registrar uso de máquina desde ESP32"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        qr_code = data.get('qr_code')
        machine_id = data.get('machine_id')
        
        if not qr_code or not machine_id:
            return jsonify({'error': 'QR code y machine_id son requeridos'}), 400
        
        print(f"🎮 Registrando uso desde ESP32 - QR: {qr_code}, Máquina: {machine_id}")
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        # Verificar que el QR existe y tiene turnos
        cursor.execute("SELECT id FROM QRCode WHERE code = %s", (qr_code,))
        qr_data = cursor.fetchone()
        if not qr_data:
            return jsonify({'error': 'Código QR no encontrado'}), 404
        
        qr_id = qr_data['id']
        cursor.execute("SELECT turns_remaining FROM UserTurns WHERE qr_code_id = %s", (qr_id,))
        turnos_data = cursor.fetchone()
        
        if not turnos_data or turnos_data['turns_remaining'] <= 0:
            return jsonify({'error': 'No hay turnos disponibles'}), 400
        
        # Registrar uso
        cursor.execute("INSERT INTO TurnUsage (qrCodeId, machineId) VALUES (%s, %s)", (qr_id, machine_id))
        cursor.execute("UPDATE UserTurns SET turns_remaining = turns_remaining - 1 WHERE qr_code_id = %s", (qr_id,))
        
        # Actualizar última fecha de uso de la máquina
        cursor.execute("UPDATE Machine SET dateLastQRUsed = NOW() WHERE id = %s", (machine_id,))
        
        connection.commit()
        
        # Obtener información actualizada
        cursor.execute("""
            SELECT ut.turns_remaining, tp.name as package_name 
            FROM UserTurns ut 
            JOIN QRCode qr ON qr.id = ut.qr_code_id
            LEFT JOIN TurnPackage tp ON ut.package_id = tp.id
            WHERE ut.qr_code_id = %s
        """, (qr_id,))
        
        info_actualizada = cursor.fetchone()
        
        return jsonify({
            'success': True,
            'message': 'Turno utilizado correctamente',
            'turns_remaining': info_actualizada['turns_remaining'],
            'package_name': info_actualizada['package_name'],
            'machine_id': machine_id
        })
        
    except Exception as e:
        print(f"❌ Error registrando uso desde ESP32: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/esp32/machine-status/<int:machine_id>', methods=['GET'])
def esp32_machine_status(machine_id):
    """Obtener estado de una máquina específica"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("""
            SELECT 
                m.id, m.name, m.status, m.errorNote,
                l.name as location_name,
                COUNT(tu.id) as usos_hoy
            FROM Machine m
            LEFT JOIN Location l ON m.location_id = l.id
            LEFT JOIN TurnUsage tu ON tu.machineId = m.id AND DATE(tu.usedAt) = CURDATE()
            WHERE m.id = %s
            GROUP BY m.id, m.name, m.status, m.errorNote, l.name
        """, (machine_id,))
        
        machine_data = cursor.fetchone()
        
        if not machine_data:
            return jsonify({'error': 'Máquina no encontrada'}), 404
        
        return jsonify({
            'machine_id': machine_data['id'],
            'machine_name': machine_data['name'],
            'status': machine_data['status'],
            'location': machine_data['location_name'],
            'usos_hoy': machine_data['usos_hoy'],
            'error_note': machine_data['errorNote'],
            'online': True
        })
        
    except Exception as e:
        print(f"❌ Error obteniendo estado de máquina: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

            # ==================== APIS PARA ESP32 y TFT ====================


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
            FROM Machine m
            LEFT JOIN Location l ON m.location_id = l.id
            LEFT JOIN TurnUsage tu ON tu.machineId = m.id AND DATE(tu.usedAt) = CURDATE()
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
        print(f"❌ Error estado máquina TFT: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/tft/qr-info/<qr_code>', methods=['GET'])
def tft_qr_info(qr_code):
    """Obtener información detallada de QR para mostrar en TFT"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión'}), 500
            
        cursor = get_db_cursor(connection)
        
        # Buscar información completa del QR
        cursor.execute("""
            SELECT 
                qr.code,
                COALESCE(qr.qr_name, 'Usuario') as user_name,
                ut.turns_remaining,
                ut.total_turns,
                tp.name as package_name,
                tp.price as package_price,
                tp.turns as package_turns,
                COALESCE(u.name, 'Sistema') as seller_name,
                MAX(qh.fecha_hora) as last_used
            FROM QRCode qr
            LEFT JOIN UserTurns ut ON ut.qr_code_id = qr.id
            LEFT JOIN TurnPackage tp ON ut.package_id = tp.id
            LEFT JOIN QRHistory qh ON qh.qr_code = qr.code
            LEFT JOIN Users u ON qh.user_id = u.id
            WHERE qr.code = %s
            GROUP BY qr.code, qr.qr_name, ut.turns_remaining, ut.total_turns, 
                     tp.name, tp.price, tp.turns, u.name
        """, (qr_code,))
        
        qr_data = cursor.fetchone()
        
        if not qr_data:
            return jsonify({
                'exists': False,
                'message': 'QR no encontrado en el sistema'
            })
        
        # Determinar mensaje según turnos disponibles
        turns_remaining = qr_data['turns_remaining'] or 0
        message = ""
        
        if turns_remaining <= 0:
            message = "No tienes turnos disponibles. Visita la caja para recargar."
        elif turns_remaining <= 2:
            message = f"Solo te quedan {turns_remaining} turnos. ¡Aprovecha!"
        else:
            message = f"Tienes {turns_remaining} turnos disponibles. ¡Disfruta!"
        
        return jsonify({
            'exists': True,
            'qr_code': qr_data['code'],
            'user_name': qr_data['user_name'],
            'turns_remaining': turns_remaining,
            'total_turns': qr_data['total_turns'] or 0,
            'package_name': qr_data['package_name'] or 'Sin paquete',
            'package_price': float(qr_data['package_price'] or 0),
            'package_turns': qr_data['package_turns'] or 0,
            'seller_name': qr_data['seller_name'],
            'last_used': qr_data['last_used'].isoformat() if qr_data['last_used'] else None,
            'message': message,
            'instructions': 'Escanea el código en la máquina para usar un turno'
        })
        
    except Exception as e:
        print(f"❌ Error info QR TFT: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/tft/register-usage', methods=['POST'])
def tft_register_usage():
    """Registrar uso desde pantalla TFT"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        qr_code = data.get('qr_code')
        machine_id = data.get('machine_id')
        
        if not qr_code or not machine_id:
            return jsonify({'error': 'QR y máquina requeridos'}), 400
        
        print(f"🎮 TFT: Registrando uso - QR: {qr_code}, Máquina: {machine_id}")
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión'}), 500
            
        cursor = get_db_cursor(connection)
        
        # 1. Verificar QR
        cursor.execute("SELECT id FROM QRCode WHERE code = %s", (qr_code,))
        qr_data = cursor.fetchone()
        if not qr_data:
            return jsonify({
                'success': False,
                'message': 'QR no encontrado',
                'error_type': 'qr_not_found'
            })
        
        qr_id = qr_data['id']
        
        # 2. Verificar turnos
        cursor.execute("SELECT turns_remaining FROM UserTurns WHERE qr_code_id = %s", (qr_id,))
        turns_data = cursor.fetchone()
        if not turns_data or turns_data['turns_remaining'] <= 0:
            return jsonify({
                'success': False,
                'message': 'No hay turnos disponibles',
                'error_type': 'no_turns',
                'turns_remaining': 0
            })
        
        # 3. Registrar uso
        cursor.execute("INSERT INTO TurnUsage (qrCodeId, machineId) VALUES (%s, %s)", (qr_id, machine_id))
        cursor.execute("UPDATE UserTurns SET turns_remaining = turns_remaining - 1 WHERE qr_code_id = %s", (qr_id,))
        
        # 4. Actualizar máquina
        cursor.execute("UPDATE Machine SET dateLastQRUsed = NOW() WHERE id = %s", (machine_id,))
        
        connection.commit()
        
        # 5. Obtener información actualizada
        cursor.execute("""
            SELECT 
                ut.turns_remaining,
                tp.name as package_name,
                qr.qr_name as user_name
            FROM UserTurns ut
            JOIN QRCode qr ON qr.id = ut.qr_code_id
            LEFT JOIN TurnPackage tp ON ut.package_id = tp.id
            WHERE ut.qr_code_id = %s
        """, (qr_id,))
        
        updated_info = cursor.fetchone()
        
        # Determinar mensaje
        new_turns = updated_info['turns_remaining']
        message = ""
        
        if new_turns <= 0:
            message = "¡Último turno usado! Recarga en caja."
        elif new_turns == 1:
            message = "Te queda 1 turno. ¡Úsalo sabiamente!"
        else:
            message = f"Te quedan {new_turns} turnos. ¡Sigue divirtiéndote!"
        
        return jsonify({
            'success': True,
            'message': 'Turno usado exitosamente',
            'turns_remaining': new_turns,
            'user_name': updated_info['user_name'] or 'Usuario',
            'package_name': updated_info['package_name'] or 'Sin paquete',
            'machine_id': machine_id,
            'usage_message': message,
            'next_instruction': 'Puedes seguir jugando o escanear otro QR',
            'timestamp': get_colombia_time().isoformat()
        })
        
    except Exception as e:
        print(f"❌ Error registro uso TFT: {e}")
        if connection:
            connection.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


# Iniciar servidor
if __name__ == '__main__':
    print("🚀 Iniciando servidor Flask en http://127.0.0.1:5000")
    app.run(debug=True, port=5000, host='0.0.0.0')  