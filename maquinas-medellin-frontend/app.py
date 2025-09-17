from flask import Flask, request, jsonify, render_template, redirect, url_for, session
import mysql.connector
from flask_cors import CORS
import os
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(
    __name__,
    static_folder=os.path.join(BASE_DIR, 'static'),
    template_folder=os.path.join(BASE_DIR, 'templates')
)
app.secret_key = 'maquinasmedellin_secret_key_2024'
CORS(app)

# Conexión a la base de datos
try:
    db = mysql.connector.connect(
        host="localhost",
        user="root",
        password="Dattebayo",
        database="maquinasmedellin"
    )
    cursor = db.cursor(dictionary=True)
    print("✅ Conexión a BD exitosa")
except Exception as e:
    print(f"❌ Error conectando a BD: {e}")

# Crear tabla QRHistory si no existe
try:
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
    print("✅ Tabla QRHistory verificada/creada")
except Exception as e:
    print(f"❌ Error creando tabla QRHistory: {e}")

# Rutas
@app.route('/')
def mostrar_login():
    session.clear()
    return render_template('login.html')

# Procesa login
@app.route('/login', methods=['POST'])
def procesar_login():
    try:
        data = request.get_json()
        codigo = data.get('codigo')
        print(f"📨 Código recibido: {codigo}")

        if not codigo:
            return jsonify({"valido": False, "error": "no_codigo"}), 400

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
    try:
        cursor.execute("SELECT * FROM TurnPackage ORDER BY id")
        return jsonify(cursor.fetchall())
    except Exception as e:
        print(f"❌ Error obteniendo paquetes: {e}")
        return jsonify({'error': str(e)}), 500

# Asignar paquete a QR 
@app.route('/api/asignar-paquete', methods=['POST'])
def asignar_paquete():
    try:
        data = request.get_json()
        codigo_qr = data.get('codigo_qr')
        paquete_id = data.get('paquete_id')
        
        if not codigo_qr or not paquete_id:
            return jsonify({'error': 'Faltan datos requeridos'}), 400
        
        # Buscar info del paquete para traer turnos y precio
        cursor.execute("SELECT turns, price FROM TurnPackage WHERE id = %s", (paquete_id,))
        paquete = cursor.fetchone()
        if not paquete:
            return jsonify({'error': 'Paquete no encontrado'}), 404
        
        turns, price = paquete['turns'], paquete['price']

        # Verificar si el QR ya existe
        cursor.execute("SELECT id FROM QRCode WHERE code = %s", (codigo_qr,))
        qr_existente = cursor.fetchone()
        
        if not qr_existente:
            # Si es un QR nuevo lo guardamos con los turnos reales
            cursor.execute("""
                INSERT INTO QRCode (code, remainingTurns, isActive, turnPackageId)
                VALUES (%s, %s, 1, %s)
            """, (codigo_qr, turns, paquete_id))
            db.commit()
            qr_id = cursor.lastrowid
        else:
            # Si ya existe, actualizamos remainingTurns sumándole los del nuevo paquete
            qr_id = qr_existente['id']
            cursor.execute("""
                UPDATE QRCode
                SET remainingTurns = remainingTurns + %s,
                    turnPackageId = %s
                WHERE id = %s
            """, (turns, paquete_id, qr_id))
            db.commit()
        
        # Guardar en UserTurns (control detallado de turnos por usuario)
        cursor.execute("""
            INSERT INTO UserTurns (qr_code_id, turns_remaining, total_turns, package_id)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE 
                turns_remaining = turns_remaining + %s,
                total_turns = total_turns + %s,
                package_id = %s
        """, (qr_id, turns, turns, paquete_id, turns, turns, paquete_id))
        
        db.commit()
        
        return jsonify({
            'success': True,
            'message': f'Paquete P{paquete_id} asignado correctamente',
            'turns': turns,
            'price': price,
            'qr_id': qr_id
        })
        
    except Exception as e:
        print(f"❌ Error asignando paquete: {e}")
        return jsonify({'error': str(e)}), 500
    
# Verificar QR
@app.route('/api/verificar-qr/<qr_code>', methods=['GET'])
def verificar_qr(qr_code):
    try:
        cursor.execute("SELECT id FROM QRCode WHERE code = %s", (qr_code,))
        qr_data = cursor.fetchone()
        if not qr_data:
            return jsonify({'existe': False})
        
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
            return jsonify({'existe': False, 'qr_code': qr_code})
            
    except Exception as e:
        print(f"❌ Error verificando QR: {e}")
        return jsonify({'error': str(e)}), 500

# Registrar uso turno
@app.route('/api/registrar-uso', methods=['POST'])
def registrar_uso():
    try:
        data = request.get_json()
        qr_code = data.get('qr_code')
        machine_id = data.get('machine_id')
        
        if not qr_code or not machine_id:
            return jsonify({'error': 'Faltan datos requeridos'}), 400
        
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
        db.commit()
        
        return jsonify({
            'success': True,
            'message': 'Turno utilizado correctamente',
            'turns_remaining': turnos_data['turns_remaining'] - 1
        })
        
    except Exception as e:
        print(f"❌ Error registrando uso: {e}")
        return jsonify({'error': str(e)}), 500

# Reportar falla
@app.route('/api/reportar-falla', methods=['POST'])
def reportar_falla():
    try:
        data = request.get_json()
        qr_code = data.get('qr_code')
        machine_id = data.get('machine_id')
        machine_name = data.get('machine_name')
        turnos_devueltos = data.get('turnos_devueltos')
        
        if not all([qr_code, machine_id, turnos_devueltos]):
            return jsonify({'error': 'Faltan datos requeridos'}), 400
        
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
        db.commit()
        
        return jsonify({
            'success': True,
            'message': f'Falla reportada y {turnos_devueltos} turnos devueltos correctamente',
            'nuevos_turnos': turnos_data['turns_remaining'] + turnos_devueltos
        })
        
    except Exception as e:
        print(f"❌ Error reportando falla: {e}")
        return jsonify({'error': str(e)}), 500

# Historial fallas
@app.route('/api/historial-fallas', methods=['GET'])
def obtener_historial_fallas():
    try:
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
        return jsonify({'error': str(e)}), 500

# Guardar QR en historial
@app.route('/api/guardar-qr', methods=['POST'])
def guardar_qr():
    try:
        data = request.get_json()
        qr_code = data.get('qr_code')
        user_id = session.get('user_id')
        user_name = session.get('user_name', 'Usuario')
        local = session.get('user_local', 'El Mekatiadero')

        if not qr_code:
            return jsonify({'error': 'QR vacío'}), 400

        print(f"💾 Guardando QR en historial: {qr_code} por usuario {user_name}")

        # Asegúrate que los nombres de columnas coincidan exactamente
        cursor.execute("""
            INSERT INTO QRHistory (qr_code, user_id, user_name, local, fecha_hora)
            VALUES (%s, %s, %s, %s, NOW())
        """, (qr_code, user_id, user_name, local))
        db.commit()

        return jsonify({'success': True, 'message': 'QR guardado en historial'})
    except Exception as e:
        print(f"❌ Error guardando QR en historial: {e}")
        return jsonify({'error': str(e)}), 500

# Consultar historial de un QR
@app.route('/api/historial-qr/<qr_code>', methods=['GET'])
def historial_qr(qr_code):
    try:
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
        return jsonify({'error': str(e)}), 500

# Consultar historial general de QR (últimos 20)
@app.route('/api/historial-completo', methods=['GET'])
def historial_completo():
    try:
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
        return jsonify({'error': str(e)}), 500
    
    # Agregar QR generados en lote al historial
@app.route('/api/guardar-multiples-qr', methods=['POST'])
def guardar_multiples_qr():
    try:
        data = request.get_json()
        qr_codes = data.get('qr_codes', [])
        user_id = session.get('user_id')
        user_name = session.get('user_name', 'Usuario')
        local = session.get('user_local', 'El Mekatiadero')

        if not qr_codes:
            return jsonify({'error': 'Lista de QR vacía'}), 400

        print(f"💾 Guardando {len(qr_codes)} QR en el sistema")

        # Insertar todos los QR en AMBAS tablas
        for qr_code in qr_codes:
            # 1. Guardar en QRHistory (historial)
            cursor.execute("""
                INSERT INTO QRHistory (qr_code, user_id, user_name, local, fecha_hora)
                VALUES (%s, %s, %s, %s, NOW())
            """, (qr_code, user_id, user_name, local))

            # 2. Guardar en QRCode (para que funcionen los escáneres)
            cursor.execute("""
                INSERT INTO QRCode (code, remainingTurns, isActive, turnPackageId)
                VALUES (%s, %s, %s, %s)
            """, (qr_code, 0, 1, 1))

        db.commit()

        return jsonify({
            'success': True, 
            'message': f'{len(qr_codes)} QR guardados en el sistema',
            'count': len(qr_codes)
        })
        
    except Exception as e:
        print(f"❌ Error guardando múltiples QR: {e}")
        db.rollback()
        return jsonify({'error': str(e)}), 500

# Iniciar servidor
if __name__ == '__main__':
    print("🚀 Iniciando servidor Flask en http://127.0.0.1:5000")
    app.run(debug=True, port=5000, host='0.0.0.0')