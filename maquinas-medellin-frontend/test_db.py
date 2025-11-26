import mysql.connector
from mysql.connector import Error

def test_connection():
    try:
        print("🔍 Probando conexión a MySQL...")
        
        connection = mysql.connector.connect(
            host="localhost",
            user="root",
            password="",
            database="maquinasmedellin",
            port=3306
        )
        
        if connection.is_connected():
            print("✅ Conexión exitosa a MySQL")
            
            # Probar consulta simple
            cursor = connection.cursor()
            cursor.execute("SELECT DATABASE()")
            db_name = cursor.fetchone()
            print(f"✅ Conectado a la base de datos: {db_name[0]}")
            
            # Verificar tablas
            cursor.execute("SHOW TABLES")
            tables = cursor.fetchall()
            print(f"✅ Tablas encontradas: {len(tables)}")
            for table in tables:
                print(f"   - {table[0]}")
            
            cursor.close()
            connection.close()
            
    except Error as e:
        print(f"❌ Error de MySQL: {e}")
    except Exception as e:
        print(f"❌ Error general: {e}")

if __name__ == "__main__":
    test_connection()