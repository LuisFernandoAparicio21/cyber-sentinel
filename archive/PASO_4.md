# PASO 4: FORENSIC REPORTING — PLAN POR TAREAS

---

## 1. Estado Actual (as-is)

**Flujo existente:**
```
POST /predict (event) 
  → DARInference.predict_one() 
  → AlertManager.handle_attack() 
  → HANAWriter.write_model_score() → SOC_LOGS.MODEL_SCORES
  → webhook SAP ANS
```

**Tabla base:** `SOC_LOGS.MODEL_SCORES` (20 cols: SCORE_ID, DETECTION_TIMESTAMP, ALERT_SENT_TIMESTAMP, MTTD_SECONDS, SEVERITY, ALERT_STATUS, RAW_FEATURES, etc.)

**Endpoints actuales:** 8 (GET /health, POST /predict, POST /predict-batch, POST /api/alert/notify, GET /api/alerts/recent, GET /model-info, POST /train, GET /train/status)

**Sin PASO 4 aún:** No existe tabla INCIDENTS, no existe `/api/report/incident`, no existe IncidentReporter

**Dashboard:** Básico funcional en `04_Security_Visualisation_Gerardo/soc-dashboard` (consumo de V_ALERTS_24H)

---

## 2. Hallazgos Clave

- ✅ MODEL_SCORES funciona, tiene datos reales (SEVERITY=CRITICAL, MTTD=4.298s verificado)
- ✅ MTTD ya se mide correctamente
- ✅ RAW_FEATURES guardado como JSON — disponible para forensia
- ✅ Dashboard básico existe y consulta vistas HANA
- ❌ No existe tabla INCIDENTS
- ❌ No existe idempotency en POST /predict
- ❌ No existe MTTR (time-to-resolve)
- ❌ No existe correlación de múltiples scores → 1 incidente
- ❌ No existe `/api/report/incident` endpoint

---

## 3. Gaps para PASO 4

| Gap | Bloqueante | Acción |
|---|---|---|
| Tabla INCIDENTS no existe | SÍ | Crear DDL |
| IncidentReporter class no existe | SÍ | Crear módulo |
| /api/report/incident endpoint no existe | SÍ | Crear en dar_server.py |
| Sin idempotency_key en request | NO | Validar en IncidentReporter |
| Sin MTTR tracking | NO | Añadir columna + lógica |

---

## 4. Diseño Objetivo (to-be)

```
POST /api/report/incident (score_id, idempotency_key, reporter, ...)
  → IncidentReporter.create_report()
    ├─ validate_request()
    ├─ resolve_score_from_hana() [SELECT MODEL_SCORES]
    ├─ check_idempotency() [SELECT INCIDENTS WHERE IDEMPOTENCY_KEY]
    ├─ build_incident_record() [31 campos]
    ├─ compute_mttr() [resolved_at - detection_ts]
    ├─ build_timeline() [4 eventos]
    ├─ compute_hash() [SHA-256 integridad]
    └─ write_incident() [UPSERT INCIDENTS]
  → HTTP 201 {incident_id, severity, mttd_seconds, mttr_seconds, timeline, forensic_evidence}
```

---

## 5. Especificación API: POST /api/report/incident

### Request
```json
{
  "score_id": "6a3e346e-0989-488a-832c-81da25957bd9",
  "idempotency_key": "req-client-001",
  "correlation_id": "trace-xyz-789",
  "reporter": {
    "actor": "SOC_ANALYST_L1",
    "source": "manual" | "automated",
    "system": "soc-dashboard"
  },
  "resolution": {
    "action_taken": "BLOCKED_IP" | "RATE_LIMITED" | "MONITORED",
    "resolved_at": "2026-04-18T10:35:00Z",
    "notes": "IP bloqueada en firewall"
  },
  "enrichment": {
    "src_ip": "10.0.0.45",
    "dst_ip": "192.168.1.10",
    "asset_name": "prod-db-01",
    "mitre_technique": "T1498"
  }
}
```

**Requeridos:** `score_id`, `idempotency_key`, `reporter`  
**Opcionales:** `correlation_id`, `resolution`, `enrichment`

### Response (HTTP 201)
```json
{
  "incident_id": "inc-6a3e346e-0989",
  "score_id": "6a3e346e-0989-488a-832c-...",
  "status": "OPEN" | "RESOLVED",
  "report_summary": {
    "severity": "CRITICAL",
    "confidence": 0.985,
    "mttd_seconds": 4.298,
    "mttr_seconds": 296.0,
    "alert_status": "SENT"
  },
  "timeline": [
    { "event": "ATTACK_DETECTED", "ts": "2026-04-18T10:30:01Z" },
    { "event": "ALERT_SENT", "ts": "2026-04-18T10:30:05Z" },
    { "event": "REPORT_CREATED", "ts": "2026-04-18T10:30:10Z" },
    { "event": "RESOLVED", "ts": "2026-04-18T10:35:00Z" }
  ],
  "forensic_evidence": {
    "raw_features": { "SRC_PORT": 54321, "DST_PORT": 80, ... },
    "network_context": { "src_ip": "10.0.0.45", ... }
  },
  "remediation": ["SYN flood: rate-limit SYN packets", "Enable SYN cookies"],
  "report_hash": "sha256:abc123...",
  "created_at": "2026-04-18T10:30:10Z",
  "hana_status": "OK"
}
```

### Status codes
- **201** Created — nuevo reporte
- **200** OK — idempotente (mismo idempotency_key)
- **400** Bad Request — faltan campos / tipos incorrectos
- **404** Not Found — score_id no existe
- **500** Internal Server Error

---

## 6. Diseño de Datos HANA

### DDL: SOC_LOGS.INCIDENTS

```sql
CREATE COLUMN TABLE "SOC_LOGS"."INCIDENTS" (
    "INCIDENT_ID"            NVARCHAR(100)   NOT NULL PK,
    "IDEMPOTENCY_KEY"        NVARCHAR(200)   NOT NULL UNIQUE,
    "CORRELATION_ID"         NVARCHAR(200),
    "SCORE_ID"               NVARCHAR(100)   NOT NULL,  -- FK MODEL_SCORES

    "SEVERITY"               NVARCHAR(10)    NOT NULL,
    "CONFIDENCE"             DECIMAL(6,4)    NOT NULL,
    "PREDICTION_LABEL"       NVARCHAR(20)    NOT NULL,

    "DETECTION_TIMESTAMP"    TIMESTAMP       NOT NULL,
    "ALERT_SENT_TIMESTAMP"   TIMESTAMP,
    "REPORT_CREATED_AT"      TIMESTAMP       NOT NULL,
    "RESOLVED_AT"            TIMESTAMP,

    "MTTD_SECONDS"           DECIMAL(10,3),
    "MTTR_SECONDS"           DECIMAL(10,3),

    "STATUS"                 NVARCHAR(20)    NOT NULL DEFAULT 'OPEN',
    "ACTION_TAKEN"           NVARCHAR(50),
    "RESOLUTION_NOTES"       NCLOB,

    "REPORTER_ACTOR"         NVARCHAR(100)   NOT NULL,
    "REPORTER_SOURCE"        NVARCHAR(20)    NOT NULL,
    "REPORTER_SYSTEM"        NVARCHAR(100),

    "SRC_IP"                 NVARCHAR(50),
    "DST_IP"                 NVARCHAR(50),
    "SRC_PORT"               INTEGER,
    "DST_PORT"               INTEGER,
    "PROTOCOL"               NVARCHAR(10),
    "ASSET_NAME"             NVARCHAR(200),
    "MITRE_TECHNIQUE"        NVARCHAR(20),

    "REPORT_HASH"            NVARCHAR(64)    NOT NULL,
    "FULL_REPORT_JSON"       NCLOB,
    "INSERTION_DATE"         DATE            DEFAULT CURRENT_DATE
);

-- Índices
CREATE INDEX IDX_INCIDENTS_DETECTION_TS ON "SOC_LOGS"."INCIDENTS" ("DETECTION_TIMESTAMP" DESC);
CREATE INDEX IDX_INCIDENTS_SEVERITY_STATUS ON "SOC_LOGS"."INCIDENTS" ("SEVERITY", "STATUS");
CREATE INDEX IDX_INCIDENTS_SCORE_ID ON "SOC_LOGS"."INCIDENTS" ("SCORE_ID");

-- Vista
CREATE OR REPLACE VIEW "SOC_LOGS"."V_INCIDENTS_REPORT" AS
SELECT i.*, ms.ALERT_STATUS, ms.FLOW_BYTES_PER_S, ms.FLOW_PACKETS_PER_S
FROM "SOC_LOGS"."INCIDENTS" i
LEFT JOIN "SOC_LOGS"."V_MODEL_SCORES" ms ON i.SCORE_ID = ms.SCORE_ID
ORDER BY i.DETECTION_TIMESTAMP DESC;
```

---

## 7. Plan de Implementación por TAREAS

### TAREA 1: Crear DDL en HANA
- [ ] Crear archivo `03_Data_Architecture_Rene_Luis/06_incidents_table.sql`
- [ ] Copiar DDL de sección 6
- [ ] Ejecutar en HANA Studio
- [ ] Verificar: `SELECT COUNT(*) FROM "SOC_LOGS"."INCIDENTS"` → 0
- [ ] Verificar UNIQUE constraint: intentar INSERT duplicado → error
- **Duración:** 15 min  
- **Responsable:** Rene/Luis

### TAREA 2: Crear IncidentReporter class
- [ ] Crear archivo `02_Data_Science_Carlos_Iker/incident_reporter.py` (~200 líneas)
- [ ] Implementar clase `IncidentReporter` con métodos:
  - `create_report(request_dict) → Dict`
  - `_validate_request(req) → bool`
  - `_resolve_score(score_id) → Optional[Dict]`
  - `_check_idempotency(key) → Optional[str]`
  - `_build_incident_record(req, score) → Dict`
  - `_compute_mttr(detection_ts, resolved_at) → float|None`
  - `_build_timeline(score, resolved_at) → List[Dict]`
  - `_compute_hash(payload) → str`
  - `_get_writer() → HANAWriter`
- [ ] Usar HANAWriter para reads/writes
- **Duración:** 1.5 h  
- **Responsable:** Carlos/Iker

### TAREA 3: Extender HANAWriter
- [ ] Abrir `02_Data_Science_Carlos_Iker/hana_writer.py`
- [ ] Añadir 3 métodos al final:
  - `write_incident(record: Dict) → str` (UPSERT 31 campos)
  - `get_score_by_id(score_id: str) → Optional[Dict]` (SELECT MODEL_SCORES)
  - `get_incident_by_idempotency(key: str) → Optional[str]` (SELECT INCIDENTS)
- [ ] Sin cambios al constructor ni `write_model_score()`
- **Duración:** 30 min  
- **Responsable:** Carlos/Iker

### TAREA 4: Añadir endpoints a dar_server.py
- [ ] Abrir `02_Data_Science_Carlos_Iker/dar_server.py`
- [ ] Línea 30: Añadir `from incident_reporter import IncidentReporter`
- [ ] Línea 47: Añadir `_reporter = IncidentReporter()`
- [ ] Añadir endpoint POST /api/report/incident (~50 líneas)
- [ ] Añadir endpoint GET /api/report/incident/<incident_id> (~30 líneas)
- [ ] Actualizar startup logs con nuevos endpoints
- [ ] Sin cambios a endpoints existentes
- **Duración:** 45 min  
- **Responsable:** Carlos/Iker

### TAREA 5: Test unitario IncidentReporter
- [ ] Crear `02_Data_Science_Carlos_Iker/test_incident_paso4.py`
- [ ] Test 1: Create report (happy path) — score_id válido → HTTP 201
- [ ] Test 2: Idempotency — mismo idempotency_key 2 veces → 1 registro HANA
- [ ] Test 3: Score not found — score_id inválido → HTTP 404
- [ ] Test 4: Missing fields — sin score_id → HTTP 400
- [ ] Test 5: With resolution — resolved_at + action_taken → mttr_seconds calculado
- [ ] Test 6: HANA down — graceful degradation → hana_status: FAILED
- [ ] Test 7: Hash integrity — mismo payload → mismo hash
- [ ] Test 8: Regression — /predict sigue funcionando
- **Duración:** 1 h  
- **Responsable:** Carlos/Iker o QA

### TAREA 6: Validación en HANA
- [ ] Conectar a HANA y ejecutar:
  ```sql
  SELECT COUNT(*) FROM "SOC_LOGS"."INCIDENTS";  -- debe ser > 0 después test
  SELECT * FROM "SOC_LOGS"."V_INCIDENTS_REPORT" LIMIT 5;
  SELECT * FROM "SOC_LOGS"."INCIDENTS" WHERE IDEMPOTENCY_KEY='test-001';  -- verificar UNIQUE
  ```
- [ ] Verificar que SCORE_ID en INCIDENTS existe en MODEL_SCORES
- **Duración:** 15 min  
- **Responsable:** Rene/Luis

### TAREA 7: Documentación
- [ ] Crear `PASO4_FORENSIC_REPORT.md` con:
  - Resumen de qué se implementó
  - Endpoints nuevos + ejemplos curl
  - Schema INCIDENTS + índices
  - Limitaciones + próximos pasos
- [ ] Actualizar `CLAUDE.md` con nuevos comandos
- [ ] Actualizar `requirements.txt` si necesario (probablemente no)
- **Duración:** 30 min  
- **Responsable:** Manuel/PM

### TAREA 8: Regresión de PASO 3
- [ ] Ejecutar `test_notification_step3.py` — verificar webhook sigue funcionando
- [ ] Ejecutar `/health` — status OK
- [ ] Llamar POST `/predict` con ataque simulado — alert_id presente, MTTD medido
- [ ] Verificar nuevos registros en MODEL_SCORES
- [ ] **Resultado esperado:** Ningún cambio vs antes de PASO 4
- **Duración:** 15 min  
- **Responsable:** QA

### TAREA 9 (OPCIONAL): Integrar PASO 4 con dashboard
- [ ] En `04_Security_Visualisation_Gerardo/soc-dashboard`:
  - Añadir nueva vista "Forensic Reports" consultando V_INCIDENTS_REPORT
  - Mostrar: incident_id, severity, status, mttr_seconds, reporter_actor, resolution_notes
  - Añadir filtros por severity, status, date range
- [ ] Sin romper vistas existentes
- **Duración:** 1.5 h  
- **Responsable:** Gerardo (Security/Dashboard)
- **Nota:** Este paso es opcional para core PASO 4, pero recomendado para UX

---

## 8. Cambios por Archivo

| Archivo | Acción | Líneas | Responsabilidad |
|---|---|---|---|
| `06_incidents_table.sql` | **CREAR** | ~80 | DDL + índices + vista INCIDENTS |
| `incident_reporter.py` | **CREAR** | ~200 | Lógica forense (validate, resolve, build, hash, write) |
| `hana_writer.py` | **MODIFICAR** | +60 | 3 métodos: write_incident, get_score_by_id, get_incident_by_idempotency |
| `dar_server.py` | **MODIFICAR** | +80 | Import IncidentReporter, instancia _reporter, 2 endpoints nuevos |
| `test_incident_paso4.py` | **CREAR** | ~120 | 8 test cases |
| `PASO4_FORENSIC_REPORT.md` | **CREAR** | ~50 | Documentación |
| `soc-dashboard/*` (OPCIONAL) | **MODIFICAR** | TBD | Integración con V_INCIDENTS_REPORT (Gerardo) |

**NO TOCAR:** alert_manager.py, dar_inference.py, dar_client.py, dar_pipeline.py, hana_views, setup.py

---

## 9. Plan de Pruebas Detallado

### Test A: Create Report (happy path)
```bash
curl -X POST http://localhost:5000/api/report/incident \
  -H "Content-Type: application/json" \
  -d '{
    "score_id": "6a3e346e-0989-488a-832c-81da25957bd9",
    "idempotency_key": "test-001",
    "reporter": { "actor": "SOC_L1", "source": "manual", "system": "test" }
  }'
```
**Esperado:** HTTP 201, `incident_id` presente, `hana_status: OK`

### Test B: Idempotency
```bash
# Mismo request 2 veces → mismo incident_id
# Verificar: SELECT COUNT(*) FROM INCIDENTS WHERE IDEMPOTENCY_KEY='test-001' → 1
```

### Test C: Score not found
```bash
curl -X POST http://localhost:5000/api/report/incident \
  -d '{"score_id": "no-existe", "idempotency_key": "test-002", "reporter": {...}}'
# Esperado: HTTP 404, error: SCORE_NOT_FOUND
```

### Test D: Missing fields
```bash
# Sin score_id → HTTP 400, MISSING_REQUIRED_FIELD
# Sin idempotency_key → HTTP 400
# Sin reporter → HTTP 400
```

### Test E: With resolution (MTTR)
```bash
{
  "score_id": "6a3e346e-...",
  "idempotency_key": "test-003",
  "reporter": { "actor": "SOC_L2", "source": "manual", "system": "test" },
  "resolution": {
    "action_taken": "BLOCKED_IP",
    "resolved_at": "2026-04-18T10:35:00Z",
    "notes": "IP bloqueada"
  }
}
# Esperado: status=RESOLVED, mttr_seconds=296.0
```

### Test F: HANA down
```python
# Desconectar credenciales HANA
# Esperado: HTTP 201 con hana_status: FAILED (graceful degradation)
```

### Test G: Hash integrity
```python
# Mismo payload 2 veces → mismo report_hash (determinista)
# Cambiar 1 campo → hash diferente
```

### Test H: Regression PASO 3
```bash
# /predict, /api/alert/notify, /api/alerts/recent siguen igual
```

---

## 10. Riesgos y Mitigaciones

| Risk | Prob | Impact | Mitigation |
|---|---|---|---|
| score_id no existe en MODEL_SCORES | Alta | Alto | HTTP 404 en IncidentReporter._validate_request() |
| HANA DOWN al crear reporte | Media | Medio | Graceful degradation: `hana_status: FAILED`, generar incident_id en memoria |
| Duplicación por retry cliente | Media | Medio | UNIQUE constraint IDEMPOTENCY_KEY en HANA + check antes de INSERT |
| NCLOB muy grande | Baja | Bajo | Limitar notes a 2000 chars, no duplicar raw_features (ya en MODEL_SCORES) |
| SQL injection en notes/asset | Media | Alto | Usar parameterized queries `?` en HANA, sanitizar con strip() + max length |
| resolved_at en el futuro | Baja | Bajo | Validar `resolved_at <= now() + 60s`, else HTTP 400 |
| MTTR negativo (resolved < detection) | Baja | Bajo | Validar `resolved_at >= detection_ts` |
| Import error IncidentReporter rompe /predict | Media | Alto | Try/except en dar_server.py, fallback sin PASO 4 si import falla |

---

## 11. Criterios de Aceptación

### Funcionalidad
- [ ] POST /api/report/incident retorna HTTP 201 con `incident_id` UUID
- [ ] Mismo `idempotency_key` retorna HTTP 200 con el mismo `incident_id`
- [ ] `score_id` inexistente retorna HTTP 404
- [ ] `timeline` contiene eventos en orden cronológico
- [ ] `mttr_seconds` es null sin resolución, positivo con resolución
- [ ] `report_hash` es SHA-256 reproducible

### Persistencia
- [ ] `SELECT COUNT(*) FROM "SOC_LOGS"."INCIDENTS"` incrementa tras crear reporte
- [ ] IDEMPOTENCY_KEY tiene UNIQUE constraint (INSERT duplicado → error)
- [ ] `SCORE_ID` en INCIDENTS existe en MODEL_SCORES

### Compatibilidad
- [ ] GET /health → HTTP 200
- [ ] POST /predict detecta ataques, retorna `alert_id`
- [ ] GET /api/alerts/recent retorna registros
- [ ] Ningún endpoint existente retorna 500

### Seguridad
- [ ] Campos texto tienen longitud máxima validada
- [ ] Sin SQL injection en notes/enrichment
- [ ] `resolved_at` en futuro retorna HTTP 400

### Performance
- [ ] POST /api/report/incident responde < 3 seg (1 SELECT + 1 INSERT)
- [ ] Máximo 2 queries HANA por request

---

## 12. Supuestos y Bloqueantes

### Supuestos (marca si son correctos)

| ID | Supuesto | Impacto si falso |
|---|---|---|
| S1 | 1 score_id → máximo 1 incidente | Si quieres N incidentes/score: eliminar validación |
| S2 | HANA sigue en `<your-hana-host>.hanacloud.ondemand.com` | Cambiar HANA_HOST |
| S3 | MODEL_SCORES tendrá datos cuando pruebe (ya verif: 1 registro) | ✅ Confirmado |
| S4 | SAP ANS OAuth2 token sigue funcionando | Si expira: no afecta PASO 4, solo alertas |
| S5 | Sin AuthN requerida en `/api/report/incident` | Si necesario: añadir Bearer token check |

### Bloqueantes

| ID | Bloqueante | Dueño | Acción |
|---|---|---|---|
| B1 | Tabla INCIDENTS no existe en HANA | Rene/Luis | **TAREA 1**: Ejecutar DDL SQL |
| B2 | dar_key.json debe estar presente | Carlos/Iker | ✅ Verificado presente |

---

## Plan Recomendado (ejecutable en 1 día)

```
08:00–08:15  TAREA 1: Rene/Luis ejecutan DDL en HANA                    [15 min]
08:15–10:00  TAREA 2: Carlos/Iker crean incident_reporter.py            [1.5 h]
10:00–10:45  TAREA 3: Extender hana_writer.py (3 métodos)               [45 min]
10:45–11:30  TAREA 4: Añadir endpoints a dar_server.py                  [45 min]
11:30–12:30  TAREA 5: Crear y ejecutar test_incident_paso4.py           [1 h]
12:30–12:45  TAREA 6: Validar en HANA (queries de verificación)         [15 min]
12:45–13:15  TAREA 7: Documentación                                      [30 min]
13:15–13:30  TAREA 8: Regresión PASO 3                                   [15 min]

OPCIONAL (día 2):
14:00–15:30  TAREA 9: Gerardo integra dashboard (V_INCIDENTS_REPORT)    [1.5 h]
```

**Total core (sin dashboard): 5.5 horas**

---

## Estimación por Tarea

| Tarea | Duración |
|---|---|
| 1. DDL HANA | **S** (15 min) |
| 2. IncidentReporter | **M** (1.5 h) |
| 3. HANAWriter extendido | **S** (45 min) |
| 4. Endpoints | **S** (45 min) |
| 5. Tests | **M** (1 h) |
| 6. Validación HANA | **S** (15 min) |
| 7. Documentación | **S** (30 min) |
| 8. Regresión | **S** (15 min) |
| **TOTAL CORE** | **M (5.5h)** |
| 9. Dashboard integration (OPCIONAL) | **M** (1.5 h) |

---

## Trade-offs y Decisiones

**Decisión 1: Tabla INCIDENTS separada de MODEL_SCORES**
- **Ventaja:** Separación de concerns (predicciones vs incidentes). Incidentes tienen ciclo de vida propio (OPEN → RESOLVED).
- **Desventaja:** Requiere LEFT JOIN en vistas de reporting.
- **Recomendación:** Mantener separadas. Futuro: soportar N incidentes por score (sin cambios en DDL).

**Decisión 2: UNIQUE constraint en IDEMPOTENCY_KEY**
- **Ventaja:** Deduplicación garantizada a nivel DB. Cliente puede reintentar sin miedo.
- **Desventaja:** Si cliente cambia datos pero usa mismo idempotency_key, recibe 200 OK con el reporte original (no actualizado).
- **Recomendación:** Aceptable. Cliente debe usar nuevo idempotency_key si quiere crear reporte diferente.

**Decisión 3: Graceful degradation si HANA down**
- **Ventaja:** API no falla aunque DB falle. Retorna `hana_status: FAILED` pero genera incident_id e incident_id se puede guardar localmente por cliente.
- **Desventaja:** Datos desincronizados temporalmente.
- **Recomendación:** Mantener. Congruente con alert_manager que hace lo mismo.

---

**Plan entregado:** `PASO_4.md`  
**Próximo paso:** Ejecutar TAREA 1 (DDL en HANA) + TAREA 2 (IncidentReporter)  
**Dashboard integration:** Gerardo (opcional, TAREA 9)
