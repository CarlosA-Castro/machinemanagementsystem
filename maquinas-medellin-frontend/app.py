from flask import Flask, request, jsonify, render_template, redirect, url_for, session
import mysql.connector
from mysql.connector import pooling
from flask_cors import CORS
import os
from datetime import datetime
import pytz
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration
import logging
from logging.handlers import RotatingFileHandler

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

# Admin
@app.route('/admin')
def mostrar_admin():
    if not session.get('logged_in'):
        return redirect(url_for('mostrar_login'))
    if session.get('user_role') != 'admin':
        return redirect(url_for('mostrar_local'))
    return render_template('admin.html',
                           nombre_usuario=session.get('user_name', 'Administrador'),
                           local_usuario=session.get('user_local', 'Sistema'))

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
    
# ==================== RUTAS PARA EL PANEL DE ADMINISTRACIÓN ====================

@app.route('/api/estadisticas-admin', methods=['GET'])
def obtener_estadisticas_admin():
    """Obtiene estadísticas reales para el panel de administración"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        # 1. Total de usuarios
        cursor.execute("SELECT COUNT(*) as total FROM Users")
        total_usuarios = cursor.fetchone()['total']
        
        # 2. Total de máquinas - CORREGIDO: usar status = 'activa' en lugar de isActive
        cursor.execute("SELECT COUNT(*) as total FROM machine WHERE status = 'activa'")
        total_maquinas = cursor.fetchone()['total']
        
        # 3. Paquetes vendidos hoy
        cursor.execute("""
            SELECT COUNT(*) as total 
            FROM QRHistory 
            WHERE DATE(fecha_hora) = CURDATE()
        """)
        paquetes_hoy = cursor.fetchone()['total']
        
        # 4. CORREGIDO: Usar ErrorReport en lugar de MachineFailures, y isResolved en lugar de resolved
        cursor.execute("""
            SELECT COUNT(*) as total 
            FROM ErrorReport 
            WHERE isResolved = 0
        """)
        incidencias_activas = cursor.fetchone()['total']
        
        return jsonify({
            'usuarios': total_usuarios,
            'maquinas': total_maquinas,
            'paquetes': paquetes_hoy,
            'incidencias': incidencias_activas
        })
        
    except Exception as e:
        app.logger.error(f"Error obteniendo estadísticas admin: {str(e)}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/locales', methods=['GET'])
def obtener_locales():
    """Obtiene lista de locales"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        cursor.execute("SELECT * FROM Location ORDER BY name")
        locales = cursor.fetchall()
        return jsonify(locales)
        
    except Exception as e:
        app.logger.error(f"Error obteniendo locales: {str(e)}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/usuarios', methods=['GET'])
def obtener_usuarios():
    """Obtiene lista completa de usuarios"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        cursor.execute("SELECT id, name, role, local, createdAt FROM Users ORDER BY name")
        usuarios = cursor.fetchall()
        return jsonify(usuarios)
        
    except Exception as e:
        app.logger.error(f"Error obteniendo usuarios: {str(e)}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/maquinas', methods=['GET'])
def obtener_maquinas():
    """Obtiene lista de máquinas"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        # CORREGIDO: usar el nombre correcto de la tabla (machine)
        cursor.execute("SELECT * FROM machine ORDER BY name")
        maquinas = cursor.fetchall()
        return jsonify(maquinas)
        
    except Exception as e:
        app.logger.error(f"Error obteniendo máquinas: {str(e)}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/incidencias', methods=['GET'])
def obtener_incidencias():
    """Obtiene incidencias recientes"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        cursor.execute("""
            SELECT mf.*, qr.code as qr_code, m.name as machine_name
            FROM MachineFailures mf
            JOIN QRCode qr ON mf.qr_code_id = qr.id
            JOIN machine m ON mf.machine_id = m.id
            ORDER BY mf.reported_at DESC
            LIMIT 50
        """)
        incidencias = cursor.fetchall()
        return jsonify(incidencias)
        
    except Exception as e:
        app.logger.error(f"Error obteniendo incidencias: {str(e)}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/paquetes', methods=['GET', 'POST'])
def gestionar_paquetes():
    """Obtiene lista de paquetes o crea nuevo paquete"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        if request.method == 'GET':
            # Obtener todos los paquetes
            cursor.execute("SELECT * FROM TurnPackage ORDER BY id")
            paquetes = cursor.fetchall()
            return jsonify(paquetes)
            
        elif request.method == 'POST':
            # Crear nuevo paquete
            data = request.get_json()
            nombre = data.get('nombre')
            turnos = data.get('turnos')
            precio = data.get('precio')
            activo = data.get('activo', True)
            
            if not all([nombre, turnos, precio]):
                return jsonify({'error': 'Faltan datos requeridos'}), 400
            
            cursor.execute("""
                INSERT INTO TurnPackage (name, turns, price, isActive)
                VALUES (%s, %s, %s, %s)
            """, (nombre, turnos, precio, activo))
            connection.commit()
            
            return jsonify({
                'success': True,
                'message': 'Paquete creado exitosamente',
                'id': cursor.lastrowid
            })
            
    except Exception as e:
        app.logger.error(f"Error gestionando paquetes: {str(e)}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/paquetes/<int:paquete_id>', methods=['PUT', 'DELETE'])
def gestionar_paquete_individual(paquete_id):
    """Actualizar o eliminar un paquete específico"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        if request.method == 'PUT':
            # Actualizar paquete
            data = request.get_json()
            nombre = data.get('nombre')
            turnos = data.get('turnos')
            precio = data.get('precio')
            activo = data.get('activo')
            
            cursor.execute("""
                UPDATE TurnPackage 
                SET name = %s, turns = %s, price = %s, isActive = %s
                WHERE id = %s
            """, (nombre, turnos, precio, activo, paquete_id))
            connection.commit()
            
            return jsonify({'success': True, 'message': 'Paquete actualizado exitosamente'})
            
        elif request.method == 'DELETE':
            # Eliminar paquete (solo lógico, cambiando isActive a False)
            cursor.execute("UPDATE TurnPackage SET isActive = FALSE WHERE id = %s", (paquete_id,))
            connection.commit()
            
            return jsonify({'success': True, 'message': 'Paquete desactivado exitosamente'})
            
    except Exception as e:
        app.logger.error(f"Error gestionando paquete individual: {str(e)}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/usuarios/<int:usuario_id>', methods=['GET', 'PUT', 'DELETE'])
def gestionar_usuario_individual(usuario_id):
    """Obtener, actualizar o eliminar un usuario específico"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        if request.method == 'GET':
            # Obtener usuario específico
            cursor.execute("SELECT id, name, role, local, createdAt FROM Users WHERE id = %s", (usuario_id,))
            usuario = cursor.fetchone()
            if not usuario:
                return jsonify({'error': 'Usuario no encontrado'}), 404
            return jsonify(usuario)
            
        elif request.method == 'PUT':
            # Actualizar usuario
            data = request.get_json()
            nombre = data.get('nombre')
            role = data.get('role')
            local = data.get('local')
            nueva_password = data.get('nueva_password')
            
            if not nombre or not role:
                return jsonify({'error': 'Nombre y rol son requeridos'}), 400
            
            # Construir query dinámicamente
            update_fields = []
            params = []
            
            update_fields.append("name = %s")
            params.append(nombre)
            
            update_fields.append("role = %s")
            params.append(role)
            
            if local:
                update_fields.append("local = %s")
                params.append(local)
            
            if nueva_password:
                update_fields.append("password = %s")
                params.append(nueva_password)
            
            params.append(usuario_id)
            
            query = f"UPDATE Users SET {', '.join(update_fields)} WHERE id = %s"
            cursor.execute(query, params)
            connection.commit()
            
            return jsonify({'success': True, 'message': 'Usuario actualizado exitosamente'})
            
        elif request.method == 'DELETE':
            # Eliminar usuario
            # Primero verificar que no sea el último admin
            cursor.execute("SELECT COUNT(*) as admin_count FROM Users WHERE role = 'admin'")
            admin_count = cursor.fetchone()['admin_count']
            
            cursor.execute("SELECT role FROM Users WHERE id = %s", (usuario_id,))
            usuario = cursor.fetchone()
            
            if usuario and usuario['role'] == 'admin' and admin_count <= 1:
                return jsonify({'error': 'No se puede eliminar el último administrador'}), 400
            
            cursor.execute("DELETE FROM Users WHERE id = %s", (usuario_id,))
            connection.commit()
            
            return jsonify({'success': True, 'message': 'Usuario eliminado exitosamente'})
            
    except Exception as e:
        app.logger.error(f"Error gestionando usuario individual: {str(e)}")
        sentry_sdk.capture_exception(e)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

@app.route('/api/usuarios/<int:usuario_id>/rol', methods=['PUT'])
def cambiar_rol_usuario(usuario_id):
    """Cambiar el rol de un usuario específico"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        data = request.get_json()
        nuevo_rol = data.get('role')
        
        if not nuevo_rol:
            return jsonify({'error': 'El nuevo rol es requerido'}), 400
        
        # Validar que el rol sea válido
        roles_validos = ['admin', 'cajero', 'admin_restaurante']
        if nuevo_rol not in roles_validos:
            return jsonify({'error': 'Rol no válido'}), 400
        
        # Verificar que no sea el último admin
        if nuevo_rol != 'admin':
            cursor.execute("SELECT COUNT(*) as admin_count FROM Users WHERE role = 'admin' AND id != %s", (usuario_id,))
            admin_count = cursor.fetchone()['admin_count']
            
            cursor.execute("SELECT role FROM Users WHERE id = %s", (usuario_id,))
            usuario_actual = cursor.fetchone()
            
            if usuario_actual and usuario_actual['role'] == 'admin' and admin_count == 0:
                return jsonify({'error': 'No se puede quitar el rol de admin al último administrador'}), 400
        
        cursor.execute("UPDATE Users SET role = %s WHERE id = %s", (nuevo_rol, usuario_id))
        connection.commit()
        
        return jsonify({'success': True, 'message': 'Rol actualizado exitosamente'})
        
    except Exception as e:
        app.logger.error(f"Error cambiando rol de usuario: {str(e)}")
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
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión a la base de datos'}), 500
            
        cursor = get_db_cursor(connection)
        
        data = request.get_json()
        nombre = data.get('nombre')
        password = data.get('password')
        role = data.get('role')
        local = data.get('local', 'El Mekatiadero')
        notes = data.get('notes', '')
        
        if not all([nombre, password, role]):
            return jsonify({'error': 'Nombre, contraseña y rol son requeridos'}), 400
        
        if len(password) < 6:
            return jsonify({'error': 'La contraseña debe tener al menos 6 caracteres'}), 400
        
        # Verificar si el usuario ya existe
        cursor.execute("SELECT id FROM Users WHERE name = %s", (nombre,))
        if cursor.fetchone():
            return jsonify({'error': 'Ya existe un usuario con ese nombre'}), 400
        
        # Obtener el ID del usuario que crea (desde la sesión)
        creado_por = session.get('user_id', 1)  
        
        cursor.execute("""
            INSERT INTO Users (name, password, role, local, createdBy, notes)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (nombre, password, role, local, creado_por, notes))
        connection.commit()
        
        return jsonify({
            'success': True,
            'message': 'Usuario creado exitosamente',
            'id': cursor.lastrowid
        })
        
    except Exception as e:
        app.logger.error(f"Error creando usuario: {str(e)}")
        sentry_sdk.capture_exception(e)
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