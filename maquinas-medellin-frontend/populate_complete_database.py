# update_database_structure.py
import mysql.connector

def update_database_structure():
    try:
        conn = mysql.connector.connect(
            host="localhost",
            user="root",
            password="",
            database="maquinasmedellin",
            port=3306
        )
        
        cursor = conn.cursor(dictionary=True)
        
        print("🔄 ACTUALIZANDO ESTRUCTURA DE LA BASE DE DATOS")
        print("=" * 60)
        
        # 1. Agregar columna 'local' a Users si no existe
        print("1. Actualizando tabla Users...")
        try:
            cursor.execute("ALTER TABLE Users ADD COLUMN local VARCHAR(100) DEFAULT 'El Mekatiadero'")
            print("   ✅ Columna 'local' agregada a Users")
        except mysql.connector.Error as e:
            if "Duplicate column name" in str(e):
                print("   ✅ Columna 'local' ya existe")
            else:
                print(f"   ⚠️  Error: {e}")
        
        # 2. Crear tabla QRHistory si no existe
        print("\n2. Creando tabla QRHistory...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS QRHistory (
                id INT AUTO_INCREMENT PRIMARY KEY,
                qr_code VARCHAR(255) NOT NULL,
                user_id INT NULL,
                user_name VARCHAR(100) NULL,
                local VARCHAR(100) NOT NULL DEFAULT 'El Mekatiadero',
                fecha_hora DATETIME DEFAULT CURRENT_TIMESTAMP,
                qr_name VARCHAR(255) NULL,
                INDEX idx_qr_code (qr_code),
                INDEX idx_fecha_hora (fecha_hora),
                INDEX idx_user_id (user_id)
            )
        """)
        print("   ✅ Tabla QRHistory creada/verificada")
        
        # 3. Actualizar tabla QRCode para que coincida con la aplicación
        print("\n3. Actualizando tabla QRCode...")
        try:
            # Cambiar isUsed por isActive si existe
            cursor.execute("ALTER TABLE QRCode CHANGE COLUMN isUsed isActive BOOLEAN DEFAULT TRUE")
            print("   ✅ Columna isUsed cambiada a isActive")
        except:
            print("   ✅ Estructura QRCode correcta")
        
        # 4. Crear tabla UserTurns si no existe
        print("\n4. Creando tabla UserTurns...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS UserTurns (
                id INT AUTO_INCREMENT PRIMARY KEY,
                qr_code_id INT NOT NULL,
                turns_remaining INT NOT NULL DEFAULT 0,
                total_turns INT NOT NULL DEFAULT 0,
                package_id INT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                FOREIGN KEY (qr_code_id) REFERENCES QRCode(id),
                FOREIGN KEY (package_id) REFERENCES TurnPackage(id),
                INDEX idx_qr_code_id (qr_code_id)
            )
        """)
        print("   ✅ Tabla UserTurns creada/verificada")
        
        # 5. Actualizar tabla Machine (locationId -> location_id)
        print("\n5. Actualizando tabla Machine...")
        try:
            # Verificar si existe locationId y no location_id
            cursor.execute("SHOW COLUMNS FROM Machine LIKE 'locationId'")
            if cursor.fetchone():
                cursor.execute("ALTER TABLE Machine CHANGE COLUMN locationId location_id INT")
                print("   ✅ locationId cambiado a location_id")
            else:
                print("   ✅ Columna location_id ya existe")
        except Exception as e:
            print(f"   ⚠️  Error: {e}")
        
        # 6. Crear tabla MachineFailures si no existe
        print("\n6. Creando tabla MachineFailures...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS MachineFailures (
                id INT AUTO_INCREMENT PRIMARY KEY,
                qr_code_id INT NOT NULL,
                machine_id INT NOT NULL,
                machine_name VARCHAR(100) NOT NULL,
                turnos_devueltos INT NOT NULL,
                reported_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (qr_code_id) REFERENCES QRCode(id),
                FOREIGN KEY (machine_id) REFERENCES Machine(id),
                INDEX idx_reported_at (reported_at)
            )
        """)
        print("   ✅ Tabla MachineFailures creada")
        
        # 7. Agregar datos de prueba esenciales
        print("\n7. Insertando datos esenciales...")
        
        # Verificar si Users tiene datos
        cursor.execute("SELECT COUNT(*) as count FROM Users")
        user_count = cursor.fetchone()['count']
        
        if user_count == 0:
            print("   Insertando usuarios...")
            users = [
                (1, 'Admin Principal', 'admin123', 'admin', 'Sistema'),
                (2, 'Cajero Principal', '123456', 'cajero', 'El Mekatiadero'),
                (3, 'Cajero Secundario', '1234', 'cajero', 'El Mekatiadero'),
                (4, 'Gerente', 'gerente123', 'admin_restaurante', 'El Mekatiadero')
            ]
            
            for user in users:
                cursor.execute("""
                    INSERT INTO Users (id, name, password, role, local) 
                    VALUES (%s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE 
                    name = VALUES(name), 
                    password = VALUES(password), 
                    role = VALUES(role), 
                    local = VALUES(local)
                """, user)
            print("   ✅ Usuarios de prueba insertados")
        else:
            print(f"   ✅ Ya existen {user_count} usuarios")
        
        # Verificar paquetes
        cursor.execute("SELECT COUNT(*) as count FROM TurnPackage")
        package_count = cursor.fetchone()['count']
        
        if package_count == 0:
            print("   Insertando paquetes...")
            packages = [
                (1, 'Paquete P1', 4, 10000, 1),
                (2, 'Paquete P2', 6, 13000, 1),
                (3, 'Paquete P3', 8, 15000, 1),
                (4, 'Paquete P4', 10, 18000, 1),
                (5, 'Paquete P5', 12, 20000, 1),
                (6, 'Paquete P6', 14, 22000, 1),
                (7, 'Paquete P7', 16, 24000, 1),
                (8, 'Paquete P8', 18, 26000, 1),
                (9, 'Paquete P9', 20, 28000, 1),
                (10, 'Paquete P10', 22, 30000, 1)
            ]
            
            for pkg in packages:
                cursor.execute("""
                    INSERT INTO TurnPackage (id, name, turns, price, isActive) 
                    VALUES (%s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE 
                    name = VALUES(name), 
                    turns = VALUES(turns), 
                    price = VALUES(price), 
                    isActive = VALUES(isActive)
                """, pkg)
            print("   ✅ Paquetes insertados")
        else:
            print(f"   ✅ Ya existen {package_count} paquetes")
        
        conn.commit()
        
        # VERIFICAR ESTRUCTURA FINAL
        print("\n" + "=" * 60)
        print("🔍 VERIFICANDO ESTRUCTURA FINAL...")
        
        essential_tables = ['Users', 'TurnPackage', 'QRCode', 'QRHistory', 'UserTurns', 'Machine']
        
        for table in essential_tables:
            cursor.execute(f"SHOW TABLES LIKE '{table}'")
            exists = cursor.fetchone()
            if exists:
                cursor.execute(f"SELECT COUNT(*) as count FROM {table}")
                count = cursor.fetchone()['count']
                print(f"   ✅ {table}: {count} registros")
            else:
                print(f"   ❌ {table}: NO EXISTE")
        
        cursor.close()
        conn.close()
        
        print("\n🎉 ESTRUCTURA ACTUALIZADA EXITOSAMENTE!")
        print("\n🔑 CREDENCIALES DE ACCESO:")
        print("   - admin123 (Administrador)")
        print("   - 123456 (Cajero Principal)") 
        print("   - 1234 (Cajero Secundario)")
        print("   - gerente123 (Gerente)")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    update_database_structure()