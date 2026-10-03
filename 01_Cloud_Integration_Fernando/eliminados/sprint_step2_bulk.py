"""
sprint_step2_bulk.py - MVP Sprint STEP 2 (Ultra memory-safe)
Usa pq.ParquetFile.iter_batches(batch_size=10000) para ~80MB pico de RAM.
"""
import os, sys, uuid, math
import pyarrow.parquet as pq
from datetime import datetime
from hdbcli import dbapi

HANA_HOST     = os.environ.get("HANA_HOST")
HANA_PORT     = int(os.environ.get("HANA_PORT", "443"))
HANA_USER     = os.environ.get("HANA_USER", "DBADMIN")
HANA_PASSWORD = os.environ.get("HANA_PASSWORD")
PARQUET_PATH  = os.path.join(os.path.dirname(__file__), "final_sap_btp_dataset.parquet")
ARROW_BATCH   = 10_000   # rows per arrow batch (~80MB RAM peak)
SQL_BATCH     = 2_000    # rows per executemany

print(f"[{datetime.utcnow().isoformat()}] STEP 2 iniciado (iter_batches mode)")
if not os.path.exists(PARQUET_PATH):
    print(f"ERROR: {PARQUET_PATH} no existe"); sys.exit(1)

pf         = pq.ParquetFile(PARQUET_PATH)
total_rows = pf.metadata.num_rows
print(f"Parquet: {total_rows:,} filas")

conn   = dbapi.connect(address=HANA_HOST, port=HANA_PORT, user=HANA_USER, password=HANA_PASSWORD, encrypt=True)
cursor = conn.cursor()
print("OK Conectado\n")

SQL = (
    'UPSERT "SOC_LOGS"."SECURITY_DATA_FINAL" '
    '("EVENT_ID","TIMESTAMP","SRC_IP","DST_IP","SRC_PORT","DST_PORT","PROTOCOL",'
    '"DURATION","TOTAL_FWD_PACKETS","TOTAL_BWD_PACKETS","FLOW_BYTES_PER_S",'
    '"FLOW_PACKETS_PER_S","FWD_PKT_LEN_MEAN","BWD_PKT_LEN_MEAN",'
    '"FLAG_SYN","FLAG_ACK","FLAG_FIN","FLAG_RST","LABEL","TOTAL_BYTES",'
    '"ATTACK_CATEGORY","IS_ATTACK","DATASET_SOURCE","PAYLOAD") '
    'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) WITH PRIMARY KEY'
)

COLS = ["event_id","timestamp","src_ip","dst_ip","src_port","dst_port","protocol",
        "duration","total_fwd_packets","total_bwd_packets","flow_bytes_per_s",
        "flow_packets_per_s","fwd_pkt_len_mean","bwd_pkt_len_mean",
        "flag_syn","flag_ack","flag_fin","flag_rst","label","total_bytes",
        "attack_category","is_attack","dataset_source","payload"]

def sv(v, ml=None):
    if v is None: return ""
    s = str(v)
    if s in ("None","nan","NaT","<NA>","nat",""): return ""
    return s[:ml] if ml else s

def si(v):
    try:
        f = float(v)
        return 0 if (math.isnan(f) or math.isinf(f)) else int(f)
    except: return 0

def sf(v):
    try:
        f = float(v)
        return 0.0 if (math.isnan(f) or math.isinf(f)) else f
    except: return 0.0

def make_ts(v):
    if v is None: return None
    s = str(v)
    if s in ("None","nan","NaT","<NA>","nat",""): return None
    try:
        import pandas as pd
        return pd.to_datetime(s).to_pydatetime()
    except: return None

total_inserted = 0
sql_buf        = []
sql_batch_num  = 0

for arrow_batch in pf.iter_batches(batch_size=ARROW_BATCH, columns=COLS):
    cols_data = {c: arrow_batch.column(c).to_pylist() for c in COLS}
    n = len(cols_data["event_id"])

    for i in range(n):
        eid = sv(cols_data["event_id"][i], 50) or uuid.uuid4().hex[:50]
        sql_buf.append((
            eid,
            make_ts(cols_data["timestamp"][i]),
            sv(cols_data["src_ip"][i], 45),
            sv(cols_data["dst_ip"][i], 45),
            si(cols_data["src_port"][i]),
            si(cols_data["dst_port"][i]),
            sv(cols_data["protocol"][i], 10),
            sf(cols_data["duration"][i]),
            si(cols_data["total_fwd_packets"][i]),
            si(cols_data["total_bwd_packets"][i]),
            sf(cols_data["flow_bytes_per_s"][i]),
            sf(cols_data["flow_packets_per_s"][i]),
            sf(cols_data["fwd_pkt_len_mean"][i]),
            sf(cols_data["bwd_pkt_len_mean"][i]),
            si(cols_data["flag_syn"][i]),
            si(cols_data["flag_ack"][i]),
            si(cols_data["flag_fin"][i]),
            si(cols_data["flag_rst"][i]),
            sv(cols_data["label"][i], 50),
            si(cols_data["total_bytes"][i]),
            sv(cols_data["attack_category"][i], 100),
            si(cols_data["is_attack"][i]),
            sv(cols_data["dataset_source"][i], 50),
            sv(cols_data["payload"][i], 2000),
        ))

        if len(sql_buf) >= SQL_BATCH:
            try:
                cursor.executemany(SQL, sql_buf)
                conn.commit()
                total_inserted += len(sql_buf)
                sql_batch_num  += 1
                pct = total_inserted / total_rows * 100
                print(f"  [{sql_batch_num}] {pct:.1f}% — {total_inserted:,}/{total_rows:,}")
            except Exception as e:
                print(f"  ERROR [{sql_batch_num}]: {e}")
                conn.rollback()
            sql_buf = []

    del cols_data

# Flush remaining
if sql_buf:
    try:
        cursor.executemany(SQL, sql_buf)
        conn.commit()
        total_inserted += len(sql_buf)
        print(f"  Flush final: {total_inserted:,}")
    except Exception as e:
        print(f"  ERROR flush: {e}")
        conn.rollback()

cursor.execute('SELECT COUNT(*) FROM "SOC_LOGS"."SECURITY_DATA_FINAL"')
final_count = cursor.fetchone()[0]

cursor.execute(
    'SELECT "ATTACK_CATEGORY", COUNT(*) AS C FROM "SOC_LOGS"."SECURITY_DATA_FINAL" '
    'GROUP BY "ATTACK_CATEGORY" ORDER BY C DESC'
)
print("\nCategorias en HANA:")
for r in cursor.fetchall():
    print(f"   {r[0]}: {r[1]:,}")

cursor.close(); conn.close()
print(f"\nSTEP 2 COMPLETO | {total_inserted:,} insertados | {final_count:,} en HANA")
