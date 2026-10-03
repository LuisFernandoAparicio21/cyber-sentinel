# ================================================
# 01_ingest_api.py — Fernando Aparicio
# Cloud Integration Engineer — SAP SOC API Ingest
# v4.0 — Schema SOC_LOGS (6 tablas) + Flask + Bulk Replenishment
# ================================================

import requests
import pandas as pd
import time
import os
import json
import threading
from datetime import datetime
import uuid
import hashlib
import hmac
from contextlib import contextmanager
import functools
from typing import Dict, Optional, Tuple
from flask import Flask, jsonify, request

from sap_classifier import classify

# ── Configuración central ──────────────────────────────────────────────────────
API_BASE       = os.environ["SOC_API_BASE"]          # SAP-provided API, set in the environment
TOKEN          = os.environ["SOC_API_TOKEN"]
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]
HEADERS_AUTH   = {"Authorization": f"Bearer {TOKEN}"}
OUTPUT_DIR     = os.path.join(os.path.dirname(__file__), "raw_api_batches")

HANA_HOST     = os.environ.get("HANA_HOST", "")
HANA_PORT     = int(os.environ.get("HANA_PORT", "443"))
HANA_USER     = os.environ.get("HANA_USER", "SOC_WRITER")
HANA_PASSWORD = os.environ.get("HANA_PASSWORD", "")

os.makedirs(OUTPUT_DIR, exist_ok=True)

# ── Enum constraints (from SAP_HANA_SCRIPT_COMPLETO.sql) ──────────────────────
VALID_LOG_TYPES    = frozenset({"INFO","WARNING","ERROR","DEBUG","AUDIT","PERF",
                                 "SECURITY","LLM_REQUEST","LLM_ERROR","LLM_TIMEOUT"})
VALID_SOURCE_TYPES = frozenset({"OData","BAPI","REST","SOAP","RFC","IDOC","BTP-Event"})
VALID_APP_ENVS     = frozenset({"production","staging","qa","development","sandbox"})
VALID_LLM_STATUSES = frozenset({"success","error","timeout"})
VALID_APP_IDS      = frozenset({"sap-btp-cf","soc-api-ingestor","soc-api",
                                  "soc-dashboard","sap-logs","unknown",
                                  "sap-analytics-cloud","sap-ariba","sap-s4hana-sales",
                                  "sap-concur","sap-s4hana-finance","sap-successfactors",
                                  "sap-fiori-launchpad","sap-data-sphere","sap-intelligent-rpa"})
VALID_SERVICE_IDS  = frozenset({"alert-notification","ai-core","hana-cloud",
                                  "dar","webhook","unknown","data-pipeline"})
VALID_REGION_IDS   = None  # Acepta cualquier region_id; se valida dinámicamente en HANA

# ── Flask App ──────────────────────────────────────────────────────────────────
app = Flask(__name__)

# ── HANA helpers ───────────────────────────────────────────────────────────────
def get_hana_connection():
    """Opens a HANA connection from env vars or VCAP_SERVICES (CF bind-service)."""
    vcap_raw = os.environ.get("VCAP_SERVICES", "{}")
    try:
        hana_services = json.loads(vcap_raw).get("hana", [])
        creds = hana_services[0].get("credentials", {}) if hana_services else {}
        host = creds.get("host", HANA_HOST)
        port = int(creds.get("port", HANA_PORT))
    except Exception:
        host, port = HANA_HOST, HANA_PORT

    if not host or not HANA_PASSWORD:
        print("⚠️  Sin credenciales HANA. Solo se guardará Parquet local.")
        return None
    try:
        from hdbcli import dbapi
        return dbapi.connect(address=host, port=port, user=HANA_USER,
                             password=HANA_PASSWORD, encrypt=True)
    except ImportError:
        print("⚠️  hdbcli no instalado.")
        return None
    except Exception as e:
        print(f"❌ Error conectando a HANA: {e}")
        return None


def _ddl(cursor, sql: str):
    """Executes a DDL statement, silently ignoring 'already exists' errors."""
    try:
        cursor.execute(sql)
    except Exception:
        pass


def bootstrap_schema(conn):
    """Idempotent: creates the SOC_LOGS 6-table schema + views + catalog seed.
    Safe to run on every startup — all DDL wrapped in try/except.
    """
    cur = conn.cursor()

    # ── Schema ─────────────────────────────────────────────────────────────────
    _ddl(cur, 'CREATE SCHEMA "SOC_LOGS"')

    # ── Catalog tables ─────────────────────────────────────────────────────────
    _ddl(cur, '''
        CREATE COLUMN TABLE "SOC_LOGS"."SOC_APPLICATIONS" (
            "APPLICATION_ID"   NVARCHAR(50)  NOT NULL,
            "APPLICATION_NAME" NVARCHAR(100) NOT NULL,
            PRIMARY KEY ("APPLICATION_ID")
        )
    ''')
    _ddl(cur, '''
        CREATE COLUMN TABLE "SOC_LOGS"."SOC_REGIONS" (
            "REGION_ID"    NVARCHAR(20)  NOT NULL,
            "REGION_CODE"  NVARCHAR(20)  NOT NULL,
            "REGION_NAME"  NVARCHAR(100) NOT NULL,
            "MACRO_REGION" NVARCHAR(50)  NOT NULL,
            PRIMARY KEY ("REGION_ID")
        )
    ''')
    _ddl(cur, '''
        CREATE COLUMN TABLE "SOC_LOGS"."SOC_SERVICES" (
            "SERVICE_ID"   NVARCHAR(50)  NOT NULL,
            "SERVICE_NAME" NVARCHAR(100) NULL,
            PRIMARY KEY ("SERVICE_ID")
        )
    ''')

    # ── Fact table ─────────────────────────────────────────────────────────────
    _ddl(cur, '''
        CREATE COLUMN TABLE "SOC_LOGS"."SOC_EVENTS" (
            "EVENT_ID"        NVARCHAR(36)   NOT NULL,
            "EVENT_HASH"      NVARCHAR(64)   NOT NULL,
            "EVENT_TIMESTAMP" TIMESTAMP      NOT NULL,
            "EVENT_REQUESTED" TIMESTAMP      NOT NULL,
            "LOG_TYPE"        NVARCHAR(20)   NOT NULL,
            "LOG_MESSAGE"     NVARCHAR(1000) NOT NULL,
            "HTTP_STATUS"     SMALLINT,
            "CLIENT_IP"       NVARCHAR(45),
            "REQUEST_METHOD"  NVARCHAR(10),
            "REQUEST_PATH"    NVARCHAR(500),
            "CONTENT_TYPE"    NVARCHAR(100),
            "HTTP_HOST"       NVARCHAR(200),
            "SOURCE_TYPE"     NVARCHAR(20)   NOT NULL,
            "APP_ENV"         NVARCHAR(20)   NOT NULL,
            "CODE_VERSION"    NVARCHAR(20)   NOT NULL,
            "INDEX_NAME"      NVARCHAR(50)   NOT NULL,
            "APPLICATION_ID"  NVARCHAR(50)   NOT NULL,
            "REGION_ID"       NVARCHAR(20)   NOT NULL,
            "SERVICE_ID"      NVARCHAR(50),
            PRIMARY KEY ("EVENT_ID"),
            FOREIGN KEY ("APPLICATION_ID") REFERENCES "SOC_LOGS"."SOC_APPLICATIONS"("APPLICATION_ID"),
            FOREIGN KEY ("REGION_ID")      REFERENCES "SOC_LOGS"."SOC_REGIONS"("REGION_ID"),
            FOREIGN KEY ("SERVICE_ID")     REFERENCES "SOC_LOGS"."SOC_SERVICES"("SERVICE_ID")
        )
    ''')

    # ── LLM details (1:1 with SOC_EVENTS) ─────────────────────────────────────
    _ddl(cur, '''
        CREATE COLUMN TABLE "SOC_LOGS"."SOC_LLM_REQUESTS" (
            "EVENT_ID"            NVARCHAR(36)  NOT NULL,
            "LLM_PROVIDER"        NVARCHAR(30)  NOT NULL,
            "LLM_MODEL_ID"        NVARCHAR(50)  NOT NULL,
            "LLM_STATUS"          NVARCHAR(20)  NOT NULL,
            "LLM_FINISH_REASON"   NVARCHAR(20),
            "PROMPT_TOKENS"       INTEGER,
            "COMPLETION_TOKENS"   INTEGER,
            "TOTAL_TOKENS"        INTEGER,
            "RESPONSE_SIZE_BYTES" INTEGER,
            "RESPONSE_TIME_MS"    DECIMAL(12,3),
            "COST_USD"            DECIMAL(10,6),
            "TEMPERATURE"         DECIMAL(4,2),
            PRIMARY KEY ("EVENT_ID"),
            FOREIGN KEY ("EVENT_ID") REFERENCES "SOC_LOGS"."SOC_EVENTS"("EVENT_ID")
        )
    ''')

    # ── AI predictions (1:1 with SOC_EVENTS) ──────────────────────────────────
    _ddl(cur, '''
        CREATE COLUMN TABLE "SOC_LOGS"."MODEL_PREDICTIONS" (
            "EVENT_ID"         NVARCHAR(36)  NOT NULL,
            "PREDICTION_LABEL" NVARCHAR(20)  NOT NULL,
            "CONFIDENCE"       DECIMAL(5,2)  NOT NULL,
            "SEVERITY"         NVARCHAR(20),
            "REASON"           NVARCHAR(500),
            "SCORED_AT"        TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY ("EVENT_ID"),
            FOREIGN KEY ("EVENT_ID") REFERENCES "SOC_LOGS"."SOC_EVENTS"("EVENT_ID")
        )
    ''')

    # ── Indices ────────────────────────────────────────────────────────────────
    for idx in [
        'CREATE INDEX "IDX_SOC_EVENTS_TIMESTAMP" ON "SOC_LOGS"."SOC_EVENTS"("EVENT_TIMESTAMP")',
        'CREATE INDEX "IDX_SOC_EVENTS_CLIENT_IP" ON "SOC_LOGS"."SOC_EVENTS"("CLIENT_IP")',
        'CREATE INDEX "IDX_SOC_EVENTS_LOG_TYPE"  ON "SOC_LOGS"."SOC_EVENTS"("LOG_TYPE")',
    ]:
        _ddl(cur, idx)

    # ── Analytical views ───────────────────────────────────────────────────────
    cur.execute('''
        CREATE OR REPLACE VIEW "SOC_LOGS"."V_DASHBOARD_ALERTS" AS
        SELECT
            E."EVENT_ID",
            E."EVENT_ID"        AS "ID",
            E."EVENT_TIMESTAMP" AS "TIMESTAMP",
            M."PREDICTION_LABEL",
            M."CONFIDENCE",
            M."SEVERITY",
            M."REASON"          AS "DETECTION_REASON",
            E."LOG_TYPE",
            E."SOURCE_TYPE",
            E."HTTP_STATUS",
            E."REQUEST_PATH",
            L."LLM_FINISH_REASON",
            L."PROMPT_TOKENS"   AS "LLM_PROMPT_TOKENS",
            L."RESPONSE_TIME_MS" AS "LLM_RESPONSE_TIME_MS",
            E."LOG_MESSAGE"     AS "FUNCTION_MESSAGE",
            E."APPLICATION_ID"  AS "FUNCTION_APPLICATION",
            CASE WHEN M."PREDICTION_LABEL" = 'ATTACK' THEN 1 ELSE 0 END AS "IS_ATTACK",
            CURRENT_TIMESTAMP   AS "INSERTION_DATE"
        FROM "SOC_LOGS"."SOC_EVENTS" E
        LEFT JOIN "SOC_LOGS"."MODEL_PREDICTIONS" M ON E."EVENT_ID" = M."EVENT_ID"
        LEFT JOIN "SOC_LOGS"."SOC_LLM_REQUESTS"  L ON E."EVENT_ID" = L."EVENT_ID"
    ''')
    cur.execute('''
        CREATE OR REPLACE VIEW "SOC_LOGS"."V_METRICS_BY_APP" AS
        SELECT
            A."APPLICATION_NAME",
            COUNT(*)  AS "TOTAL_EVENTS",
            SUM(CASE WHEN E."LOG_TYPE" IN ('ERROR','LLM_ERROR') THEN 1 ELSE 0 END) AS "ERROR_COUNT",
            AVG(L."RESPONSE_TIME_MS") AS "AVG_LATENCY"
        FROM "SOC_LOGS"."SOC_EVENTS" E
        JOIN  "SOC_LOGS"."SOC_APPLICATIONS"  A ON E."APPLICATION_ID" = A."APPLICATION_ID"
        LEFT JOIN "SOC_LOGS"."SOC_LLM_REQUESTS" L ON E."EVENT_ID" = L."EVENT_ID"
        GROUP BY A."APPLICATION_NAME"
    ''')
    cur.execute('''
        CREATE OR REPLACE VIEW "SOC_LOGS"."V_METRICS_LLM" AS
        SELECT
            L."LLM_PROVIDER",
            L."LLM_MODEL_ID",
            COUNT(*) AS "TOTAL_REQUESTS",
            SUM(L."TOTAL_TOKENS")       AS "SUM_TOKENS",
            AVG(L."RESPONSE_TIME_MS")   AS "AVG_TIME"
        FROM "SOC_LOGS"."SOC_LLM_REQUESTS" L
        GROUP BY L."LLM_PROVIDER", L."LLM_MODEL_ID"
    ''')

    # ── Catalog seed (idempotent UPSERT) ───────────────────────────────────────
    apps = [
        ("sap-btp-cf",       "SAP BTP Cloud Foundry"),
        ("soc-api-ingestor", "SOC API Ingestor"),
        ("soc-api",          "SOC API (DAR/ML)"),
        ("soc-dashboard",    "SOC Dashboard (Next.js)"),
        ("sap-logs",         "SAP Logs Generic"),
        ("unknown",          "Unknown Application"),
    ]
    for app_id, app_name in apps:
        cur.execute(
            'UPSERT "SOC_LOGS"."SOC_APPLICATIONS" ("APPLICATION_ID","APPLICATION_NAME") '
            'VALUES (?,?) WITH PRIMARY KEY', (app_id, app_name)
        )

    regions = [
        ("EU-015",  "eu-central-1",   "Europe (Frankfurt)",      "EMEA"),
        ("US-010",  "us-east-1",      "US East (N. Virginia)",   "AMER"),
        ("US-030",  "us10",           "US West (SAP BTP US10)",  "AMER"),
        ("AP-020",  "ap-southeast-1", "Asia Pacific (Singapore)","APAC"),
        ("BR-010",  "sa-east-1",      "Brazil (São Paulo)",      "LATAM"),
        ("MX-010",  "mx-central-1",   "Mexico (UPAEP)",          "LATAM"),
        ("unknown", "unknown",        "Unknown Region",          "unknown"),
    ]
    for rid, code, name, macro in regions:
        cur.execute(
            'UPSERT "SOC_LOGS"."SOC_REGIONS" ("REGION_ID","REGION_CODE","REGION_NAME","MACRO_REGION") '
            'VALUES (?,?,?,?) WITH PRIMARY KEY', (rid, code, name, macro)
        )

    services = [
        ("alert-notification", "SAP Alert Notification Service"),
        ("ai-core",            "SAP AI Core"),
        ("hana-cloud",         "SAP HANA Cloud"),
        ("dar",                "SAP Data Attribute Recommendation"),
        ("webhook",            "Webhook Receiver"),
        ("unknown",            "Unknown / Unlisted Service"),
    ]
    for svc_id, svc_name in services:
        cur.execute(
            'UPSERT "SOC_LOGS"."SOC_SERVICES" ("SERVICE_ID","SERVICE_NAME") '
            'VALUES (?,?) WITH PRIMARY KEY', (svc_id, svc_name)
        )

    conn.commit()
    cur.close()
    print("[DB] Bootstrap SOC_LOGS completado (schema + vistas + catálogos).")


@contextmanager
def hana_cursor():
    """Yields (conn, cursor) for Flask routes; aborts 503 when HANA is unavailable."""
    conn = get_hana_connection()
    if conn is None:
        from flask import abort
        abort(503, description="HANA unavailable")
    cursor = conn.cursor()
    try:
        yield conn, cursor
    finally:
        cursor.close()
        conn.close()


# ── Normalization helpers ──────────────────────────────────────────────────────
def _coerce(val, default=None):
    """Returns val if non-null/non-empty, else default."""
    if val is None:
        return default
    try:
        if pd.isna(val):
            return default
    except (TypeError, ValueError):
        pass
    if isinstance(val, str) and not val.strip():
        return default
    return val


def _first_of(row: Dict, *keys, default=None):
    """Returns the first non-null value among the given keys in row."""
    for k in keys:
        v = _coerce(row.get(k))
        if v is not None:
            return v
    return default


def _normalize_log_type(raw: str) -> str:
    if raw in VALID_LOG_TYPES:
        return raw
    upper = (raw or "").upper()
    if upper in VALID_LOG_TYPES:
        return upper
    if "LLM" in upper:
        return "LLM_REQUEST"
    if "ERR" in upper:
        return "ERROR"
    if "WARN" in upper:
        return "WARNING"
    return "INFO"


def _normalize_source_type(raw: str) -> str:
    if raw in VALID_SOURCE_TYPES:
        return raw
    _map = {"rest": "REST", "odata": "OData", "bapi": "BAPI", "soap": "SOAP",
            "rfc": "RFC", "idoc": "IDOC", "webhook": "BTP-Event", "btp-event": "BTP-Event"}
    return _map.get((raw or "").lower(), "REST")


def _normalize_app_env(raw: str) -> str:
    if raw in VALID_APP_ENVS:
        return raw
    return "production" if not raw else "development"


def _normalize_llm_status(finish_reason: str) -> str:
    if finish_reason in VALID_LLM_STATUSES:
        return finish_reason
    fr = (finish_reason or "").lower()
    if "stop" in fr or "length" in fr:
        return "success"
    if "timeout" in fr:
        return "timeout"
    return "error"


def _normalize_app_id(raw: str) -> str:
    return raw if raw in VALID_APP_IDS else "unknown"


def _normalize_service_id(raw: str) -> str:
    if raw in VALID_SERVICE_IDS:
        return raw
    return "unknown"


def _normalize_region_id(raw: str) -> str:
    cleaned = str(raw).strip()[:20]
    return cleaned if cleaned else "unknown"


# ── HANA write helpers (one per target table) ─────────────────────────────────
def _insert_event(cursor, row: Dict) -> str:
    """Upserts one record into SOC_EVENTS. Returns the event_id."""
    raw_ts = _first_of(row, "@timestamp", "timestamp", "TIMESTAMP")
    try:
        ts = pd.to_datetime(raw_ts).to_pydatetime() if raw_ts else datetime.utcnow()
    except Exception:
        ts = datetime.utcnow()

    event_id   = str(_first_of(row, "_id", "id", "EVENT_ID") or uuid.uuid4())[:36]
    raw_hash   = _first_of(row, "event_hash", "EVENT_HASH", default="")
    event_hash = str(hashlib.sha256(event_id.encode()).hexdigest()
                     if not raw_hash else raw_hash)[:64]

    log_type    = _normalize_log_type(str(_first_of(row, "sap_function_log_type",
                                                     "category", "LOG_TYPE", "log_type",
                                                     default="INFO")))
    source_type = _normalize_source_type(str(_first_of(row, "sap_source_type",
                                                        "source", "SOURCE_TYPE", default="REST")))
    app_env     = _normalize_app_env(str(_first_of(row, "sap_app_env", "APP_ENV",
                                                    default="production")))
    app_id      = _normalize_app_id(str(_first_of(row, "sap_function_application",
                                                   "APPLICATION_ID", default="unknown")))

    cursor.execute(
        'UPSERT "SOC_LOGS"."SOC_EVENTS" '
        '("EVENT_ID","EVENT_HASH","EVENT_TIMESTAMP","EVENT_REQUESTED","LOG_TYPE",'
        '"LOG_MESSAGE","HTTP_STATUS","CLIENT_IP","REQUEST_METHOD","REQUEST_PATH",'
        '"CONTENT_TYPE","HTTP_HOST","SOURCE_TYPE","APP_ENV","CODE_VERSION",'
        '"INDEX_NAME","APPLICATION_ID","REGION_ID","SERVICE_ID") '
        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) WITH PRIMARY KEY',
        (
            event_id,
            event_hash,
            ts,
            ts,
            log_type,
            str(_first_of(row, "sap_function_message", "payload", "message",
                          default="") or "")[:1000],
            _coerce(row.get("http_status"), None),
            (str(_first_of(row, "client_ip", "CLIENT_IP", default="") or "")[:45] or None),
            (str(_first_of(row, "headers_http_request_method", default="") or "")[:10] or None),
            (str(_first_of(row, "heathers_request_path", "request_path", default="") or "")[:500] or None),
            (str(_first_of(row, "headers_content_type", default="") or "")[:100] or None),
            (str(_first_of(row, "headers_http_host", default="") or "")[:200] or None),
            source_type,
            app_env,
            str(_first_of(row, "event_code_version", "CODE_VERSION", default="1.0") or "1.0")[:20],
            str(_first_of(row, "index_name", "INDEX_NAME", default="_api") or "_api")[:50],
            app_id,
            _normalize_region_id(str(_first_of(row, "region_id", "REGION_ID", default="unknown") or "unknown")),
            _normalize_service_id(str(_first_of(row, "service_id", "SERVICE_ID", default="unknown") or "unknown")),
        )
    )
    return event_id


def _insert_llm_request(cursor, event_id: str, row: Dict):
    """Upserts into SOC_LLM_REQUESTS only when LLM fields are present."""
    model_id = str(_first_of(row, "llm_model_id", "LLM_MODEL", default="") or "")
    if not model_id:
        return

    provider      = (model_id.split("-")[0] if "-" in model_id
                     else model_id.split("/")[0] if "/" in model_id
                     else "unknown")
    finish_reason = str(_first_of(row, "llm_finish_reason", default="") or "")
    llm_status    = _normalize_llm_status(finish_reason)

    cursor.execute(
        'UPSERT "SOC_LOGS"."SOC_LLM_REQUESTS" '
        '("EVENT_ID","LLM_PROVIDER","LLM_MODEL_ID","LLM_STATUS","LLM_FINISH_REASON",'
        '"PROMPT_TOKENS","COMPLETION_TOKENS","TOTAL_TOKENS","RESPONSE_SIZE_BYTES","TEMPERATURE") '
        'VALUES (?,?,?,?,?,?,?,?,?,?) WITH PRIMARY KEY',
        (
            event_id,
            provider[:30],
            model_id[:50],
            llm_status,
            (finish_reason[:20] or None),
            int(_coerce(row.get("llm_prompt_tokens"), 0) or 0),
            int(_coerce(row.get("llm_completion_tokens"), 0) or 0),
            int(_coerce(row.get("llm_total_tokens"), 0) or 0),
            int(_coerce(row.get("sap_llm_response_size"), 0) or 0),
            float(_coerce(row.get("llm_temperature"), 0.0) or 0.0),
        )
    )


def _insert_prediction(cursor, event_id: str, row: Dict):
    """Runs sap_classifier and upserts result into MODEL_PREDICTIONS."""
    features = {
        "llm_response_time_ms": _coerce(row.get("llm_response_time_ms"), 0),
        "http_status_code":     _coerce(row.get("http_status"), 0),
    }
    pred = classify(dict(row), features)
    severity = "CRITICAL" if pred["confidence"] >= 0.85 else "HIGH" if pred["confidence"] >= 0.70 else "MEDIUM"

    cursor.execute(
        'UPSERT "SOC_LOGS"."MODEL_PREDICTIONS" '
        '("EVENT_ID","PREDICTION_LABEL","CONFIDENCE","SEVERITY","REASON") '
        'VALUES (?,?,?,?,?) WITH PRIMARY KEY',
        (
            event_id,
            pred["label"][:20],
            round(float(pred["confidence"]), 2),
            (severity if pred["is_attack"] else None),
            (pred.get("reason", "")[:500] or None),
        )
    )


def insert_to_hana(df: pd.DataFrame, conn, _window_start: str):
    """Inserts all rows into SOC_EVENTS, SOC_LLM_REQUESTS, and MODEL_PREDICTIONS."""
    if df.empty:
        print("⏭️  DataFrame vacío — nada que insertar.")
        return

    cursor = conn.cursor()
    for _, row in df.iterrows():
        row_dict  = dict(row)
        event_id  = _insert_event(cursor, row_dict)
        _insert_llm_request(cursor, event_id, row_dict)
        _insert_prediction(cursor, event_id, row_dict)

    conn.commit()
    cursor.close()
    print(f"✅ {len(df):,} eventos insertados/actualizados en SAP HANA Cloud.")


# ── Flask auth decorator ───────────────────────────────────────────────────────
def require_api_key(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        api_key = request.headers.get("X-API-Key", "")
        if not hmac.compare_digest(api_key, WEBHOOK_SECRET):
            return jsonify({"error": "Unauthorized", "hint": "Send X-API-Key header"}), 401
        return f(*args, **kwargs)
    return decorated


@app.errorhandler(503)
def service_unavailable(e):
    return jsonify({"error": str(e.description)}), 503


# ── Flask routes ───────────────────────────────────────────────────────────────
@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "soc-api-ingestor", "version": "4.0"})


@app.route("/api/webhook", methods=["POST"])
@require_api_key
def webhook_receiver():
    """Receives alert/log events pushed from SAP's Webhook system.
    Accepts single event or batch: {"events": [...]}
    """
    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"error": "Empty or invalid JSON body"}), 400

    events = payload.get("events", [payload]) if isinstance(payload, dict) else payload
    try:
        df = pd.DataFrame(events)
        if df.empty:
            return jsonify({"received": 0, "status": "empty_payload"}), 200
        with hana_cursor() as (conn, _):
            insert_to_hana(df, conn, datetime.utcnow().isoformat())
        return jsonify({"received": len(df), "status": "inserted"}), 200
    except Exception as e:
        print(f"Webhook error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/replenish", methods=["POST"])
@require_api_key
def replenish_data():
    """Bulk data replenishment for backfill/reprocessing."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Expected JSON with 'records' array"}), 400

    records = payload.get("records", [])
    if not records:
        return jsonify({"error": "No records provided"}), 400

    try:
        df = pd.DataFrame(records)
        with hana_cursor() as (conn, _):
            insert_to_hana(df, conn, datetime.utcnow().isoformat())
            ts          = datetime.utcnow().strftime("%Y-%m-%d_%H-%M-%S")
            backup_path = os.path.join(OUTPUT_DIR, f"replenish_{ts}.parquet")
            df.to_parquet(backup_path, index=False)
        return jsonify({"inserted": len(df), "backup": backup_path, "status": "ok"}), 200
    except Exception as e:
        print(f"Replenish error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/logs")
def get_logs():
    """Returns recent records from V_DASHBOARD_ALERTS."""
    limit    = min(request.args.get("limit", 100, type=int), 1000)
    log_type = request.args.get("log_type", None)

    with hana_cursor() as (_, cursor):
        if log_type:
            normalized = _normalize_log_type(log_type)
            cursor.execute(
                f'SELECT TOP {int(limit)} * FROM "SOC_LOGS"."V_DASHBOARD_ALERTS" '
                f'WHERE "LOG_TYPE" = ? ORDER BY "TIMESTAMP" DESC',
                (normalized,)
            )
        else:
            cursor.execute(
                f'SELECT TOP {int(limit)} * FROM "SOC_LOGS"."V_DASHBOARD_ALERTS" '
                f'ORDER BY "TIMESTAMP" DESC'
            )
        cols = [d[0] for d in cursor.description]
        rows = [dict(zip(cols, [str(v) if v is not None else None for v in r]))
                for r in cursor.fetchall()]

    return jsonify({"total": len(rows), "limit": limit, "data": rows})


@app.route("/api/stats")
def get_stats():
    """Quick stats from SOC_EVENTS."""
    with hana_cursor() as (_, cursor):
        cursor.execute('SELECT COUNT(*) FROM "SOC_LOGS"."SOC_EVENTS"')
        total = cursor.fetchone()[0]
        cursor.execute(
            'SELECT "LOG_TYPE", COUNT(*) AS CNT FROM "SOC_LOGS"."SOC_EVENTS" '
            'GROUP BY "LOG_TYPE" ORDER BY CNT DESC'
        )
        by_type = {r[0]: r[1] for r in cursor.fetchall()}

    return jsonify({"total_records": total, "by_log_type": by_type})


# ── Ingestion loop helpers ─────────────────────────────────────────────────────
LOG_FILE = os.path.join(OUTPUT_DIR, "log_extracciones.txt")


def _window_already_downloaded(window_start: str) -> bool:
    if not os.path.exists(LOG_FILE):
        return False
    with open(LOG_FILE, "r") as f:
        return window_start in f.read().splitlines()


def _register_download(window_start: str):
    with open(LOG_FILE, "a") as f:
        f.write(window_start + "\n")


def check_server_alive() -> bool:
    try:
        r     = requests.get(f"{API_BASE}/health", timeout=10)
        alive = r.status_code == 200 and r.json().get("status") == "ok"
        print("✅ Servidor vivo." if alive else f"⚠️ Respuesta inesperada: {r.status_code}")
        return alive
    except requests.exceptions.RequestException as e:
        print(f"❌ Sin conexión: {e}")
        return False


_RETRY_BACKOFF = [10, 30, 60]  # Tiempos de espera mucho más largos para evitar el ban


def _fetch_page(page: int) -> Optional[dict]:
    """Fetches one page from /logs/current with exponential backoff.

    Returns the parsed JSON dict, or None if all retries are exhausted.
    Handles HTTP errors (503) and empty-body JSON decode failures.
    """
    url = f"{API_BASE}/logs/current"
    for attempt, wait in enumerate([0] + _RETRY_BACKOFF, start=1):
        if wait:
            time.sleep(wait)
        try:
            r = requests.get(url, headers=HEADERS_AUTH,
                             params={"page": page}, timeout=30)
            r.raise_for_status()
            data = r.json()
            if attempt > 1:
                print(f"   ✅ Página {page} OK en intento {attempt}.")
            return data
        except (requests.exceptions.HTTPError,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as e:
            print(f"   ⚠️  Página {page} — intento {attempt}/{len(_RETRY_BACKOFF) + 1} "
                  f"— HTTP error: {e}")
        except ValueError as e:
            # requests raises ValueError (JSONDecodeError) on empty/invalid body
            print(f"   ⚠️  Página {page} — intento {attempt}/{len(_RETRY_BACKOFF) + 1} "
                  f"— JSON inválido: {e}")

    print(f"   ❌ Página {page} descartada tras {len(_RETRY_BACKOFF) + 1} intentos "
          f"(registrada para auditoría).")
    return None


def fetch_all_logs() -> Optional[Tuple[pd.DataFrame, str]]:
    """Downloads all pages of /logs/current. Returns (df, window_start) or None."""
    print("\n📡 Iniciando descarga de logs...")

    first = _fetch_page(1)
    if first is None:
        print("❌ No se pudo descargar la página 1 — abortando ciclo.")
        return None

    total_pages  = first["total_pages"]
    window_start = first["window_start"]
    records      = list(first["data"])
    failed_pages: list[int] = []
    print(f"🖥️ Ventana: {window_start} → {first['window_end']}  "
          f"({first['total_records']:,} registros, {total_pages} páginas)")

    for page in range(2, total_pages + 1):
        result = _fetch_page(page)
        if result is not None:
            records.extend(result["data"])
        else:
            failed_pages.append(page)
        time.sleep(1.5)  # <-- Ralentizamos las peticiones (antes 0.5s) para no estresar la API
        if page % 10 == 0:
            print(f"   ⏳ {page}/{total_pages} páginas procesadas...")

    if failed_pages:
        print(f"⚠️  Páginas con fallo permanente: {failed_pages} "
              f"({len(failed_pages)}/{total_pages}). "
              f"Registros obtenidos: {len(records):,}/{first['total_records']:,}.")
    else:
        print(f"✅ Descarga completa: {len(records):,} registros (sin fallos).")

    return pd.DataFrame(records), window_start
def save_to_parquet(df: pd.DataFrame, window_start: str) -> str:
    ts_clean = window_start.replace(":", "-").replace("+", "").replace(" ", "_")[:19]
    path     = os.path.join(OUTPUT_DIR, f"raw_logs_{ts_clean}.parquet")
    df.to_parquet(path, index=False)
    print(f"[DISK] Parquet guardado: {path}  ({df.shape[0]} filas x {df.shape[1]} cols)")
    return path


# ── Main ingestion cycle ───────────────────────────────────────────────────────
def main():
    if not check_server_alive():
        print("🛑 Servidor no responde — abortando ciclo.")
        return

    result = fetch_all_logs()
    if result is None:
        print("🛑 No se pudo descargar ningún log.")
        return

    df, window_start = result

    if _window_already_downloaded(window_start):
        print(f"⏭️  Ventana {window_start} ya procesada — saltando.")
        return

    save_to_parquet(df, window_start)

    conn = get_hana_connection()
    if conn:
        try:
            insert_to_hana(df, conn, window_start)
        except Exception as e:
            print(f"⚠️  Error insertando en HANA: {e}. Parquet local conservado.")
        finally:
            conn.close()
    else:
        print("ℹ️  HANA no configurado. Datos guardados en Parquet local.")

    _register_download(window_start)
    print("[OK] Ciclo de ingesta completado.")


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))

    flask_thread = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False),
        daemon=True,
    )
    flask_thread.start()
    print(f"Flask activo en :{port}")
    print(f"   GET  /health  |  GET  /api/logs  |  GET  /api/stats")
    print(f"   POST /api/webhook  |  POST /api/replenish")

    time.sleep(2)

    # Bootstrap schema once at startup (idempotent — safe to run on every deploy)
    _boot_conn = get_hana_connection()
    if _boot_conn:
        try:
            bootstrap_schema(_boot_conn)
        except Exception as e:
            print(f"[WARN] Bootstrap fallo (schema ya existe o sin permisos DDL): {e}")
        finally:
            _boot_conn.close()
    else:
        print("ℹ️  HANA no disponible al arrancar — bootstrap omitido.")

    INGEST_INTERVAL_S = int(os.environ.get("INGEST_INTERVAL_S", "120"))
    print(f"[TIME] Ingesta cada {INGEST_INTERVAL_S}s.")

    consecutive_errors = 0
    while True:
        try:
            main()
            consecutive_errors = 0
        except Exception as e:
            consecutive_errors += 1
            backoff = min(60, INGEST_INTERVAL_S * consecutive_errors)
            print(f"[ERROR] Error #{consecutive_errors}: {e}. Reintento en {backoff}s.")
            time.sleep(backoff)
            continue
        time.sleep(INGEST_INTERVAL_S)
