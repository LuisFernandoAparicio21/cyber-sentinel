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
cur.execute('SELECT TOP 5 "REGION_ID", COUNT(*) FROM "SOC_LOGS"."SOC_EVENTS" GROUP BY "REGION_ID" ORDER BY COUNT(*) DESC')
print('REGIONS:')
for row in cur.fetchall(): print(repr(row[0]), row[1])
cur.execute('SELECT TOP 5 "APPLICATION_ID", COUNT(*) FROM "SOC_LOGS"."SOC_EVENTS" GROUP BY "APPLICATION_ID" ORDER BY COUNT(*) DESC')
print('APPS:')
for row in cur.fetchall(): print(repr(row[0]), row[1])
conn.close()
