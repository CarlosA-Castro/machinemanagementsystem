
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime, timedelta

# Tu conexión a BD existente (reutiliza tu función)
def get_db_connection():
    import mysql.connector
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "mysql"),
        user=os.getenv("DB_USER", "myuser"),
        password=os.getenv("DB_PASSWORD", "mypassword"),
        database=os.getenv("DB_NAME", "maquinasmedellin"),
        port=3306,
        auth_plugin="mysql_native_password"
    )

def limpiar_logs_antiguos(dias=30):
    """Limpiar logs más antiguos que X días"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        fecha_limite = (datetime.now() - timedelta(days=dias)).strftime('%Y-%m-%d')
        
        print(f"Limpiando logs anteriores a {fecha_limite}")
        
        tablas = ['app_logs', 'access_logs', 'error_logs', 'sessionlog']
        total = 0
        
        for tabla in tablas:
            cursor.execute(f"DELETE FROM {tabla} WHERE DATE(created_at) < %s", (fecha_limite,))
            eliminados = cursor.rowcount
            total += eliminados
            print(f"  {tabla}: {eliminados} registros eliminados")
        
        conn.commit()
        cursor.close()
        conn.close()
        
        print(f"\nTotal eliminados: {total}")
        
        # También limpiar archivo de log si es muy grande
        log_file = 'logs/maquinas.log'
        if os.path.exists(log_file) and os.path.getsize(log_file) > 50 * 1024 * 1024:  # 50MB
            with open(log_file, 'w') as f:
                f.write(f"# Log file trimmed at {datetime.now()}\n")
            print("Archivo de log limpiado (más de 50MB)")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == '__main__':
    if len(sys.argv) > 1:
        dias = int(sys.argv[1])
    else:
        dias = 30  # Por defecto, 30 días
    
    limpiar_logs_antiguos(dias)