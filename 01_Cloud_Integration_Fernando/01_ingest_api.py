# ================================================
# 01_ingest_api.py — Fernando Aparicio
# Cloud Integration Engineer — SAP SOC API Ingest
# v5.0 — Idempotent loads: run control in HANA, transactional bulk UPSERT,
#        quarantine for bad rows, schema-drift counters, freshness in /health
# ================================================

import requests
import pandas as pd
import time
import os
import json
import threading
from collections import Counter
from datetime import datetime
import uuid
import hashlib
import hmac
from contextlib import contextmanager
import functools
from typing import Any, Dict, List, Optional, Tuple
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

INGEST_INTERVAL_S    = int(os.environ.get("INGEST_INTERVAL_S", "120"))
BATCH_SIZE           = int(os.environ.get("INGEST_BATCH_SIZE", "1000"))
UNMAPPED_ALERT_RATIO = float(os.environ.get("UNMAPPED_ALERT_RATIO", "0.05"))
RAW_RETENTION_FILES  = int(os.environ.get("RAW_RETENTION_FILES", "50"))

# In-memory view of the last cycle, exposed by /health
STATE: Dict[str, Any] = {"last_success_at": None, "last_window": None,
                         "last_status": None, "last_error": None, "last_checked_at": None}

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
# Regions seeded by bootstrap_schema(); the loader also reads SOC_REGIONS from HANA.
SEEDED_REGIONS = (
    ("EU-015",  "eu-central-1",   "Europe (Frankfurt)",      "EMEA"),
    ("US-010",  "us-east-1",      "US East (N. Virginia)",   "AMER"),
    ("US-030",  "us10",           "US West (SAP BTP US10)",  "AMER"),
    ("AP-020",  "ap-southeast-1", "Asia Pacific (Singapore)","APAC"),
    ("BR-010",  "sa-east-1",      "Brazil (São Paulo)",      "LATAM"),
    ("MX-010",  "mx-central-1",   "Mexico (UPAEP)",          "LATAM"),
    ("unknown", "unknown",        "Unknown Region",          "unknown"),
)
SEEDED_REGION_IDS = frozenset(r[0] for r in SEEDED_REGIONS)

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


_ALREADY_EXISTS_MARKERS = ("duplicate", "already exist")


def _ddl(cursor, sql: str):
    """Executes a DDL statement. Ignores only 'already exists' errors; anything
    else (permissions, syntax) is raised so a broken bootstrap is not hidden."""
    try:
        cursor.execute(sql)
    except Exception as e:
        if any(m in str(e).lower() for m in _ALREADY_EXISTS_MARKERS):
            return
        raise


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

    # ── Ingestion time (added in v5; idempotent on existing tables) ───────────
    _ddl(cur, 'ALTER TABLE "SOC_LOGS"."SOC_EVENTS" ADD ("INGESTED_AT" TIMESTAMP)')

    # ── Run control: one row per API window (watermark) ────────────────────────
    _ddl(cur, """
        CREATE COLUMN TABLE "SOC_LOGS"."INGEST_RUNS" (
            "WINDOW_START"     NVARCHAR(60)   NOT NULL,
            "STATUS"           NVARCHAR(10)   NOT NULL,
            "TOTAL_RECORDS"    INTEGER,
            "LOADED_ROWS"      INTEGER,
            "QUARANTINED_ROWS" INTEGER,
            "FAILED_PAGES"     NVARCHAR(1000),
            "UNMAPPED"         NVARCHAR(1000),
            "ERROR"            NVARCHAR(500),
            "STARTED_AT"       TIMESTAMP,
            "FINISHED_AT"      TIMESTAMP,
            PRIMARY KEY ("WINDOW_START")
        )
    """)

    # ── Quarantine (dead letter table) for rows that cannot be loaded ─────────
    _ddl(cur, """
        CREATE COLUMN TABLE "SOC_LOGS"."INGEST_QUARANTINE" (
            "RECORD_HASH"    NVARCHAR(64)  NOT NULL,
            "WINDOW_START"   NVARCHAR(60),
            "SOURCE"         NVARCHAR(20),
            "ERROR"          NVARCHAR(500),
            "RAW_PAYLOAD"    NCLOB,
            "QUARANTINED_AT" TIMESTAMP,
            PRIMARY KEY ("RECORD_HASH")
        )
    """)

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
    # every app id accepted by the normalizer must exist, or the FK rejects the row
    seeded_apps = {a for a, _ in apps}
    apps += [(a, a) for a in sorted(VALID_APP_IDS - seeded_apps)]
    for app_id, app_name in apps:
        cur.execute(
            'UPSERT "SOC_LOGS"."SOC_APPLICATIONS" ("APPLICATION_ID","APPLICATION_NAME") '
            'VALUES (?,?) WITH PRIMARY KEY', (app_id, app_name)
        )

    regions = list(SEEDED_REGIONS)
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
    seeded_services = {sid for sid, _ in services}
    services += [(sid, sid) for sid in sorted(VALID_SERVICE_IDS - seeded_services)]
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


_SOURCE_TYPE_ALIASES = {"rest": "REST", "odata": "OData", "bapi": "BAPI", "soap": "SOAP",
                        "rfc": "RFC", "idoc": "IDOC", "webhook": "BTP-Event", "btp-event": "BTP-Event"}


def _normalize_source_type(raw: str) -> str:
    if raw in VALID_SOURCE_TYPES:
        return raw
    return _SOURCE_TYPE_ALIASES.get((raw or "").lower(), "REST")


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


# ── Row building (pure, no database) ──────────────────────────────────────────
def content_hash(record: Dict[str, Any]) -> str:
    """SHA-256 of the record's canonical JSON (sorted keys, empty values dropped).
    The same event gets the same hash regardless of key order or batch shape."""
    clean = {k: v for k, v in record.items() if _coerce(v) is not None}
    canonical = json.dumps(clean, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _to_utc_naive(raw) -> Optional[datetime]:
    """Parses a timestamp to naive UTC (HANA TIMESTAMP has no time zone)."""
    if _coerce(raw) is None:
        return None
    try:
        ts = pd.to_datetime(raw, utc=True)
    except (ValueError, TypeError, OverflowError):
        return None
    if pd.isna(ts):
        return None
    return ts.tz_convert(None).to_pydatetime()


def _to_int(val) -> int:
    return int(float(val))


def _to_number(val, cast, field: str, unmapped: Counter):
    """Casts a numeric field; values that are not numbers become NULL and are counted."""
    val = _coerce(val)
    if val is None:
        return None
    try:
        return cast(val)
    except (ValueError, TypeError, OverflowError):
        unmapped[field] += 1
        return None


def build_rows(record: Dict[str, Any], ingested_at: datetime,
               known_regions: frozenset, unmapped: Counter) -> Dict[str, Any]:
    """Normalizes one raw event into the rows for SOC_EVENTS, SOC_LLM_REQUESTS and
    MODEL_PREDICTIONS. Values outside the catalogs fall back to a safe default and
    are counted in `unmapped`, so schema drift in the source becomes visible."""
    if not isinstance(record, dict):
        raise TypeError(f"expected a JSON object, got {type(record).__name__}")

    chash    = content_hash(record)
    event_id = str(_first_of(record, "_id", "id", "EVENT_ID") or chash[:32])[:36]

    ts = _to_utc_naive(_first_of(record, "@timestamp", "timestamp", "TIMESTAMP"))
    if ts is None:
        unmapped["timestamp"] += 1
        ts = ingested_at

    raw_log_type = str(_first_of(record, "sap_function_log_type", "category",
                                 "LOG_TYPE", "log_type", default="INFO"))
    if raw_log_type.upper() not in VALID_LOG_TYPES:
        unmapped["log_type"] += 1
    log_type = _normalize_log_type(raw_log_type)

    raw_source = str(_first_of(record, "sap_source_type", "source", "SOURCE_TYPE", default="REST"))
    if raw_source not in VALID_SOURCE_TYPES and raw_source.lower() not in _SOURCE_TYPE_ALIASES:
        unmapped["source_type"] += 1
    source_type = _normalize_source_type(raw_source)

    raw_env = str(_first_of(record, "sap_app_env", "APP_ENV", default="production"))
    if raw_env not in VALID_APP_ENVS:
        unmapped["app_env"] += 1
    app_env = _normalize_app_env(raw_env)

    raw_app = str(_first_of(record, "sap_function_application", "APPLICATION_ID", default="unknown"))
    if raw_app not in VALID_APP_IDS:
        unmapped["application_id"] += 1
    app_id = _normalize_app_id(raw_app)

    raw_service = str(_first_of(record, "service_id", "SERVICE_ID", default="unknown") or "unknown")
    if raw_service not in VALID_SERVICE_IDS:
        unmapped["service_id"] += 1
    service_id = _normalize_service_id(raw_service)

    raw_region = _normalize_region_id(str(_first_of(record, "region_id", "REGION_ID", default="unknown")))
    if raw_region in known_regions:
        region_id = raw_region
    else:
        unmapped["region_id"] += 1
        region_id = "unknown"

    http_status = _to_number(record.get("http_status"), _to_int, "http_status", unmapped)

    event = (
        event_id, chash, ts, ts, log_type,
        str(_first_of(record, "sap_function_message", "payload", "message", default="") or "")[:1000],
        http_status,
        (str(_first_of(record, "client_ip", "CLIENT_IP", default="") or "")[:45] or None),
        (str(_first_of(record, "headers_http_request_method", default="") or "")[:10] or None),
        (str(_first_of(record, "heathers_request_path", "request_path", default="") or "")[:500] or None),
        (str(_first_of(record, "headers_content_type", default="") or "")[:100] or None),
        (str(_first_of(record, "headers_http_host", default="") or "")[:200] or None),
        source_type, app_env,
        str(_first_of(record, "event_code_version", "CODE_VERSION", default="1.0") or "1.0")[:20],
        str(_first_of(record, "index_name", "INDEX_NAME", default="_api") or "_api")[:50],
        app_id, region_id, service_id, ingested_at,
    )

    llm = None
    model_id = str(_first_of(record, "llm_model_id", "LLM_MODEL", default="") or "")
    if model_id:
        provider = (model_id.split("-")[0] if "-" in model_id
                    else model_id.split("/")[0] if "/" in model_id else "unknown")
        finish_reason = str(_first_of(record, "llm_finish_reason", default="") or "")
        llm = (
            event_id, provider[:30], model_id[:50], _normalize_llm_status(finish_reason),
            (finish_reason[:20] or None),
            _to_number(record.get("llm_prompt_tokens"), _to_int, "llm_prompt_tokens", unmapped) or 0,
            _to_number(record.get("llm_completion_tokens"), _to_int, "llm_completion_tokens", unmapped) or 0,
            _to_number(record.get("llm_total_tokens"), _to_int, "llm_total_tokens", unmapped) or 0,
            _to_number(record.get("sap_llm_response_size"), _to_int, "sap_llm_response_size", unmapped) or 0,
            _to_number(record.get("llm_temperature"), float, "llm_temperature", unmapped) or 0.0,
        )

    features = {
        "llm_response_time_ms": _to_number(record.get("llm_response_time_ms"), float,
                                           "llm_response_time_ms", unmapped) or 0,
        "http_status_code":     http_status or 0,
    }
    p = classify(dict(record), features)
    severity = "CRITICAL" if p["confidence"] >= 0.85 else "HIGH" if p["confidence"] >= 0.70 else "MEDIUM"
    pred = (event_id, p["label"][:20], round(float(p["confidence"]), 2),
            (severity if p["is_attack"] else None), (p.get("reason", "")[:500] or None))

    return {"event_id": event_id, "hash": chash, "event": event, "llm": llm, "pred": pred, "raw": record}


# ── Loading (one transaction per batch) ───────────────────────────────────────
_UPSERT_EVENT = (
    'UPSERT "SOC_LOGS"."SOC_EVENTS" '
    '("EVENT_ID","EVENT_HASH","EVENT_TIMESTAMP","EVENT_REQUESTED","LOG_TYPE",'
    '"LOG_MESSAGE","HTTP_STATUS","CLIENT_IP","REQUEST_METHOD","REQUEST_PATH",'
    '"CONTENT_TYPE","HTTP_HOST","SOURCE_TYPE","APP_ENV","CODE_VERSION",'
    '"INDEX_NAME","APPLICATION_ID","REGION_ID","SERVICE_ID","INGESTED_AT") '
    'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) WITH PRIMARY KEY'
)
_UPSERT_LLM = (
    'UPSERT "SOC_LOGS"."SOC_LLM_REQUESTS" '
    '("EVENT_ID","LLM_PROVIDER","LLM_MODEL_ID","LLM_STATUS","LLM_FINISH_REASON",'
    '"PROMPT_TOKENS","COMPLETION_TOKENS","TOTAL_TOKENS","RESPONSE_SIZE_BYTES","TEMPERATURE") '
    'VALUES (?,?,?,?,?,?,?,?,?,?) WITH PRIMARY KEY'
)
_UPSERT_PRED = (
    'UPSERT "SOC_LOGS"."MODEL_PREDICTIONS" '
    '("EVENT_ID","PREDICTION_LABEL","CONFIDENCE","SEVERITY","REASON") '
    'VALUES (?,?,?,?,?) WITH PRIMARY KEY'
)
_UPSERT_QUARANTINE = (
    'UPSERT "SOC_LOGS"."INGEST_QUARANTINE" '
    '("RECORD_HASH","WINDOW_START","SOURCE","ERROR","RAW_PAYLOAD","QUARANTINED_AT") '
    'VALUES (?,?,?,?,?,?) WITH PRIMARY KEY'
)


def _known_regions(conn) -> frozenset:
    """Region ids present in SOC_REGIONS (FK target); falls back to the seeded set."""
    cur = conn.cursor()
    try:
        cur.execute('SELECT "REGION_ID" FROM "SOC_LOGS"."SOC_REGIONS"')
        ids = frozenset(r[0] for r in cur.fetchall())
        return ids or SEEDED_REGION_IDS
    except Exception:
        return SEEDED_REGION_IDS
    finally:
        cur.close()


def _quarantine_row(record: Any, window_start: str, source: str, error: str, at: datetime) -> tuple:
    try:
        rhash = content_hash(record) if isinstance(record, dict) else \
            hashlib.sha256(repr(record).encode("utf-8")).hexdigest()
        payload = json.dumps(record, default=str)
    except Exception:
        rhash, payload = uuid.uuid4().hex, repr(record)
    return (rhash, str(window_start)[:60], source[:20], str(error)[:500], payload[:1_000_000], at)


def _executemany_chunks(cursor, sql: str, rows: List[tuple]):
    for i in range(0, len(rows), BATCH_SIZE):
        cursor.executemany(sql, rows[i:i + BATCH_SIZE])


def _report_unmapped(unmapped: Counter, total: int):
    for field, n in sorted(unmapped.items()):
        ratio = n / total if total else 0.0
        tag = "⚠️  SCHEMA DRIFT" if ratio >= UNMAPPED_ALERT_RATIO else "ℹ️  unmapped"
        print(f"{tag} · {field}: {n}/{total} values outside the catalog ({ratio:.1%})")


def load_records(conn, records: List[Any], window_start: str, source: str) -> Dict[str, Any]:
    """Loads a batch in ONE transaction with bulk UPSERTs (idempotent by primary key).

    Rows that cannot be normalized go to INGEST_QUARANTINE. If the database
    rejects the bulk load, the batch is rolled back and retried event by event,
    so a single bad row is quarantined instead of blocking the whole window."""
    ingested_at   = datetime.utcnow()
    known_regions = _known_regions(conn)
    unmapped: Counter = Counter()
    prepared: List[Dict[str, Any]] = []
    quarantine: List[tuple] = []

    for rec in records:
        try:
            prepared.append(build_rows(rec, ingested_at, known_regions, unmapped))
        except Exception as e:
            quarantine.append(_quarantine_row(rec, window_start, source, f"normalize: {e}", ingested_at))

    conn.setautocommit(False)
    try:
        cur = conn.cursor()
        try:
            try:
                _executemany_chunks(cur, _UPSERT_EVENT, [p["event"] for p in prepared])
                _executemany_chunks(cur, _UPSERT_LLM, [p["llm"] for p in prepared if p["llm"]])
                _executemany_chunks(cur, _UPSERT_PRED, [p["pred"] for p in prepared])
                _executemany_chunks(cur, _UPSERT_QUARANTINE, quarantine)
                conn.commit()
                loaded = len(prepared)
            except Exception as bulk_err:
                conn.rollback()
                print(f"⚠️  Bulk load rejected ({bulk_err}). Retrying event by event...")
                loaded = 0
                for p in prepared:
                    try:
                        cur.execute(_UPSERT_EVENT, p["event"])
                        if p["llm"]:
                            cur.execute(_UPSERT_LLM, p["llm"])
                        cur.execute(_UPSERT_PRED, p["pred"])
                        conn.commit()
                        loaded += 1
                    except Exception as row_err:
                        conn.rollback()
                        quarantine.append(_quarantine_row(p["raw"], window_start, source,
                                                          f"database: {row_err}", ingested_at))
                _executemany_chunks(cur, _UPSERT_QUARANTINE, quarantine)
                conn.commit()
        finally:
            cur.close()
    finally:
        conn.setautocommit(True)

    _report_unmapped(unmapped, len(records))
    print(f"✅ {loaded:,} loaded · {len(quarantine):,} quarantined · {source} {window_start}")
    return {"loaded": loaded, "quarantined": len(quarantine), "unmapped": dict(unmapped)}


# ── Run control (watermark per API window) ─────────────────────────────────────
def get_run_status(conn, window_start: str) -> Optional[str]:
    cur = conn.cursor()
    try:
        cur.execute('SELECT "STATUS" FROM "SOC_LOGS"."INGEST_RUNS" WHERE "WINDOW_START" = ?',
                    (str(window_start)[:60],))
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        cur.close()


def save_run(conn, window_start: str, status: str, started_at: datetime, total: int = 0,
             loaded: int = 0, quarantined: int = 0, failed_pages: Optional[List[int]] = None,
             unmapped: Optional[Dict[str, int]] = None, error: Any = None):
    cur = conn.cursor()
    try:
        cur.execute(
            'UPSERT "SOC_LOGS"."INGEST_RUNS" ("WINDOW_START","STATUS","TOTAL_RECORDS","LOADED_ROWS",'
            '"QUARANTINED_ROWS","FAILED_PAGES","UNMAPPED","ERROR","STARTED_AT","FINISHED_AT") '
            'VALUES (?,?,?,?,?,?,?,?,?,?) WITH PRIMARY KEY',
            (str(window_start)[:60], status, int(total), int(loaded), int(quarantined),
             json.dumps(failed_pages or [])[:1000], json.dumps(unmapped or {})[:1000],
             (str(error)[:500] if error else None), started_at, datetime.utcnow()),
        )
        conn.commit()
    finally:
        cur.close()


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
    """Liveness for the CF health check (always 200) plus ingestion freshness."""
    last = STATE["last_success_at"]
    lag  = (datetime.utcnow() - last).total_seconds() if last else None
    return jsonify({
        "status": "ok", "service": "soc-api-ingestor", "version": "5.0",
        "ingestion": {
            "last_success_at": (last.isoformat() + "Z") if last else None,
            "last_window":     STATE["last_window"],
            "last_status":     STATE["last_status"],
            "lag_seconds":     round(lag) if lag is not None else None,
            "stale":           lag is None or lag > 3 * INGEST_INTERVAL_S,
            "last_error":      STATE["last_error"],
            "last_checked_at": STATE["last_checked_at"],
        },
    })


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
    if not isinstance(events, list) or not events:
        return jsonify({"received": 0, "status": "empty_payload"}), 200
    try:
        with hana_cursor() as (conn, _):
            result = load_records(conn, events, f"webhook:{datetime.utcnow().isoformat(timespec='seconds')}", "webhook")
        return jsonify({"received": len(events), **result, "status": "loaded"}), 200
    except Exception as e:
        print(f"Webhook error: {e}")
        return jsonify({"error": "Load failed; see service logs"}), 500


@app.route("/api/replenish", methods=["POST"])
@require_api_key
def replenish_data():
    """Bulk data replenishment for backfill/reprocessing."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Expected JSON with 'records' array"}), 400

    records = payload.get("records", [])
    if not isinstance(records, list) or not records:
        return jsonify({"error": "No records provided"}), 400

    try:
        name = f"replenish:{datetime.utcnow().isoformat(timespec='seconds')}"
        backup = save_raw_batch(records, name)
        with hana_cursor() as (conn, _):
            result = load_records(conn, records, name, "replenish")
        return jsonify({**result, "backup_saved": backup is not None, "status": "ok"}), 200
    except Exception as e:
        print(f"Replenish error: {e}")
        return jsonify({"error": "Load failed; see service logs"}), 500


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


# ── Ingestion cycle ────────────────────────────────────────────────────────────
def check_server_alive() -> bool:
    try:
        r     = requests.get(f"{API_BASE}/health", timeout=10)
        alive = r.status_code == 200 and r.json().get("status") == "ok"
        print("✅ Servidor vivo." if alive else f"⚠️ Respuesta inesperada: {r.status_code}")
        return alive
    except requests.exceptions.RequestException as e:
        print(f"❌ Sin conexión: {e}")
        return False


_RETRY_BACKOFF = [10, 30, 60]  # long waits so the API does not rate-limit us


def _fetch_page(page: int) -> Optional[dict]:
    """Fetches one page from /logs/current with backoff. Returns None after the last retry."""
    url = f"{API_BASE}/logs/current"
    for attempt, wait in enumerate([0] + _RETRY_BACKOFF, start=1):
        if wait:
            time.sleep(wait)
        try:
            r = requests.get(url, headers=HEADERS_AUTH, params={"page": page}, timeout=30)
            r.raise_for_status()
            data = r.json()
            if attempt > 1:
                print(f"   ✅ Página {page} OK en intento {attempt}.")
            return data
        except (requests.exceptions.HTTPError,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as e:
            print(f"   ⚠️  Página {page} — intento {attempt}/{len(_RETRY_BACKOFF) + 1} — HTTP error: {e}")
        except ValueError as e:
            print(f"   ⚠️  Página {page} — intento {attempt}/{len(_RETRY_BACKOFF) + 1} — JSON inválido: {e}")

    print(f"   ❌ Página {page} descartada tras {len(_RETRY_BACKOFF) + 1} intentos.")
    return None


def _fetch_remaining(first: dict) -> Tuple[List[dict], List[int]]:
    """Downloads pages 2..N of the window. Returns (records, failed_pages)."""
    total_pages = int(first["total_pages"])
    records: List[dict] = list(first["data"])
    failed_pages: List[int] = []
    print(f"🖥️ Ventana: {first['window_start']} → {first.get('window_end')}  "
          f"({first.get('total_records', len(records)):,} registros, {total_pages} páginas)")

    for page in range(2, total_pages + 1):
        result = _fetch_page(page)
        if result is not None:
            records.extend(result["data"])
        else:
            failed_pages.append(page)
        time.sleep(1.5)  # be gentle with the API
        if page % 10 == 0:
            print(f"   ⏳ {page}/{total_pages} páginas procesadas...")

    if failed_pages:
        print(f"⚠️  Páginas con fallo: {failed_pages} — la ventana queda PARTIAL y se reintenta.")
    return records, failed_pages


def save_raw_batch(records: List[Any], name: str) -> Optional[str]:
    """Best-effort local Parquet copy of a raw batch. The container disk is
    ephemeral and HANA is the source of truth, so only the newest
    RAW_RETENTION_FILES copies are kept."""
    if not records:
        return None
    clean = "".join(c if c.isalnum() or c in "-_" else "-" for c in str(name))[:60]
    path  = os.path.join(OUTPUT_DIR, f"raw_{clean}.parquet")
    try:
        pd.DataFrame(records).to_parquet(path, index=False)
    except Exception as e:
        print(f"⚠️  Copia local no guardada ({e}).")
        return None
    _apply_retention()
    return path


def _apply_retention():
    files = sorted((os.path.join(OUTPUT_DIR, f) for f in os.listdir(OUTPUT_DIR) if f.endswith(".parquet")),
                   key=os.path.getmtime)
    for old in files[:-RAW_RETENTION_FILES] if RAW_RETENTION_FILES > 0 else files:
        try:
            os.remove(old)
        except OSError:
            pass


def main():
    """One ingestion cycle.

    A window is marked LOADED in INGEST_RUNS only after its rows are committed.
    PARTIAL (pages failed) and FAILED windows are downloaded again while the API
    still serves them; UPSERT by primary key keeps the reload idempotent."""
    STATE["last_checked_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    if not check_server_alive():
        print("🛑 Servidor no responde — abortando ciclo.")
        return

    first = _fetch_page(1)
    if first is None:
        print("❌ No se pudo descargar la página 1 — abortando ciclo.")
        return
    window_start = str(first["window_start"])

    conn = get_hana_connection()
    if conn is None:
        records, _ = _fetch_remaining(first)
        save_raw_batch(records, window_start)
        print("ℹ️  HANA no configurado: solo copia local; la ventana no se marca.")
        return

    try:
        if get_run_status(conn, window_start) == "LOADED":
            print(f"⏭️  Ventana {window_start} ya cargada — saltando.")
            return

        started_at = datetime.utcnow()
        records, failed_pages = _fetch_remaining(first)
        save_raw_batch(records, window_start)

        try:
            result = load_records(conn, records, window_start, "api")
        except Exception as e:
            print(f"❌ Carga fallida para {window_start}: {e}. Se reintenta en el siguiente ciclo.")
            STATE.update(last_status="FAILED", last_error=str(e)[:300])
            try:
                save_run(conn, window_start, "FAILED", started_at, total=len(records),
                         failed_pages=failed_pages, error=e)
            except Exception as run_err:
                print(f"⚠️  No se pudo registrar el fallo: {run_err}")
            return

        status = "PARTIAL" if failed_pages else "LOADED"
        save_run(conn, window_start, status, started_at,
                 total=int(first.get("total_records", len(records))),
                 loaded=result["loaded"], quarantined=result["quarantined"],
                 failed_pages=failed_pages, unmapped=result["unmapped"])
        STATE.update(last_success_at=datetime.utcnow(), last_window=window_start,
                     last_status=status, last_error=None)
        print(f"[OK] Ventana {window_start}: {status}.")
    finally:
        conn.close()


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))

    flask_thread = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False),
        daemon=True,
    )
    flask_thread.start()
    print(f"Flask activo en :{port}")
    print("   GET  /health  |  GET  /api/logs  |  GET  /api/stats")
    print("   POST /api/webhook  |  POST /api/replenish")

    time.sleep(2)

    # Bootstrap schema once at startup (idempotent — safe to run on every deploy)
    _boot_conn = get_hana_connection()
    if _boot_conn:
        try:
            bootstrap_schema(_boot_conn)
        except Exception as e:
            print(f"[WARN] Bootstrap fallo: {e}")
        finally:
            _boot_conn.close()
    else:
        print("ℹ️  HANA no disponible al arrancar — bootstrap omitido.")

    print(f"[TIME] Ingesta cada {INGEST_INTERVAL_S}s.")

    consecutive_errors = 0
    while True:
        try:
            main()
            consecutive_errors = 0
        except Exception as e:
            consecutive_errors += 1
            STATE["last_error"] = str(e)[:300]
            backoff = min(60, INGEST_INTERVAL_S * consecutive_errors)
            print(f"[ERROR] Error #{consecutive_errors}: {e}. Reintento en {backoff}s.")
            time.sleep(backoff)
            continue
        time.sleep(INGEST_INTERVAL_S)
