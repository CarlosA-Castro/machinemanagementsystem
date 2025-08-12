from flask import Flask, request, jsonify
import mysql.connector
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

db = mysql.connector.connect(
    host="localhost",
    user="root",
    password="Dattebayo",
    database="maquinasmedellin"
)
cursor = db.cursor(dictionary=True)

@app.route('/login', methods=['POST'])
def login():
    data = request.get_json() or {}
    codigo = data.get('codigo')

    if not codigo:
        return jsonify({"valido": False, "error": "no_codigo"}), 400

    try:
        usuario = None

        # Intento 1: buscar por id (si el codigo es un número)
        try:
            cursor.execute("SELECT * FROM Users WHERE id = %s", (int(codigo),))
            usuario = cursor.fetchone()
        except Exception:
            usuario = None

        # Intento 2: buscar por password (por ahora, para pruebas)
        if not usuario:
            cursor.execute("SELECT * FROM Users WHERE password = %s", (codigo,))
            usuario = cursor.fetchone()

        if usuario:
            return jsonify({
                "valido": True,
                "nombre": usuario.get("name"),
                "role": usuario.get("role")
            }), 200
        else:
            # Credenciales inválidas -> 200 con valido: false (más fácil de manejar en frontend)
            return jsonify({"valido": False}), 200

    except Exception as e:
        app.logger.exception("Error en /login")
        return jsonify({"valido": False, "error": "server_error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)

