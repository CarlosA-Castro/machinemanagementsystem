# test_fixed.py
import mysql.connector
from mysql.connector import pooling

def test_fixed_connection():
    print("🔍 PROBANDO CONEXIÓN CON CONTRASEÑA VACÍA")
    print("=" * 50)
    
    # 1. Probar conexión básica
    try:
        conn = mysql.connector.connect(
            host="localhost",
            user="root",
            password="",  # VACÍA
            database="maquinasmedellin",
            port=3306
        )
        print("✅ Conexión básica EXITOSA")
        
        # Verificar base de datos
        cursor = conn.cursor()
        cursor.execute("SELECT DATABASE()")
        db_name = cursor.fetchone()[0]
        print(f"   Base de datos: {db_name}")
        
        # Verificar tablas
        cursor.execute("SHOW TABLES")
        tables = cursor.fetchall()
        print(f"   Tablas encontradas: {len(tables)}")
        
        cursor.close()
        conn.close()
        
    except Exception as e:
        print(f"❌ Error conexión básica: {e}")
        return False
    
    # 2. Probar pool de conexiones
    try:
        pool_config = {
            "host": "localhost",
            "user": "root", 
            "password": "",  # VACÍA
            "database": "maquinasmedellin",
            "port": 3306,
            "pool_name": "test_pool",
            "pool_size": 3
        }
        
        connection_pool = pooling.MySQLConnectionPool(**pool_config)
        print("✅ Pool de conexiones EXITOSO")
        
        # Probar conexiones del pool
        conn1 = connection_pool.get_connection()
        print("   Conexión 1 obtenida")
        
        conn2 = connection_pool.get_connection()
        print("   Conexión 2 obtenida")
        
        conn1.close()
        conn2.close()
        print("   Conexiones cerradas")
        
        return True
        
    except Exception as e:
        print(f"❌ Error con pool: {e}")
        return False

if __name__ == "__main__":
    success = test_fixed_connection()
    print("=" * 50)
    if success:
        print("🎉 ¡CONEXIÓN CORREGIDA! Ahora ejecuta: python app.py")
    else:
        print("❌ Aún hay problemas")