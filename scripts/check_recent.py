import sys, os, re
sys.stdout.reconfigure(encoding='utf-8')
import hdbcli.dbapi as dbapi

env = open(r'c:\Users\HP\Documents\SAP proyecto\04_Security_Visualisation_Gerardo\soc-dashboard\.env.local').read()
host = re.search(r'HANA_HOST=(.+)', env).group(1).strip()
port = int(re.search(r'HANA_PORT=(.+)', env).group(1).strip())
user = re.search(r'HANA_USER=(.+)', env).group(1).strip()
pwd  = re.search(r'HANA_PASSWORD=(.+)', env).group(1).strip()

conn = dbapi.connect(address=host, port=port, user=user, password=pwd, encrypt=True)
cur = conn.cursor()
try:
    cur.execute('SELECT TOP 10 "EVENT_TIMESTAMP", "APPLICATION_ID", "REGION_ID" FROM "SOC_LOGS"."SOC_EVENTS" ORDER BY "EVENT_TIMESTAMP" DESC')
    print('ULTIMOS 10 EVENTOS INGRESADOS:')
    for row in cur.fetchall(): 
        print(f"Time: {row[0]}, App: {row[1]}, Region: {row[2]}")
except Exception as e:
    print(f"Error querying events: {e}")
conn.close()
