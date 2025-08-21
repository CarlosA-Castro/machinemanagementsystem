from flask import Flask, request, jsonify, render_template
import mysql.connector
from flask_cors import CORS
import os

# 📁 Configuración de Flask para servir archivos estáticos y plantillas
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(
    __name__,
    static_folder=os.path.join(BASE_DIR, 'public', 'static'),
    template_folder=os.path.join(BASE_DIR, 'public', 'templates')
)
CORS(app)

# 🔌 Conexión a la base de datos
db = mysql.connector.connect(
    host="localhost",
    user="root",
    password="Dattebayo",
    database="maquinasmedellin"
)
cursor = db.cursor(dictionary=True)

# 🏠 Ruta principal: muestra el login
@app.route('/')
def mostrar_login():
    return render_template('login.html')

# 🔍 Ruta de verificación (opcional para listar archivos en static)
@app.route('/verificar')
def verificar():
    carpeta_static = os.path.join(BASE_DIR, 'static')
    archivos = os.listdir(carpeta_static)
    return jsonify(archivos)

# 🔐 Ruta para procesar el login
@app.route('/login', methods=['POST'])
def procesar_login():
    data = request.get_json() or {}
    codigo = data.get('codigo')

    if not codigo:
        return jsonify({"valido": False, "error": "no_codigo"}), 400

    try:
        cursor.execute("SELECT * FROM Users WHERE password = %s", (codigo,))
        usuario = cursor.fetchone()

        if usuario:
            return jsonify({
                "valido": True,
                "nombre": usuario.get("name"),
                "role": usuario.get("role")
            }), 200
        else:
            return jsonify({"valido": False}), 200

    except Exception as e:
        app.logger.exception("Error en /login")
        return jsonify({
            "valido": False,
            "error": "server_error",
            "message": str(e)
        }), 500

# 🚀 Ejecutar el servidor
if __name__ == '__main__':
    app.run(debug=True)
