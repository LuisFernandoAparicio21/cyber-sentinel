# Plan: Pipeline Real — SAP Logs → Detección de Ataques

**Fecha:** 2026-04-25  
**Estado:** Pendiente ejecución  
**Objetivo:** Eliminar toda data sintética y conectar el pipeline a los logs SAP reales de `<sap-provided-soc-api-host>`

---

## Diagnóstico: Qué está mal hoy

### El problema

Hay DOS pipelines corriendo en paralelo que **nunca se hablan**:

```
PIPELINE A (01_ingest_api.py)         PIPELINE B (alert_loop.py)
─────────────────────────────         ─────────────────────────────
<sap-provided-soc-api-host>                    Templates hardcodeados
     ↓                                     ↓
SECURITY_API_LOGS (HANA)              DDoS_SYN, PortScan, HTTP_Flood...
     ↓                                     ↓
NUNCA SE USA                          MODEL_SCORES ← FAKE
                                           ↓
                                      Dashboard ← muestra datos inventados
```

### Los datos reales que llegan de la API

La API devuelve **44 campos por registro** en 10 tipos de log:

| Tipo | Cantidad | Descripción |
|------|----------|-------------|
| `INFO` | ~4% | Logs informativos generales |
| `WARNING` | ~2% | Advertencias de capacidad/recursos |
| `LLM_REQUEST` | ~2% | Llamadas exitosas a LLM |
| `ERROR` | ~1.4% | Errores de aplicación |
| `AUDIT` | ~1.3% | Intentos de acceso, cross-tenant |
| `DEBUG` | ~1% | Debugging de sesiones |
| `PERF` | ~0.6% | JVM heap, memoria |
| `SECURITY` | ~0.2% | IPs nuevas, anomalías de acceso |
| `LLM_ERROR` | ~0.3% | Fallos del LLM |
| `LLM_TIMEOUT` | ~0.1% | Timeouts del LLM |

**Campos clave disponibles:**
- `_id`, `@timestamp` — identidad del evento
- `client_ip` — IP real del cliente (disponible en logs no-LLM)
- `headers_http_request_method` — GET/POST/PUT/PATCH/DELETE
- `heathers_request_path` — ruta de la request
- `http_status_code` — 200/201/403/500/etc.
- `headers_http_host` — host destino (con región: `us-az.sap-services.internal`)
- `sap_function_application` — app SAP que generó el log
- `sap_function_log_type` — tipo de log
- `sap_source_type` — IDOC/REST/SOAP/BAPI/RFC/OData/BTP-Event
- `sap_app_env` — development/qa/production/sandbox
- `llm_response_time_ms` — latencia del LLM (solo logs LLM)
- `llm_total_tokens`, `llm_prompt_tokens`, `llm_completion_tokens`
- `llm_response_size_bytes`, `llm_cost_usd`
- `llm_finish_reason` — stop/content_filter/timeout/error
- `macro_region`, `region_code` — ubicación geográfica

---

## Mapeo: Logs SAP → 14 Features del Modelo DAR

El modelo DAR (`soc_attack_detection v1`) espera estas 14 features:

| Feature DAR | Campo fuente SAP | Lógica de derivación |
|-------------|-----------------|----------------------|
| `DURATION` | `llm_response_time_ms` | `ms / 1000.0` → segundos. Si vacío: `1.0` |
| `SRC_PORT` | `sap_source_type` | IDOC=8000, REST=443, SOAP=443, BAPI=3300, RFC=3300, OData=443, BTP-Event=8443 |
| `DST_PORT` | `heathers_request_path` | Extraer número de ruta. Si no: 443 (HTTPS) |
| `PROTOCOL` | `headers_http_host` | Siempre `TCP` (todo es HTTP/HTTPS) |
| `FLAG_SYN` | `http_status_code` | 1 si status en (401,403,408,429), señal de intento nuevo |
| `FLAG_ACK` | `http_status_code` | 1 si status 2xx (respuesta exitosa) |
| `FLAG_FIN` | `llm_finish_reason` | 1 si finish_reason = "stop". 0 en otro caso |
| `FLAG_RST` | `http_status_code` | 1 si status 5xx (error servidor = reset) |
| `TOTAL_FWD_PACKETS` | `llm_prompt_tokens` | tokens / 1500 (MTU bytes). Si vacío: 1 |
| `TOTAL_BWD_PACKETS` | `llm_completion_tokens` | tokens / 1500. Si vacío: 1 |
| `FLOW_BYTES_PER_S` | `llm_response_size_bytes` + `llm_response_time_ms` | bytes / (ms/1000). Si vacío: 1500 |
| `FLOW_PACKETS_PER_S` | `llm_total_tokens` + `llm_response_time_ms` | tokens / (ms/1000). Si vacío: 1.0 |
| `FWD_PKT_LEN_MEAN` | `llm_prompt_tokens` | tokens como proxy de bytes promedio por paquete |
| `BWD_PKT_LEN_MEAN` | `llm_completion_tokens` | idem para paquetes de respuesta |

**Señales de ataque en logs SAP:**
- `AUDIT` + `http_status_code=403` → cross-tenant access (FLAG_SYN=1, FLAG_RST=1)
- `LLM_TIMEOUT` + `llm_response_time_ms > 30000` → posible flood o abuso
- `ERROR` + `http_status_code=500` + `sap_function_log_type=ERROR` → data corruption
- `SECURITY` + nuevo IP range → anomalía de acceso
- `LLM_ERROR` + `llm_finish_reason=content_filter` → prompt injection attempt

---

## Pasos del Plan

---

### PASO 1 — Marcar y aislar datos sintéticos (NO borrar)

> ⚠️ Revisión Codex: el DELETE sin backup es irreversible y destruye continuidad forense.  
> Solución: marcar como sintéticos, no borrar. El DELETE solo si el equipo aprueba después de validar.

**Acción:** Ejecutar en HANA Explorer:

```sql
-- 1. Ver cuántos registros existen y de qué modelo vienen
SELECT MODEL_NAME, MODEL_VERSION, COUNT(*) AS CNT
FROM "SOC_LOGS"."MODEL_SCORES"
GROUP BY MODEL_NAME, MODEL_VERSION;

-- 2. Exportar snapshot antes de cualquier cambio
-- (En HANA Explorer: botón Export → CSV o JSON)
-- Guardar como: backup_model_scores_YYYY-MM-DD.csv

-- 3. Marcar registros sintéticos con un flag (NO borrar)
UPDATE "SOC_LOGS"."MODEL_SCORES"
SET "ALERT_STATUS" = 'SYNTHETIC_DEPRECATED'
WHERE "MODEL_NAME" = 'soc_attack_detection'
  AND "DETECTION_TIMESTAMP" < '2026-04-25';

-- 4. Verificar cuántos quedaron marcados
SELECT ALERT_STATUS, COUNT(*) FROM "SOC_LOGS"."MODEL_SCORES"
GROUP BY ALERT_STATUS;

-- 5. (OPCIONAL, solo con aprobación del equipo) Borrar SOLO los marcados
-- DELETE FROM "SOC_LOGS"."MODEL_SCORES"
-- WHERE "ALERT_STATUS" = 'SYNTHETIC_DEPRECATED';
```

**Rollback:** Si algo falla, los datos siguen intactos (solo se actualizó un campo de estado).  
**Responsable:** Gerardo (acceso HANA Explorer)  
**Tiempo estimado:** 10 minutos

---

### PASO 2 — Actualizar vista V_DASHBOARD_ALERTS (sin exponer RAW_FEATURES)

> ⚠️ Revisión Codex: RAW_FEATURES contiene metadata operacional sensible de SAP  
> (prompts LLM, rutas internas, tokens). No debe exponerse en el dashboard sin filtrar.  
> Solución: agregar FLOW_BYTES_PER_S y FLOW_PACKETS_PER_S (métricas numéricas, seguras),  
> pero RAW_FEATURES queda solo en MODEL_SCORES — acceso solo vía endpoint protegido.

```sql
CREATE OR REPLACE VIEW "SOC_LOGS"."V_DASHBOARD_ALERTS" AS
SELECT
    "EVENT_ID",
    "DETECTION_TIMESTAMP"   AS "TIMESTAMP",
    "INSERTION_DATE",
    "SRC_PORT",
    "DST_PORT",
    "PROTOCOL",
    "PREDICTION_LABEL",
    "CONFIDENCE",
    "SEVERITY",
    "MODEL_NAME",
    "MODEL_VERSION",
    "MTTD_SECONDS",
    "ALERT_STATUS",
    "FLOW_BYTES_PER_S",
    "FLOW_PACKETS_PER_S",
    "IS_ATTACK"
    -- RAW_FEATURES excluido intencionalmente: contiene metadata LLM sensible
FROM "SOC_LOGS"."MODEL_SCORES"
WHERE "DETECTION_TIMESTAMP" >= ADD_DAYS(CURRENT_DATE, -7)
  AND "ALERT_STATUS" != 'SYNTHETIC_DEPRECATED'
ORDER BY "DETECTION_TIMESTAMP" DESC;
```

**Campos nuevos que se agregan:** `FLOW_BYTES_PER_S`, `FLOW_PACKETS_PER_S`, `IS_ATTACK`  
**RAW_FEATURES:** Disponible solo via endpoint separado `/api/alerts/[id]/features` con autenticación  
**Responsable:** Gerardo  
**Tiempo estimado:** 5 minutos

---

### PASO 3 — Crear transformador SAP → Features de red

**Archivo nuevo:** `01_Cloud_Integration_Fernando/sap_feature_extractor.py`

```python
def sap_log_to_network_features(record: dict) -> dict:
    """
    Convierte un registro SAP real a las 14 features del modelo.
    Funciona ahora con reglas. Cuando ML esté listo, solo se swapea
    la función classify() en PASO 4 — este extractor no cambia.
    """
    method  = record.get("headers_http_request_method", "GET")
    status  = int(record.get("http_status_code") or 0)
    rtime   = float(record.get("llm_response_time_ms") or 1000)
    rbytes  = float(record.get("llm_response_size_bytes") or 1500)
    ptok    = float(record.get("llm_prompt_tokens") or 0)
    ctok    = float(record.get("llm_completion_tokens") or 0)
    ttok    = float(record.get("llm_total_tokens") or 0)

    src_port_map = {"IDOC": 8000, "REST": 443, "SOAP": 443,
                    "BAPI": 3300, "RFC": 3300, "OData": 443, "BTP-Event": 8443}

    return {
        "DURATION":           round(rtime / 1000.0, 3),
        "SRC_PORT":           src_port_map.get(record.get("sap_source_type", ""), 443),
        "DST_PORT":           443,
        "PROTOCOL":           "TCP",
        "FLAG_SYN":           1 if status in (401, 403, 408, 429) else 0,
        "FLAG_ACK":           1 if 200 <= status < 300 else 0,
        "FLAG_FIN":           1 if record.get("llm_finish_reason") == "stop" else 0,
        "FLAG_RST":           1 if status >= 500 else 0,
        "TOTAL_FWD_PACKETS":  max(1, int(ptok / 1500)),
        "TOTAL_BWD_PACKETS":  max(1, int(ctok / 1500)),
        "FLOW_BYTES_PER_S":   round(rbytes / (rtime / 1000.0), 2) if rtime > 0 else 1500,
        "FLOW_PACKETS_PER_S": round(ttok / (rtime / 1000.0), 2) if rtime > 0 else 1.0,
        "FWD_PKT_LEN_MEAN":   ptok or 64.0,
        "BWD_PKT_LEN_MEAN":   ctok or 64.0,
    }
```

**Responsable:** Fernando  
**Tiempo estimado:** 2 horas

---

### PASO 4 — Clasificador por reglas (placeholder ML)

> Esta es la lógica central del plan.  
> El pipeline corre completo con datos reales YA.  
> Cuando Carlos/Iker entreguen el modelo, se reemplaza `rule_based_classify()`  
> por `dar_infer.predict_one()` — una sola línea, sin tocar nada más.

**Archivo nuevo:** `01_Cloud_Integration_Fernando/sap_classifier.py`

```python
def rule_based_classify(record: dict, features: dict) -> dict:
    """
    Clasificador basado en reglas mientras el modelo ML no está listo.
    Retorna el mismo formato que DARInference.predict_one() para ser
    un drop-in replacement cuando llegue el modelo real.
    """
    log_type = record.get("sap_function_log_type", "")
    status   = int(record.get("http_status_code") or 0)
    rtime    = float(record.get("llm_response_time_ms") or 0)
    finish   = record.get("llm_finish_reason", "")

    # Señales de ataque claras
    if log_type == "AUDIT" and status == 403:
        return {"label": "ATTACK", "is_attack": True,  "confidence": 0.88}
    if log_type == "SECURITY":
        return {"label": "ATTACK", "is_attack": True,  "confidence": 0.75}
    if log_type == "LLM_TIMEOUT" and rtime > 30000:
        return {"label": "ATTACK", "is_attack": True,  "confidence": 0.72}
    if log_type == "ERROR" and status >= 500:
        return {"label": "ATTACK", "is_attack": True,  "confidence": 0.70}
    if finish == "content_filter":
        return {"label": "ATTACK", "is_attack": True,  "confidence": 0.80}
    if status in (401, 429):
        return {"label": "ATTACK", "is_attack": True,  "confidence": 0.65}

    # Todo lo demás: benigno
    return {"label": "BENIGN", "is_attack": False, "confidence": 0.95}


# ── Cuando ML esté listo, reemplazar esto: ────────────────────────
# from dar_inference import DARInference
# _dar = DARInference()
# def classify(record, features):
#     return _dar.predict_one(features, event_id=record['_id'])
# ──────────────────────────────────────────────────────────────────

def classify(record: dict, features: dict) -> dict:
    """Entry point único. Swap aquí cuando ML esté listo."""
    prediction = rule_based_classify(record, features)
    prediction["id"] = record.get("_id")
    return prediction
```

---

### PASO 5 — Integrar en el pipeline (dos etapas, idempotente)

**Arquitectura:**
```
ETAPA 1 — ya existe, NO tocar:
fetch_all_logs() → insert_to_hana() → SECURITY_API_LOGS  ← fuente de verdad

ETAPA 2 — nueva, idempotente:
SECURITY_API_LOGS → sap_feature_extractor → classify() → MODEL_SCORES
                    (deduplica por EVENT_ID — no procesa dos veces el mismo log)
```

**Cambio en `01_ingest_api.py`** — agregar al final de `main()`:

```python
from sap_feature_extractor import sap_log_to_network_features
from sap_classifier import classify

# IDs ya procesados — evita duplicados en reinicios
cursor = conn.cursor()
cursor.execute('SELECT "EVENT_ID" FROM "SOC_LOGS"."MODEL_SCORES"')
already_processed = {row[0] for row in cursor.fetchall()}

for _, row in df.iterrows():
    event_id = row.get("_id")
    if event_id in already_processed:
        continue

    features   = sap_log_to_network_features(row.to_dict())
    prediction = classify(row.to_dict(), features)

    if prediction["is_attack"]:
        alert_manager.handle_attack(prediction, features)
    else:
        alert_manager.handle_benign(prediction, features)
```

**Responsable:** Fernando  
**Tiempo estimado:** 1-2 horas

---

### PASO 6 — Apagar alert_loop.py

1. Quitar `alert_loop` del `manifest.yml` del CF ingestor
2. Marcar archivo como `DEPRECATED_alert_loop.py`
3. `cf push soc-api-ingestor`

**Responsable:** Fernando  
**Tiempo estimado:** 20 minutos

---

### PASO 7 — Actualizar dashboard

Campos nuevos disponibles en la vista:

| Campo | Vista | Seguro para UI |
|-------|-------|----------------|
| `FLOW_BYTES_PER_S` | L2 | ✅ |
| `FLOW_PACKETS_PER_S` | L2 | ✅ |
| `IS_ATTACK` | L1 | ✅ |
| `RAW_FEATURES` | L3 | ❌ Solo vía endpoint protegido |

**Archivo:** `src/lib/hana.ts` — agregar campos al SELECT  
**Responsable:** Gerardo  
**Tiempo estimado:** 1 hora

---

## Orden de ejecución

```
[ ] PASO 1 — Marcar sintéticos en HANA Explorer            (10 min)
[ ] PASO 2 — Actualizar V_DASHBOARD_ALERTS en HANA         (5 min)
[ ] PASO 3 — Escribir sap_feature_extractor.py             (2 h)
[ ] PASO 4 — Escribir sap_classifier.py (reglas + hook ML) (1 h)
[ ] PASO 5 — Integrar en 01_ingest_api.py                  (1-2 h)
[ ] PASO 6 — Apagar alert_loop.py + redeploy CF            (20 min)
[ ] PASO 7 — Actualizar dashboard (hana.ts)                (1 h)
```

**Tiempo total estimado:** 6-7 horas

---

## Cuando ML esté listo (Carlos/Iker)

Solo cambiar en `sap_classifier.py`:

```python
# ANTES (reglas):
def classify(record, features):
    return rule_based_classify(record, features)

# DESPUÉS (modelo real):
from dar_inference import DARInference
_dar = DARInference()
def classify(record, features):
    return _dar.predict_one(features, event_id=record.get("_id"))
```

**Un archivo. Cuatro líneas. Sin tocar nada más del pipeline.**

---

## Qué NO cambia

- Tabla `MODEL_SCORES` — schema intacto
- Tabla `SECURITY_API_LOGS` — persiste como fuente de verdad
- Tabla `SECURITY_DATA_FINAL` — datos de entrenamiento, no se toca
- Dashboard Next.js — solo se actualizan campos en `hana.ts`
- `alert_manager.py` y `hana_writer.py` — funcionan igual

---

## Riesgos

| Riesgo | Mitigación |
|--------|------------|
| HANA Free Tier se pausa | Ejecutar pasos 1-2 primero (solo HANA Explorer) |
| Logs sin `client_ip` (LLM logs) | SRC_PORT usa fallback por `sap_source_type` |
| Volume alto de logs (3,862 / 30 min) | Deduplica por EVENT_ID — no se procesa dos veces |
