# check_all_tables.py
import mysql.connector

def check_all_tables():
    try:
        conn = mysql.connector.connect(
            host="localhost",
            user="root",
            password="",
            database="maquinasmedellin",
            port=3306
        )
        
        cursor = conn.cursor(dictionary=True)
        
        print("📊 ESTADO COMPLETO DE LA BASE DE DATOS")
        print("=" * 60)
        
        # Obtener todas las tablas
        cursor.execute("SHOW TABLES")
        tables = cursor.fetchall()
        
        table_data = {}
        
        for table in tables:
            table_name = list(table.values())[0]
            
            # Contar registros en cada tabla
            cursor.execute(f"SELECT COUNT(*) as count FROM {table_name}")
            count_result = cursor.fetchone()
            record_count = count_result['count']
            
            # Obtener estructura
            cursor.execute(f"DESCRIBE {table_name}")
            structure = cursor.fetchall()
            
            table_data[table_name] = {
                'count': record_count,
                'structure': structure
            }
        
        # Mostrar resultados
        print(f"📁 Tablas encontradas: {len(tables)}")
        print("\n" + "=" * 60)
        
        empty_tables = []
        populated_tables = []
        
        for table_name, data in table_data.items():
            if data['count'] == 0:
                empty_tables.append(table_name)
                print(f"❌ {table_name}: {data['count']} registros - VACÍA")
            else:
                populated_tables.append(table_name)
                print(f"✅ {table_name}: {data['count']} registros")
        
        print("\n" + "=" * 60)
        print(f"📈 RESUMEN:")
        print(f"   ✅ Tablas con datos: {len(populated_tables)}")
        print(f"   ❌ Tablas vacías: {len(empty_tables)}")
        
        if empty_tables:
            print(f"\n📋 Tablas vacías que necesitan datos:")
            for table in empty_tables:
                print(f"   - {table}")
        
        cursor.close()
        conn.close()
        
        return table_data
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return None

if __name__ == "__main__":
    check_all_tables()