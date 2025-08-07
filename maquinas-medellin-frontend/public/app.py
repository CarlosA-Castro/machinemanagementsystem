from flask import Flask, jsonify, request
import mysql.connector

app = Flask(__name__)

# Conexión a tu base de datos
db = mysql.connector.connect(
    host="localhost",
    user="root",
    password="Dattebayo",  # Reemplaza esto
    database="maquinasmedellin"  # Reemplaza esto también
)
cursor = db.cursor(dictionary=True)

# Ruta para probar que el servidor funcione
@app.route("/")
def home():
    return "Servidor funcionando."

# Ruta para verificar código QR
@app.route("/verificar_qr", methods=["POST"])
def verificar_qr():
    data = request.json
    codigo_qr = data.get("codigo")

    cursor.execute("SELECT * FROM qr WHERE codigo = %s", (codigo_qr,))
    resultado = cursor.fetchone()

    if resultado:
        return jsonify({"valido": True, "datos": resultado})
    else:
        return jsonify({"valido": False}), 404

# Ejecutar el servidor
if __name__ == "__main__":
    app.run(debug=True)
