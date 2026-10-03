"""
Borra filas de prueba inyectadas con INYECCION_PRUEBA.py (source SIMULATOR_V2).
Usa las mismas credenciales que soc-dashboard/.env.local
"""
import os
import sys
from pathlib import Path

from hdbcli import dbapi

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / "04_Security_Visualisation_Gerardo" / "soc-dashboard" / ".env.local"


def load_env():
    if not ENV.exists():
        print("ERROR: falta", ENV)
        sys.exit(1)
    for line in ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ[k.strip()] = v.strip()


def main():
    load_env()
    host = os.environ["HANA_HOST"]
    port = int(os.environ.get("HANA_PORT", "443"))
    user = os.environ["HANA_USER"]
    password = os.environ["HANA_PASSWORD"]

    conn = dbapi.connect(address=host, port=port, user=user, password=password, encrypt=True)
    cur = conn.cursor()

    count_sql = '''
    SELECT COUNT(*) FROM "SOC_LOGS"."SECURITY_API_LOGS"
    WHERE "SOURCE_TYPE" = 'SIMULATOR_V2'
       OR TO_NVARCHAR("RAW_PAYLOAD") LIKE '%SIMULATOR_V2%'
    '''
    cur.execute(count_sql)
    n_before = cur.fetchone()[0]
    print(f"Filas de simulador encontradas: {n_before}")

    if n_before == 0:
        print("Nada que borrar.")
        cur.close()
        conn.close()
        return

    delete_sql = '''
    DELETE FROM "SOC_LOGS"."SECURITY_API_LOGS"
    WHERE "SOURCE_TYPE" = 'SIMULATOR_V2'
       OR TO_NVARCHAR("RAW_PAYLOAD") LIKE '%SIMULATOR_V2%'
    '''
    cur.execute(delete_sql)
    conn.commit()
    print(f"Borradas {cur.rowcount} filas (aprox.; HANA puede reportar -1).")

    cur.execute(count_sql)
    n_after = cur.fetchone()[0]
    print(f"Quedan filas simulador: {n_after}")

    cur.close()
    conn.close()
    print("Listo. La vista V_ALERTS_24H reflejará el cambio al refrescar el dashboard.")


if __name__ == "__main__":
    main()
