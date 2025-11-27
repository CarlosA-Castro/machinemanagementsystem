# test_connection.py
import mysql.connector
import requests

def test_mysql_connection():
    try:
        conn = mysql.connector.connect(
            host="localhost",
            user="root",
            password="Dattebayo",
            database="maquinasmedellin",
            port=3306
        )
        print("✅ Conexión MySQL exitosa")
        conn.close()
        return True
    except Exception as e:
        print(f"❌ Error MySQL: {e}")
        return False

def test_flask_endpoints():
    base_url = "http://localhost:5000"
    endpoints = ["/health", "/test-db", "/api/paquetes"]
    
    for endpoint in endpoints:
        try:
            response = requests.get(f"{base_url}{endpoint}", timeout=5)
            print(f"✅ {endpoint}: {response.status_code}")
        except Exception as e:
            print(f"❌ {endpoint}: {e}")

if __name__ == "__main__":
    print("🧪 Probando conexiones...")
    test_mysql_connection()
    test_flask_endpoints()