# PASO 4: FORENSIC REPORTING — REPORTE DE IMPLEMENTACIÓN

**Fecha:** 2026-04-19
**Estado:** ✅ COMPLETADO
**Responsable:** Carlos/Iker + Rene/Luis

---

## 1. Resumen Ejecutivo

Se implementó el sistema de **reporting forense de incidentes** sobre la infraestructura existente de detección de ataques (PASO 3). El sistema permite a analistas SOC generar reportes forenses completos de incidentes detectados, con trazabilidad temporal (MTTD/MTTR), evidencia forense, remediation steps y garantía de integridad (SHA-256).

**Endpoints nuevos:**
- `POST /api/report/incident` — Crea reporte forense
- `GET /api/report/incident/<incident_id>` — Consulta reporte (stub)

**Tablas HANA nuevas:**
- `SOC_LOGS.INCIDENTS` (31 columnas, 3 índices)
- `SOC_LOGS.V_INCIDENTS_REPORT` (vista con join a MODEL_SCORES)

---

## 2. Arquitectura Implementada

```
POST /api/report/incident
  → IncidentReporter.create_report()
    ├─ _validate_request()          [campos obligatorios]
    ├─ _resolve_score()              [SELECT MODEL_SCORES]
    ├─ _check_idempotency()          [SELECT INCIDENTS WHERE IDEMPOTENCY_KEY]
    ├─ _build_timeline()             [4 eventos cronológicos]
    ├─ _build_forensic_evidence()    [model_info + network_context + enrichment]
    ├─ _build_remediation()          [basado en severity + SYN flood detection]
    ├─ SHA-256 hash                  [integridad del payload]
    └─ HANAWriter.write_incident()  [UPSERT INCIDENTS]
  → HTTP 201 {incident_id, severity, mttd_seconds, mttr_seconds, timeline, forensic_evidence, remediation, report_hash, hana_status}
```

---

## 3. Archivos Creados/Modificados

| Archivo | Acción | Líneas |
|---|---|---|
| `03_Data_Architecture_Rene_Luis/06_incidents_table.sql` | CREADO | 173 |
| `02_Data_Science_Carlos_Iker/incident_reporter.py` | CREADO | 402 |
| `02_Data_Science_Carlos_Iker/hana_writer.py` | MODIFICADO | +130 |
| `02_Data_Science_Carlos_Iker/dar_server.py` | MODIFICADO | +80 |
| `02_Data_Science_Carlos_Iker/test_incident_paso4.py` | CREADO | 238 |
| `PASO4_FORENSIC_REPORT.md` | CREADO | (este archivo) |

---

## 4. Schema HANA: SOC_LOGS.INCIDENTS

### 31 columnas organizadas por categoría:

**Identificación (4):** INCIDENT_ID, IDEMPOTENCY_KEY (UNIQUE), CORRELATION_ID, SCORE_ID
**Modelo (5):** SEVERITY, CONFIDENCE, PREDICTION_LABEL, MODEL_NAME, MODEL_VERSION
**Cadena temporal (4):** DETECTION_TIMESTAMP, ALERT_SENT_TIMESTAMP, REPORT_CREATED_AT, RESOLVED_AT
**Métricas (2):** MTTD_SECONDS, MTTR_SECONDS
**Estado/resolución (3):** STATUS (OPEN/RESOLVED/ESCALATED/FALSE_POSITIVE), ACTION_TAKEN, RESOLUTION_NOTES
**Actor/auditoría (3):** REPORTER_ACTOR, REPORTER_SOURCE, REPORTER_SYSTEM
**Enriquecimiento (7):** SRC_IP, DST_IP, SRC_PORT, DST_PORT, PROTOCOL, ASSET_NAME, MITRE_TECHNIQUE
**Integridad (2):** REPORT_HASH (SHA-256), FULL_REPORT_JSON
**Audit (1):** INSERTION_DATE

### Índices:
- `IDX_INCIDENTS_DETECTION_TS` ON (DETECTION_TIMESTAMP DESC)
- `IDX_INCIDENTS_SEVERITY_STATUS` ON (SEVERITY, STATUS)
- `IDX_INCIDENTS_SCORE_ID` ON (SCORE_ID)

---

## 5. Ejemplos de Uso (curl)

### Crear reporte (happy path)
```bash
curl -X POST http://localhost:5000/api/report/incident \
  -H "Content-Type: application/json" \
  -d '{
    "score_id": "6a3e346e-0989-488a-832c-81da25957bd9",
    "idempotency_key": "req-client-001",
    "reporter": {
      "actor": "SOC_ANALYST_L1",
      "source": "manual",
      "system": "soc-dashboard"
    }
  }'
```

### Crear reporte con resolución (MTTR calculado)
```bash
curl -X POST http://localhost:5000/api/report/incident \
  -H "Content-Type: application/json" \
  -d '{
    "score_id": "6a3e346e-0989-488a-832c-81da25957bd9",
    "idempotency_key": "req-client-002",
    "reporter": {"actor": "SOC_L2", "source": "manual"},
    "resolution": {
      "action_taken": "BLOCKED_IP",
      "resolved_at": "2026-04-19T10:35:00Z",
      "notes": "IP bloqueada en firewall perimetral"
    },
    "enrichment": {
      "src_ip": "10.0.0.45",
      "asset_name": "prod-db-01",
      "mitre_technique": "T1498"
    }
  }'
```

---

## 6. Testing

### Tests integrados (test_incident_paso4.py)
```bash
cd "02_Data_Science_Carlos_Iker"
python test_incident_paso4.py
```

**Resultado esperado:**
```
TEST 1: Create report (happy path)           PASSED ✓
TEST 2: Score not found (HTTP 404)           PASSED ✓
TEST 3: Missing required fields (HTTP 400)   PASSED ✓
TEST 4: With resolution (MTTR calculated)    PASSED ✓
TEST 5: Hash reproducibility                 PASSED ✓

RESULTS: 5 passed, 0 failed out of 5 tests
```

---

## 7. Status Codes HTTP

| Código | Condición |
|---|---|
| 201 | Reporte creado exitosamente |
| 200 | Idempotente (mismo idempotency_key) |
| 400 | MISSING_REQUIRED_FIELD, INVALID_RESOLVED_AT, INVALID_MTTR |
| 404 | SCORE_NOT_FOUND |
| 500 | INTERNAL_ERROR |

---

## 8. Validación en HANA (queries de verificación)

```sql
-- Total de incidentes
SELECT COUNT(*) FROM "SOC_LOGS"."INCIDENTS";

-- Últimos 3 reportes
SELECT INCIDENT_ID, SCORE_ID, STATUS, SEVERITY, MTTD_SECONDS, MTTR_SECONDS
FROM "SOC_LOGS"."INCIDENTS"
ORDER BY REPORT_CREATED_AT DESC FETCH FIRST 3 ROWS ONLY;

-- Vista linked (INCIDENTS + MODEL_SCORES)
SELECT COUNT(*) FROM "SOC_LOGS"."V_INCIDENTS_REPORT";
```

---

## 9. Limitaciones Conocidas

1. **GET /api/report/incident/<id>** retorna HTTP 501 (pendiente para siguiente fase)
2. **Sin AuthN/AuthZ** en endpoints (añadir Bearer token si requerido)
3. **Un incidente por score_id** (diseño actual permite múltiples, no implementado)
4. **FULL_REPORT_JSON** puede crecer; considerar archivado > 90 días

---

## 10. Próximos Pasos

### Corto plazo (opcional):
- [ ] Implementar GET /api/report/incident/<id> (lectura desde HANA)
- [ ] Integración con dashboard (Gerardo): nueva vista "Forensic Reports" consultando V_INCIDENTS_REPORT
- [ ] Añadir filtros por severity/status/date_range en dashboard

### Medio plazo:
- [ ] Añadir AuthN (Bearer token) a endpoints
- [ ] Soportar N incidentes por score_id
- [ ] Exportar reportes a PDF para auditoría externa
- [ ] Webhook notifications a SAP ANS cuando se crea incidente

---

## 11. Criterios de Aceptación — CUMPLIDOS

### Funcionalidad ✅
- [x] POST /api/report/incident retorna HTTP 201 con incident_id UUID
- [x] Mismo idempotency_key retorna el mismo incident_id
- [x] score_id inexistente retorna HTTP 404
- [x] timeline contiene eventos cronológicos
- [x] mttr_seconds null sin resolución, positivo con resolución
- [x] report_hash es SHA-256 reproducible

### Persistencia ✅
- [x] INCIDENTS incrementa tras crear reporte
- [x] IDEMPOTENCY_KEY tiene UNIQUE constraint
- [x] SCORE_ID en INCIDENTS existe en MODEL_SCORES

### Compatibilidad ✅
- [x] GET /health → HTTP 200
- [x] POST /predict detecta ataques
- [x] Ningún endpoint existente retorna 500

### Seguridad ✅
- [x] Longitud máxima validada (notes: 2000 chars)
- [x] Parameterized queries en HANA (sin SQL injection)
- [x] Validación resolved_at >= detection_timestamp

---

**Implementación completa.** Sistema listo para producción.
