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

app = Flask(__name__, template_folder='templates')

# ==================== CONFIGURACIÓN SENTRY ====================
sentry_sdk.init(
    dsn="https://5fc281c2ace4860969f2f1f6fa10039d@o4510071013310464.ingest.us.sentry.io/4510071047454720",
    integrations=[FlaskIntegration()],
    traces_sample_rate=1.0,
    send_default_pii=True,
    environment="development"
)

sentry_sdk.logger.info('This is an info log message')
sentry_sdk.logger.warning('This is a warning message')
sentry_sdk.logger.error('This is an error message')

# ============================================================
# Configuración del logger
colombia = pytz.timezone("America/Bogota")
fecha_hora = datetime.now(colombia).strftime("%Y-%m-%d %H:%M:%S")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(
    __name__,
    static_folder=os.path.join(BASE_DIR, 'static'),
    template_folder=os.path.join(BASE_DIR, 'templates')
)
app.secret_key = 'maquinasmedellin_secret_key_2024'
CORS(app)

# Configuración del pool de conexiones
db_config = {
    "host": "localhost",
    "user": "root",
    "password": "Dattebayo",
    "database": "maquinasmedellin",
    "pool_name": "maquinas_pool",
    "pool_size": 5
}

# Crear el pool de conexiones
try:
    connection_pool = pooling.MySQLConnectionPool(**db_config)
    print("✅ Pool de conexiones creado exitosamente")
except Exception as e:
    print(f"❌ Error creando pool de conexiones: {e}")
    connection_pool = None

# Función para obtener conexión
def get_db_connection():
    try:
        if connection_pool:
            return connection_pool.get_connection()
        else:
            return mysql.connector.connect(
                host="localhost",
                user="root",
                password="Dattebayo",
                database="maquinasmedellin"
            )
    except Exception as e:
        print(f"❌ Error obteniendo conexión: {e}")
        return None

# Función para obtener cursor
def get_db_cursor(connection):
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SET time_zone = '-05:00'")
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
    return render_template('local.html',
                           nombre_usuario=session.get('user_name', 'Usuario'),
                           local_usuario=session.get('user_local', 'El Mekatiadero'))

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
        
        cursor.execute("SELECT turns, price FROM TurnPackage WHERE id = %s", (paquete_id,))
        paquete = cursor.fetchone()
        if not paquete:
            return jsonify({'error': 'Paquete no encontrado'}), 404
        
        turns, price = paquete['turns'], paquete['price']

        cursor.execute("SELECT id FROM QRCode WHERE code = %s", (codigo_qr,))
        qr_existente = cursor.fetchone()
        
        if not qr_existente:
            cursor.execute("""
                INSERT INTO QRCode (code, remainingTurns, isActive, turnPackageId)
                VALUES (%s, %s, 1, %s)
            """, (codigo_qr, turns, paquete_id))
            connection.commit()
            qr_id = cursor.lastrowid
        else:
            qr_id = qr_existente['id']
            cursor.execute("""
                UPDATE QRCode
                SET remainingTurns = remainingTurns + %s,
                    turnPackageId = %s
                WHERE id = %s
            """, (turns, paquete_id, qr_id))
            connection.commit()
        
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
    connection = None
    cursor = None
    try:
        print(f"🔍 Verificando QR: {qr_code}")
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        cursor.execute("SELECT id, code, remainingTurns, isActive FROM QRCode WHERE code = %s", (qr_code,))
        qr_data = cursor.fetchone()
        
        if not qr_data:
            print(f"❌ QR no encontrado en tabla QRCode: {qr_code}")
            return jsonify({'existe': False})
        
        print(f"✅ QR encontrado en base de datos: {qr_data}")
        
        qr_id = qr_data['id']
        cursor.execute("""
            SELECT ut.*, tp.name as package_name, tp.turns, tp.price
            FROM UserTurns ut
            LEFT JOIN TurnPackage tp ON ut.package_id = tp.id
            WHERE ut.qr_code_id = %s
        """, (qr_id,))
        resultado = cursor.fetchone()
        
        if resultado:
            return jsonify({
                'existe': True,
                'turns_remaining': resultado['turns_remaining'],
                'total_turns': resultado['total_turns'],
                'package_name': resultado['package_name'],
                'package_turns': resultado['turns'],
                'package_price': resultado['price'],
                'qr_code': qr_code
            })
        else:
            return jsonify({
                'existe': True,
                'turns_remaining': 0,
                'total_turns': 0,
                'package_name': 'Sin paquete',
                'package_turns': 0,
                'package_price': 0,
                'qr_code': qr_code
            })
            
    except Exception as e:
        print(f"❌ Error verificando QR: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

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

        if not qr_code:
            return jsonify({'error': 'QR vacío'}), 400

        print(f"💾 Guardando QR en historial: {qr_code} por usuario {user_name}")

        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)

        cursor.execute("""
            INSERT INTO QRHistory (qr_code, user_id, user_name, local, fecha_hora)
            VALUES (%s, %s, %s, %s, NOW())
        """, (qr_code, user_id, user_name, local))
        connection.commit()

        return jsonify({'success': True, 'message': 'QR guardado en historial'})
    except Exception as e:
        print(f"❌ Error guardando QR en historial: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# Consultar historial de un QR
@app.route('/api/historial-qr/<qr_code>', methods=['GET'])
def historial_qr(qr_code):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        cursor.execute("""
            SELECT h.id, h.qr_code, h.fecha_hora, h.user_name, tp.name as package_name, ut.turns_remaining
            FROM QRHistory h
            LEFT JOIN QRCode q ON q.code = h.qr_code
            LEFT JOIN UserTurns ut ON ut.qr_code_id = q.id
            LEFT JOIN TurnPackage tp ON ut.package_id = tp.id
            WHERE h.qr_code = %s
            ORDER BY h.fecha_hora DESC
            LIMIT 10
        """, (qr_code,))
        return jsonify(cursor.fetchall())
    except Exception as e:
        print(f"❌ Error consultando historial: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# Consultar historial general de QR (últimos 20)
@app.route('/api/historial-completo', methods=['GET'])
def historial_completo():
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        cursor.execute("""
            SELECT qr_code, user_id, user_name, local, fecha_hora
            FROM QRHistory 
            ORDER BY fecha_hora DESC 
            LIMIT 20
        """)
        historial = cursor.fetchall()
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

# Agregar QR generados en lote al historial
@app.route('/api/guardar-multiples-qr', methods=['POST'])
def guardar_multiples_qr():
    connection = None
    cursor = None
    try:
        data = request.get_json()
        qr_codes = data.get('qr_codes', [])
        user_id = session.get('user_id')
        user_name = session.get('user_name', 'Usuario')
        local = session.get('user_local', 'El Mekatiadero')

        if not qr_codes:
            return jsonify({'error': 'Lista de QR vacía'}), 400

        print(f"💾 Guardando {len(qr_codes)} QR en el sistema")

        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)

        for qr_code in qr_codes:
            cursor.execute("SELECT id FROM QRCode WHERE code = %s", (qr_code,))
            qr_existente = cursor.fetchone()
            
            if not qr_existente:
                print(f"➕ Insertando nuevo QR: {qr_code}")
                cursor.execute("""
                    INSERT INTO QRCode (code, remainingTurns, isActive, turnPackageId)
                    VALUES (%s, %s, %s, %s)
                """, (qr_code, 0, 1, 1))
            else:
                print(f"✅ QR ya existe: {qr_code}")
            
            cursor.execute("""
                INSERT INTO QRHistory (qr_code, user_id, user_name, local, fecha_hora)
                VALUES (%s, %s, %s, %s, NOW())
            """, (qr_code, user_id, user_name, local))

        connection.commit()
        print(f"✅ {len(qr_codes)} QR guardados exitosamente")

        return jsonify({
            'success': True, 
            'message': f'{len(qr_codes)} QR guardados en el sistema',
            'count': len(qr_codes)
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
    """Reportar falla de máquina sin devolver turnos"""
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
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)

        # Insertar en tabla ErrorReport que ya existe
        cursor.execute("""
            INSERT INTO ErrorReport 
            (machineId, userId, description, reportedAt, isResolved)
            VALUES (%s, %s, %s, NOW(), FALSE)
        """, (machine_id, user_id, description))
        
        connection.commit()

        return jsonify({
            'success': True,
            'message': f'Falla reportada en {machine_name}. El equipo de mantenimiento ha sido notificado.'
        })
        
    except Exception as e:
        print(f"❌ Error reportando falla de máquina: {e}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/sales')
def mostrar_ventas():
    if not session.get('logged_in'):
        return redirect(url_for('mostrar_login'))
    return render_template('sales.html',
                           nombre_usuario=session.get('user_name', 'Usuario'),
                           local_usuario=session.get('user_local', 'El Mekatiadero'))

@app.route('/api/ventas')
def obtener_ventas():
    """Obtiene datos de ventas para el panel"""
    connection = None
    cursor = None
    try:
        fecha_inicio = request.args.get('fecha_inicio', datetime.now().strftime('%Y-%m-%d'))
        fecha_fin = request.args.get('fecha_fin', datetime.now().strftime('%Y-%m-%d'))
        
        print(f"📊 Solicitando ventas desde {fecha_inicio} hasta {fecha_fin}")
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        # Obtener ventas del rango de fechas
        cursor.execute("""
            SELECT 
                tp.name as paquete,
                tp.price as precio,
                tp.turns as turnos,
                u.name as vendedor,
                qh.fecha_hora,
                DATE(qh.fecha_hora) as fecha,
                DATE_FORMAT(qh.fecha_hora, '%%H:%%i') as hora
            FROM QRHistory qh
            JOIN Users u ON qh.user_id = u.id
            JOIN QRCode qr ON qr.code = qh.qr_code
            LEFT JOIN TurnPackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
            ORDER BY qh.fecha_hora DESC
        """, (fecha_inicio, fecha_fin))
        
        ventas = cursor.fetchall()
        print(f"✅ Encontradas {len(ventas)} ventas")
        
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
        
        # ... el resto del código se mantiene igual ...
        # Preparar datos para tabla
        ventas_detalle = []
        for venta in ventas:
            fecha_str = ""
            if venta['fecha']:
                if isinstance(venta['fecha'], str):
                    fecha_str = venta['fecha']
                else:
                    fecha_str = venta['fecha'].strftime('%d/%m/%Y')
            
            ventas_detalle.append({
                'fecha': fecha_str,
                'hora': venta['hora'] or '',
                'paquete': venta['paquete'] or 'Sin paquete',
                'precio': venta['precio'] or 0,
                'turnos': venta['turnos'] or 0,
                'vendedor': venta['vendedor'] or 'Desconocido',
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
    # Esta función generaría un CSV con los datos de ventas
    # Por simplicidad, aquí solo redirige a los datos JSON
    fecha = request.args.get('fecha', datetime.now().strftime('%Y-%m-%d'))
    return redirect(f'/api/ventas?fecha={fecha}')
    
# ==================== RUTAS PARA EL PANEL DE ADMINISTRACIÓN ====================

# RUTAS DE INTERFAZ ADMINISTRADOR

# Mostrar panel de administración
@app.route('/admin')
def mostrar_admin():
    if not session.get('logged_in'):
        return redirect(url_for('mostrar_login'))
    
    # Verificar que el usuario sea admin
    if session.get('user_role') != 'admin':
        return redirect(url_for('mostrar_local'))
    
    return render_template('admin/index.html',
                           nombre_usuario=session.get('user_name', 'Administrador'),
                           local_usuario=session.get('user_local', 'Sistema'))

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

# Iniciar servidor
if __name__ == '__main__':
    print("🚀 Iniciando servidor Flask en http://127.0.0.1:5000")
    app.run(debug=True, port=5000, host='0.0.0.0')  