from hdbcli import dbapi
import os

host = os.environ["HANA_HOST"]
user = os.environ["HANA_USER"]
password = os.environ["HANA_PASSWORD"]

try:
    conn = dbapi.connect(address=host, port=443, user=user, password=password, encrypt=True)
    cursor = conn.cursor()
    cursor.execute('SELECT TOP 1 * FROM "SOC_LOGS"."V_DASHBOARD_ALERTS"')
    cols = [d[0] for d in cursor.description]
    print(f"COLUMNS: {cols}")
    conn.close()
except Exception as e:
    print(f"ERROR: {e}")
