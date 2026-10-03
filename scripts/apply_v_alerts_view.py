"""Aplica V_ALERTS_24H en HANA usando credenciales de .env.local (demo)."""
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

UNIFIED_DDL = """
CREATE OR REPLACE VIEW "SOC_LOGS"."V_ALERTS_24H" AS
SELECT
  "EVENT_ID",
  "TIMESTAMP",
  "SRC_IP",
  "DST_IP",
  "PROTOCOL",
  "ATTACK_CATEGORY",
  "PAYLOAD",
  "INGESTION_TS",
  "ALERT_SEVERITY"
FROM (
  SELECT
    f."EVENT_ID" AS "EVENT_ID",
    f."TIMESTAMP" AS "TIMESTAMP",
    f."SRC_IP" AS "SRC_IP",
    f."DST_IP" AS "DST_IP",
    f."PROTOCOL" AS "PROTOCOL",
    f."ATTACK_CATEGORY" AS "ATTACK_CATEGORY",
    f."PAYLOAD" AS "PAYLOAD",
    f."INGESTION_TS" AS "INGESTION_TS",
    CAST( N'HIGH' AS NVARCHAR(20) ) AS "ALERT_SEVERITY"
  FROM "SOC_LOGS"."SECURITY_DATA_FINAL" f
  WHERE f."IS_ATTACK" = 1
    AND f."TIMESTAMP" >= ADD_SECONDS( CURRENT_TIMESTAMP, -86400 )
  UNION ALL
  SELECT
    t."ID" AS "EVENT_ID",
    COALESCE( t."TIMESTAMP", t."INGESTION_TIMESTAMP" ) AS "TIMESTAMP",
    COALESCE(
      JSON_VALUE( SUBSTRING( TO_NVARCHAR( t."RAW_PAYLOAD" ), 1, 5000 ), '$.src_ip' ),
      CAST( '' AS NVARCHAR(45) )
    ) AS "SRC_IP",
    COALESCE(
      JSON_VALUE( SUBSTRING( TO_NVARCHAR( t."RAW_PAYLOAD" ), 1, 5000 ), '$.dst_ip' ),
      CAST( '' AS NVARCHAR(45) )
    ) AS "DST_IP",
    COALESCE(
      JSON_VALUE( SUBSTRING( TO_NVARCHAR( t."RAW_PAYLOAD" ), 1, 5000 ), '$.protocol' ),
      CAST( '' AS NVARCHAR(10) )
    ) AS "PROTOCOL",
    COALESCE(
      NULLIF( TRIM( t."LOG_TYPE" ), '' ),
      JSON_VALUE( SUBSTRING( TO_NVARCHAR( t."RAW_PAYLOAD" ), 1, 5000 ), '$.category' ),
      CAST( N'alert' AS NVARCHAR(100) )
    ) AS "ATTACK_CATEGORY",
    COALESCE( t."FUNCTION_MESSAGE", SUBSTRING( TO_NVARCHAR( t."RAW_PAYLOAD" ), 1, 5000 ) ) AS "PAYLOAD",
    t."INGESTION_TIMESTAMP" AS "INGESTION_TS",
    CAST(
      CASE
        WHEN COALESCE( t."FUNCTION_MESSAGE", '' ) LIKE N'%Severity: critical%' THEN N'CRITICAL'
        WHEN COALESCE( t."FUNCTION_MESSAGE", '' ) LIKE N'%Severity: high%'     THEN N'HIGH'
        WHEN SUBSTRING( TO_NVARCHAR( t."RAW_PAYLOAD" ), 1, 5000 ) LIKE N'%critical%'                 THEN N'HIGH'
        ELSE N'MEDIUM'
      END
      AS NVARCHAR(20)
    ) AS "ALERT_SEVERITY"
  FROM "SOC_LOGS"."SECURITY_API_LOGS" t
  WHERE t."INGESTION_TIMESTAMP" >= ADD_SECONDS( CURRENT_TIMESTAMP, -86400 )
)
"""

API_ONLY_DDL = """
CREATE OR REPLACE VIEW "SOC_LOGS"."V_ALERTS_24H" AS
SELECT
  t."ID" AS "EVENT_ID",
  COALESCE( t."TIMESTAMP", t."INGESTION_TIMESTAMP" ) AS "TIMESTAMP",
  COALESCE(
    JSON_VALUE( SUBSTRING( TO_NVARCHAR( t."RAW_PAYLOAD" ), 1, 5000 ), '$.src_ip' ),
    CAST( '' AS NVARCHAR(45) )
  ) AS "SRC_IP",
  COALESCE(
    JSON_VALUE( SUBSTRING( TO_NVARCHAR( t."RAW_PAYLOAD" ), 1, 5000 ), '$.dst_ip' ),
    CAST( '' AS NVARCHAR(45) )
  ) AS "DST_IP",
  COALESCE(
    JSON_VALUE( SUBSTRING( TO_NVARCHAR( t."RAW_PAYLOAD" ), 1, 5000 ), '$.protocol' ),
    CAST( '' AS NVARCHAR(10) )
  ) AS "PROTOCOL",
  COALESCE(
    NULLIF( TRIM( t."LOG_TYPE" ), '' ),
    JSON_VALUE( SUBSTRING( TO_NVARCHAR( t."RAW_PAYLOAD" ), 1, 5000 ), '$.category' ),
    CAST( N'alert' AS NVARCHAR(100) )
  ) AS "ATTACK_CATEGORY",
  COALESCE( t."FUNCTION_MESSAGE", SUBSTRING( TO_NVARCHAR( t."RAW_PAYLOAD" ), 1, 5000 ) ) AS "PAYLOAD",
  t."INGESTION_TIMESTAMP" AS "INGESTION_TS",
  CAST(
    CASE
      WHEN COALESCE( t."FUNCTION_MESSAGE", '' ) LIKE N'%Severity: critical%' THEN N'CRITICAL'
      WHEN COALESCE( t."FUNCTION_MESSAGE", '' ) LIKE N'%Severity: high%'     THEN N'HIGH'
      WHEN SUBSTRING( TO_NVARCHAR( t."RAW_PAYLOAD" ), 1, 5000 ) LIKE N'%critical%'                 THEN N'HIGH'
      ELSE N'MEDIUM'
    END
    AS NVARCHAR(20)
  ) AS "ALERT_SEVERITY"
FROM "SOC_LOGS"."SECURITY_API_LOGS" t
WHERE t."INGESTION_TIMESTAMP" >= ADD_SECONDS( CURRENT_TIMESTAMP, -86400 )
"""

GRANTS = [
    'GRANT SELECT ON "SOC_LOGS"."V_ALERTS_24H" TO "SAP_DASHBOARD_USER"',
    'GRANT SELECT ON "SOC_LOGS"."V_ALERTS_24H" TO "ROLE_DASHBOARD"',
]

def main():
    load_env()
    host = os.environ["HANA_HOST"]
    port = int(os.environ.get("HANA_PORT", "443"))
    user = os.environ["HANA_USER"]
    password = os.environ["HANA_PASSWORD"]
    print("Conectando a HANA:", host, "usuario:", user)
    conn = dbapi.connect(address=host, port=port, user=user, password=password, encrypt=True)
    cur = conn.cursor()

    def run(ddl: str, label: str) -> None:
        print(label, "...")
        cur.execute(ddl)
        conn.commit()
        print("OK", label)

    try:
        run(UNIFIED_DDL.strip(), "CREATE VIEW (unificada)")
    except Exception as e:
        print("Unificada falló:", e)
        try:
            run(API_ONLY_DDL.strip(), "CREATE VIEW (solo SECURITY_API_LOGS)")
        except Exception as e2:
            print("ERROR:", e2)
            sys.exit(1)

    for g in GRANTS:
        try:
            cur.execute(g)
            conn.commit()
            print("OK", g.split("TO")[1].strip())
        except Exception as e:
            print("SKIP grant (usuario/rol puede no existir):", e)

    cur.execute('SELECT COUNT(*) FROM "SOC_LOGS"."V_ALERTS_24H"')
    n = cur.fetchone()[0]
    print("Filas visibles en V_ALERTS_24H ahora:", n)
    cur.close()
    conn.close()
    print("Listo.")

if __name__ == "__main__":
    main()
