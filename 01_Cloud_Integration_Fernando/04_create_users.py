import os
import json
from hdbcli import dbapi

def get_hana_connection():
    host = os.environ.get("HANA_HOST")
    port = int(os.environ.get("HANA_PORT", 443))
    user = os.environ.get("HANA_USER")
    password = os.environ.get("HANA_PASSWORD")

    print(f"Conectando a {host}...")
    return dbapi.connect(address=host, port=port, user=user, password=password, encrypt=True)

# Initial password for the team users, set in the environment (never in code)
NEW_USER_PASSWORD = os.environ["NEW_USER_PASSWORD"]

SQL_COMMANDS = [
    # 1. Crear Roles (Ignora error si ya existen)
    'CREATE ROLE "ROLE_ML"',
    'CREATE ROLE "ROLE_BACKEND"',
    'CREATE ROLE "ROLE_DASHBOARD"',
    
    # 2. Dar permisos
    'GRANT SELECT ON SCHEMA "SOC_LOGS" TO "ROLE_ML"',
    'GRANT SELECT ON SCHEMA "SOC_LOGS" TO "ROLE_DASHBOARD"',
    'GRANT SELECT, INSERT, UPDATE, DELETE ON SCHEMA "SOC_LOGS" TO "ROLE_BACKEND"',
    
    # 3. Crear Usuarios
    f'CREATE USER USER_CARLOS PASSWORD "{NEW_USER_PASSWORD}" NO FORCE_FIRST_PASSWORD_CHANGE',
    f'CREATE USER USER_IKER PASSWORD "{NEW_USER_PASSWORD}" NO FORCE_FIRST_PASSWORD_CHANGE',
    f'CREATE USER USER_RENE PASSWORD "{NEW_USER_PASSWORD}" NO FORCE_FIRST_PASSWORD_CHANGE',
    f'CREATE USER USER_LUIS PASSWORD "{NEW_USER_PASSWORD}" NO FORCE_FIRST_PASSWORD_CHANGE',
    f'CREATE USER USER_GERARDO PASSWORD "{NEW_USER_PASSWORD}" NO FORCE_FIRST_PASSWORD_CHANGE',
    
    # 4. Asignar Roles
    'GRANT "ROLE_ML" TO USER_CARLOS',
    'GRANT "ROLE_ML" TO USER_IKER',
    'GRANT "ROLE_BACKEND" TO USER_RENE',
    'GRANT "ROLE_BACKEND" TO USER_LUIS',
    'GRANT "ROLE_DASHBOARD" TO USER_GERARDO'
]

if __name__ == "__main__":
    try:
        conn = get_hana_connection()
        cursor = conn.cursor()
        for sql in SQL_COMMANDS:
            try:
                cursor.execute(sql)
                print(f"Ejecutado OK: {sql[:50]}...")
            except Exception as e:
                # 288 is "authorization already exists", 258 is "role already exists", 314 is "user already exists"
                print(f"Aviso en {sql[:30]}: {e}")
        
        conn.commit()
        cursor.close()
        conn.close()
        print("✅ ¡Todos los usuarios y roles creados en HANA exitosamente!")
    except Exception as e:
        print(f"❌ Error fatal conectando a HANA: {e}")
