# check_users.py
import mysql.connector

def check_users():
    try:
        conn = mysql.connector.connect(
            host="localhost",
            user="root",
            password="",
            database="maquinasmedellin",
            port=3306
        )
        
        cursor = conn.cursor(dictionary=True)
        
        print("👥 VERIFICANDO USUARIOS EN LA BASE DE DATOS")
        print("=" * 50)
        
        # 1. Verificar tabla Users
        cursor.execute("SHOW TABLES LIKE 'Users'")
        users_table_exists = cursor.fetchone()
        
        if not users_table_exists:
            print("❌ La tabla 'Users' NO existe")
            return False
        
        print("✅ Tabla 'Users' encontrada")
        
        # 2. Verificar estructura de la tabla
        cursor.execute("DESCRIBE Users")
        columns = cursor.fetchall()
        print("📋 Estructura de la tabla Users:")
        for col in columns:
            print(f"   - {col['Field']} ({col['Type']})")
        
        # 3. Verificar usuarios existentes
        cursor.execute("SELECT * FROM Users")
        users = cursor.fetchall()
        
        print(f"\n📊 Usuarios encontrados: {len(users)}")
        for user in users:
            print(f"\n👤 ID: {user['id']}")
            print(f"   Nombre: {user.get('name', 'N/A')}")
            print(f"   Password: {user.get('password', 'N/A')}") 
            print(f"   Role: {user.get('role', 'N/A')}")
            print(f"   Local: {user.get('local', 'N/A')}")
        
        cursor.close()
        conn.close()
        
        return len(users) > 0
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def test_login(password):
    try:
        conn = mysql.connector.connect(
            host="localhost",
            user="root",
            password="",
            database="maquinasmedellin",
            port=3306
        )
        
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM Users WHERE password = %s", (password,))
        user = cursor.fetchone()
        
        cursor.close()
        conn.close()
        
        if user:
            print(f"✅ Login EXITOSO con password: '{password}'")
            print(f"   Usuario: {user.get('name', 'N/A')}")
            print(f"   Role: {user.get('role', 'N/A')}")
            return True
        else:
            print(f"❌ Login FALLIDO con password: '{password}'")
            return False
            
    except Exception as e:
        print(f"❌ Error en login: {e}")
        return False

if __name__ == "__main__":
    print("🔍 DIAGNÓSTICO DE USUARIOS Y LOGIN")
    print("=" * 50)
    
    # Verificar usuarios
    has_users = check_users()
    
    if has_users:
        print("\n🧪 PROBANDO LOGINS COMUNES")
        print("-" * 30)
        
        # Probar passwords comunes
        common_passwords = [
            "123456", "1234", "123", "admin", "password", 
            "root", "user", "test", "0000", "1111"
        ]
        
        found = False
        for pwd in common_passwords:
            if test_login(pwd):
                found = True
                break
        
        if not found:
            print("\n💡 SUGERENCIAS:")
            print("1. Los passwords pueden estar encriptados")
            print("2. Puede que necesites crear usuarios nuevos")
            print("3. Revisa el backup SQL original")