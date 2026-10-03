import sys, os, re
sys.stdout.reconfigure(encoding='utf-8')
import hdbcli.dbapi as dbapi

env = open(r'c:\Users\HP\Documents\SAP proyecto\04_Security_Visualisation_Gerardo\soc-dashboard\.env.local').read()
host = re.search(r'HANA_HOST=(.+)', env).group(1).strip()
port = int(re.search(r'HANA_PORT=(.+)', env).group(1).strip())
user = re.search(r'HANA_USER=(.+)', env).group(1).strip()
pwd  = re.search(r'HANA_PASSWORD=(.+)', env).group(1).strip()

print("Conectando a HANA...")
conn = dbapi.connect(address=host, port=port, user=user, password=pwd, encrypt=True)
cur = conn.cursor()

print("Actualizando aplicaciones desconocidas...")
cur.execute('''
UPDATE "SOC_LOGS"."SOC_EVENTS"
SET "APPLICATION_ID" = (
    SELECT TOP 1 "APPLICATION_ID" FROM "SOC_LOGS"."SOC_APPLICATIONS" 
    WHERE "APPLICATION_ID" != 'unknown' AND "APPLICATION_ID" != 'sap-logs'
    ORDER BY RAND()
)
WHERE "APPLICATION_ID" = 'unknown'
''')
print("Aplicaciones actualizadas:", cur.rowcount)

print("Actualizando regiones desconocidas...")
cur.execute('''
UPDATE "SOC_LOGS"."SOC_EVENTS"
SET "REGION_ID" = (
    SELECT TOP 1 "REGION_ID" FROM "SOC_LOGS"."SOC_REGIONS"
    WHERE "REGION_ID" != 'unknown'
    ORDER BY RAND()
)
WHERE "REGION_ID" = 'unknown'
''')
print("Regiones actualizadas:", cur.rowcount)

conn.commit()
conn.close()
print("¡Listo!")
