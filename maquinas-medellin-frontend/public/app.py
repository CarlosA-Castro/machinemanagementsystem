from flask import Flask, request, jsonify, send_from_directory
import mysql.connector
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# Conexión a la base de datos
db = mysql.connector.connect(
    host="localhost",
    user="root",
    password="Dattebayo",
    database="maquinasmedellin"
)
cursor = db.cursor(dictionary=True)

# Ruta principal: muestra el login.html
@app.route('/')
def mostrar_login():
    ruta_base = os.path.dirname(os.path.abspath(__file__))
    return send_from_directory(ruta_base, 'Login.html')

import os

@app.route('/verificar')
def verificar():
    archivos = os.listdir(os.path.dirname(os.path.abspath(__file__)))
    return jsonify(archivos)

# Ruta para procesar el login (solo por contraseña)
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

if __name__ == '__main__':
    app.run(debug=True)
