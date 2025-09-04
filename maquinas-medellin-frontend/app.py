from flask import Flask, request, jsonify, render_template, redirect, url_for
import mysql.connector
from flask_cors import CORS
import os

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

# 📌 Ruta de la interfaz principal MEJORADA
@app.route('/local')
def mostrar_local():
    print("📍 Ruta /local accedida")
    
    # ✅ Obtener parámetros de URL para fallback
    nombre = request.args.get('nombre')
    local = request.args.get('local')
    
    if nombre and local:
        print(f"📦 Parámetros recibidos: {nombre}, {local}")
    
    return render_template('local.html')

@app.route('/packagefailure')
def mostrar_package_failure():
    print("📦 Ruta /packagefailure accedida - Reporte de Paquetes")
    return render_template('packagefailure.html')

# 📦 Ruta para la interfaz de ingresar paquete
@app.route('/package')
def mostrar_package():
    print("📦 Ruta /package accedida")
    return render_template('package.html')

# 📦 Ruta para la interfaz de reporte de máquina
@app.route('/machinereport')
def mostrar_machine_report():
    print("📦 Ruta /machinereport accedida")
    return render_template('machinereport.html')

# 👨‍💼 Ruta para el panel de administración
@app.route('/admin')
def mostrar_admin():
    print("👨‍💼 Ruta /admin accedida - Panel de administración")
    return render_template('admin.html')

# 🆕 Ruta para redireccionar Login.html a la raíz
@app.route('/Login.html')
def redirect_login():
    print("🔄 Redirigiendo Login.html a /")
    return redirect('/')

# 🐛 Endpoint para debug de localStorage
@app.route('/debug/localstorage')
def debug_localstorage():
    return jsonify({
        "nombreCajero": "Test User",
        "local": "Test Local",
        "rol": "Test Rol"
    })

# 🐛 Endpoint para verificar sesión
@app.route('/check-session')
def check_session():
    return jsonify({
        "session_working": True,
        "message": "Flask session is working"
    })

@app.route('/health')
def health_check():
    return jsonify({"status": "ok", "message": "Server is running"})

if __name__ == '__main__':
    print("🚀 Iniciando servidor Flask en http://127.0.0.1:5000")
    app.run(debug=True, port=5000, host='0.0.0.0')