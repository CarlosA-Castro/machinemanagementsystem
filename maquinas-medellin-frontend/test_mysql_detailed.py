# test_mysql_detailed.py
import mysql.connector
from mysql.connector import pooling
import sys

def test_mysql_connection_detailed():
    print("🔍 PRUEBA DETALLADA DE MYSQL")
    print("=" * 60)
    
    config = {
        "host": "localhost",
        "user": "root",
       "password": "" ,
        "database": "maquinasmedellin",
        "port": 3306
    }
    
    # 1. Probar conexión básica
    print("1. Probando conexión básica...")
    try:
        conn = mysql.connector.connect(**config)
        print("   ✅ Conexión básica exitosa")
        
        # Verificar base de datos
        cursor = conn.cursor()
        cursor.execute("SELECT DATABASE()")
        db_name = cursor.fetchone()[0]
        print(f"   ✅ Base de datos conectada: {db_name}")
        
        # Verificar tablas
        cursor.execute("SHOW TABLES")
        tables = cursor.fetchall()
        print(f"   ✅ Tablas encontradas: {len(tables)}")
        
        cursor.close()
        conn.close()
        
    except mysql.connector.Error as e:
        print(f"   ❌ Error en conexión básica: {e}")
        return False
    
    # 2. Probar pool de conexiones
    print("\n2. Probando pool de conexiones...")
    try:
        pool_config = config.copy()
        pool_config.update({
            "pool_name": "test_pool",
            "pool_size": 3
        })
        
        connection_pool = pooling.MySQLConnectionPool(**pool_config)
        print("   ✅ Pool creado exitosamente")
        
        # Probar obtener conexión del pool
        conn1 = connection_pool.get_connection()
        print("   ✅ Conexión 1 obtenida del pool")
        
        conn2 = connection_pool.get_connection() 
        print("   ✅ Conexión 2 obtenida del pool")
        
        conn1.close()
        conn2.close()
        print("   ✅ Conexiones cerradas correctamente")
        
        return True
        
    except Exception as e:
        print(f"   ❌ Error con pool: {e}")
        import traceback
        traceback.print_exc()
        return False

def check_mysql_version():
    print("\n3. Verificando versión de MySQL...")
    try:
        conn = mysql.connector.connect(
            host="localhost",
            user="root",
            password="Dattebayo",
            port=3306
        )
        cursor = conn.cursor()
        cursor.execute("SELECT VERSION()")
        version = cursor.fetchone()[0]
        print(f"   ✅ Versión MySQL: {version}")
        cursor.close()
        conn.close()
        return version
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return None

def check_connector_version():
    print("\n4. Verificando versión del conector...")
    try:
        print(f"   ✅ mysql-connector-python: {mysql.connector.__version__}")
        return mysql.connector.__version__
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return None

if __name__ == "__main__":
    print("🧪 DIAGNÓSTICO AVANZADO MYSQL")
    print("=" * 60)
    
    check_connector_version()
    check_mysql_version()
    success = test_mysql_connection_detailed()
    
    print("=" * 60)
    if success:
        print("🎉 TODAS LAS PRUEBAS EXITOSAS")
    else:
        print("❌ Hay problemas con el pool de conexiones")
        print("\n💡 SOLUCIÓN RÁPIDA: Usar conexiones directas")
        print("   En app.py, cambia 'connection_pool = None' permanentemente")