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

# 🔌 Conexión a la base de datos
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

# 🏠 Rutas
@app.route('/')
def mostrar_login():
    # Limpiar sesión al cargar login
    session.clear()
    return render_template('login.html')

# 🔐 Procesa login
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
            # GUARDAR EN SESIÓN FLASK
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
        return jsonify({
            "valido": False,
            "error": "server_error",
            "message": str(e)
        }), 500

# 📌 Ruta de la interfaz principal
@app.route('/local')
def mostrar_local():
    print("📍 Ruta /local accedida")
    
    # Verificar si el usuario está logueado
    if not session.get('logged_in'):
        print("⚠️ Usuario no autenticado, redirigiendo a login")
        return redirect(url_for('mostrar_login'))
    
    # Obtener datos de la sesión
    nombre_usuario = session.get('user_name', 'Usuario')
    local_usuario = session.get('user_local', 'El Mekatiadero')
    
    print(f"👤 Usuario en sesión: {nombre_usuario}, Local: {local_usuario}")
    
    return render_template('local.html', 
                         nombre_usuario=nombre_usuario,
                         local_usuario=local_usuario)

# 📦 Ruta para la interfaz de ingresar paquete
@app.route('/package')
def mostrar_package():
    print("📦 Ruta /package accedida")
    
    # Verificar autenticación
    if not session.get('logged_in'):
        return redirect(url_for('mostrar_login'))
    
    nombre_usuario = session.get('user_name', 'Usuario')
    local_usuario = session.get('user_local', 'El Mekatiadero')
    
    return render_template('package.html',
                         nombre_usuario=nombre_usuario,
                         local_usuario=local_usuario)

# ⚠️ Ruta para falla de paquete
@app.route('/package/failure')
def mostrar_package_failure():
    print("📦 Ruta /package/failure accedida - Reporte de Paquetes")
    
    # Verificar autenticación
    if not session.get('logged_in'):
        return redirect(url_for('mostrar_login'))
    
    nombre_usuario = session.get('user_name', 'Usuario')
    local_usuario = session.get('user_local', 'El Mekatiadero')
    
    return render_template('packfailure.html',
                         nombre_usuario=nombre_usuario,
                         local_usuario=local_usuario)

# ⚠️ Ruta para reporte de máquina
@app.route('/machinereport')
def mostrar_machine_report():
    print("📦 Ruta /machinereport accedida")
    
    # Verificar autenticación
    if not session.get('logged_in'):
        return redirect(url_for('mostrar_login'))
    
    nombre_usuario = session.get('user_name', 'Usuario')
    local_usuario = session.get('user_local', 'El Mekatiadero')
    
    return render_template('machinereport.html',
                         nombre_usuario=nombre_usuario,
                         local_usuario=local_usuario)

# 👨‍💼 Ruta para el panel de administración
@app.route('/admin')
def mostrar_admin():
    print("👨‍💼 Ruta /admin accedida - Panel de administración")
    
    # Verificar autenticación y rol
    if not session.get('logged_in'):
        return redirect(url_for('mostrar_login'))
    
    if session.get('user_role') != 'admin':
        return redirect(url_for('mostrar_local'))
    
    nombre_usuario = session.get('user_name', 'Administrador')
    local_usuario = session.get('user_local', 'Sistema')
    
    return render_template('admin.html',
                         nombre_usuario=nombre_usuario,
                         local_usuario=local_usuario)

# 🚪 Ruta para logout
@app.route('/logout')
def logout():
    session.clear()
    print("🚪 Usuario cerró sesión")
    return redirect(url_for('mostrar_login'))

# 🆕 Ruta para redireccionar Login.html a la raíz
@app.route('/Login.html')
def redirect_login():
    print("🔄 Redirigiendo Login.html a /")
    return redirect('/')

# 🐛 Endpoint para debug de sesión
@app.route('/debug/session')
def debug_session():
    return jsonify(dict(session))

# 🐛 Endpoint para verificar sesión
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

# 🆕 NUEVAS RUTAS PARA PAQUETES - AJUSTADAS A TU ESTRUCTURA

# Endpoint para obtener información de los paquetes
@app.route('/api/paquetes', methods=['GET'])
def obtener_paquetes():
    try:
        cursor.execute("SELECT * FROM TurnPackage ORDER BY id")
        paquetes = cursor.fetchall()
        
        return jsonify(paquetes)
    except Exception as e:
        print(f"❌ Error obteniendo paquetes: {e}")
        return jsonify({'error': str(e)}), 500

# Endpoint para asignar un paquete a un código QR
@app.route('/api/asignar-paquete', methods=['POST'])
def asignar_paquete():
    try:
        data = request.get_json()
        codigo_qr = data.get('codigo_qr')
        paquete_id = data.get('paquete_id')
        
        if not codigo_qr or not paquete_id:
            return jsonify({'error': 'Faltan datos requeridos'}), 400
        
        # Primero verificamos si el código QR existe
        cursor.execute("SELECT id FROM QRCode WHERE code = %s", (codigo_qr,))
        qr_existente = cursor.fetchone()
        
        qr_id = None
        if qr_existente:
            qr_id = qr_existente['id']
        else:
            # Si no existe, creamos el código QR
            cursor.execute("INSERT INTO QRCode (code) VALUES (%s)", (codigo_qr,))
            db.commit()
            qr_id = cursor.lastrowid
        
        # Obtenemos la información del paquete
        cursor.execute("SELECT turns, price FROM TurnPackage WHERE id = %s", (paquete_id,))
        paquete = cursor.fetchone()
        
        if not paquete:
            return jsonify({'error': 'Paquete no encontrado'}), 404
        
        turns, price = paquete['turns'], paquete['price']
        
        # Insertar o actualizar en UserTurns
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

# Endpoint para verificar estado de un código QR
@app.route('/api/verificar-qr/<qr_code>', methods=['GET'])
def verificar_qr(qr_code):
    try:
        # Buscar el código QR
        cursor.execute("SELECT id FROM QRCode WHERE code = %s", (qr_code,))
        qr_data = cursor.fetchone()
        
        if not qr_data:
            return jsonify({'existe': False})
        
        qr_id = qr_data['id']
        
        # Verificar si tiene turnos asignados
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

# Endpoint para registrar uso de turno en máquina
@app.route('/api/registrar-uso', methods=['POST'])
def registrar_uso():
    try:
        data = request.get_json()
        qr_code = data.get('qr_code')
        machine_id = data.get('machine_id')
        
        if not qr_code or not machine_id:
            return jsonify({'error': 'Faltan datos requeridos'}), 400
        
        # Verificar código QR y obtener ID
        cursor.execute("SELECT id FROM QRCode WHERE code = %s", (qr_code,))
        qr_data = cursor.fetchone()
        
        if not qr_data:
            return jsonify({'error': 'Código QR no encontrado'}), 404
        
        qr_id = qr_data['id']
        
        # Verificar si tiene turnos disponibles
        cursor.execute("SELECT turns_remaining FROM UserTurns WHERE qr_code_id = %s", (qr_id,))
        turnos_data = cursor.fetchone()
        
        if not turnos_data or turnos_data['turns_remaining'] <= 0:
            return jsonify({'error': 'No hay turnos disponibles'}), 400
        
        # Registrar uso en TurnUsage
        cursor.execute("""
            INSERT INTO TurnUsage (qrCodeId, machineId) 
            VALUES (%s, %s)
        """, (qr_id, machine_id))
        
        # Reducir turnos disponibles
        cursor.execute("""
            UPDATE UserTurns 
            SET turns_remaining = turns_remaining - 1 
            WHERE qr_code_id = %s
        """, (qr_id,))
        
        db.commit()
        
        return jsonify({
            'success': True,
            'message': 'Turno utilizado correctamente',
            'turns_remaining': turnos_data['turns_remaining'] - 1
        })
        
    except Exception as e:
        print(f"❌ Error registrando uso: {e}")
        return jsonify({'error': str(e)}), 500

# 🆕 Endpoint para reportar falla y devolver turnos
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
        
        # Verificar código QR y obtener ID
        cursor.execute("SELECT id FROM QRCode WHERE code = %s", (qr_code,))
        qr_data = cursor.fetchone()
        
        if not qr_data:
            return jsonify({'error': 'Código QR no encontrado'}), 404
        
        qr_id = qr_data['id']
        
        # Verificar si tiene turnos asignados
        cursor.execute("SELECT turns_remaining FROM UserTurns WHERE qr_code_id = %s", (qr_id,))
        turnos_data = cursor.fetchone()
        
        if not turnos_data:
            return jsonify({'error': 'No hay turnos asignados a este QR'}), 400
        
        # Registrar la falla en la base de datos
        cursor.execute("""
            INSERT INTO MachineFailures (qr_code_id, machine_id, machine_name, turnos_devueltos)
            VALUES (%s, %s, %s, %s)
        """, (qr_id, machine_id, machine_name, turnos_devueltos))
        
        # Devolver los turnos al usuario
        cursor.execute("""
            UPDATE UserTurns 
            SET turns_remaining = turns_remaining + %s 
            WHERE qr_code_id = %s
        """, (turnos_devueltos, qr_id))
        
        db.commit()
        
        return jsonify({
            'success': True,
            'message': f'Falla reportada y {turnos_devueltos} turnos devueltos correctamente',
            'nuevos_turnos': turnos_data['turns_remaining'] + turnos_devueltos
        })
        
    except Exception as e:
        print(f"❌ Error reportando falla: {e}")
        return jsonify({'error': str(e)}), 500

# 🆕 Endpoint para obtener historial de fallas
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
        fallas = cursor.fetchall()
        
        return jsonify(fallas)
    except Exception as e:
        print(f"❌ Error obteniendo historial de fallas: {e}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    print("🚀 Iniciando servidor Flask en http://127.0.0.1:5000")
    app.run(debug=True, port=5000, host='0.0.0.0')