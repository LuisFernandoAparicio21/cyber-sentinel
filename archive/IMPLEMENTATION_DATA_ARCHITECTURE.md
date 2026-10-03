# Data Architecture — Implementación para Hackathon

**Equipo:** Rene & Luis (03_Data_Architecture_Rene_Luis)  
**Responsabilidad:** SQL views, schema design, data flow in HANA  
**Peso en scoring:** 25% (SAP Integration) + 20% (Architecture)

---

## 🎯 Objetivo del Hackathon para Data Architecture

**Design and deploy correct SQL views and schema in SAP HANA Cloud that support ML training, real-time inference, and forensic reporting.**

---

## ✅ Lo que YA ESTÁ HECHO

- ✅ `01_hana_views_step3.sql` — Script para crear 4 vistas
- ✅ Schema design — unified_schema con campos normalizados
- ✅ HANA connection — credenciales y acceso configurado
- ✅ Dataset histórico cargado — 5M registros en HANA

---

## ❌ LO QUE FALTA (CRÍTICO)

### 1. 🔴 **EXECUTE SQL VIEWS** (BLOCKER)
**¿Qué es?** Ejecutar el script de vistas en HANA Studio/DBeaver

**Status:** Script existe pero NO ha sido ejecutado en HANA

**Tareas:**
```sql
-- En HANA Studio o DBeaver, ejecutar:
-- Archivo: 03_Data_Architecture_Rene_Luis/01_hana_views_step3.sql

-- Esto DEBE crear 4 vistas:
1. V_ATTACKS_ML          — 5M rows (histórico + live para TRAINING)
2. V_ALERTS_24H          — <1000 rows (últimas 24h para DASHBOARD)
3. V_ML_FEATURES         — Features normalizadas para inference
4. V_MODEL_SCORES        — Audit trail de predicciones

-- Validación POST-EJECUCIÓN:
SELECT COUNT(*) FROM "SOC_LOGS"."V_ATTACKS_ML";       -- Debe ser ~5M
SELECT COUNT(*) FROM "SOC_LOGS"."V_ALERTS_24H";       -- Debe ser 100-500
SELECT * FROM "SOC_LOGS"."V_ML_FEATURES" LIMIT 10;    -- Debe tener features
SELECT * FROM "SOC_LOGS"."V_MODEL_SCORES" LIMIT 1;    -- Debe estar vacía (auditoría)
```

**Verificar que incluye:**
- [ ] Crear esquema "SOC_LOGS"
- [ ] Crear tabla "SECURITY_DATA_FINAL" (si no existe)
- [ ] Crear 4 vistas SQL
- [ ] Crear índices para performance
- [ ] Dar GRANTS a usuarios (SOC_READER, SOC_WRITER)
- [ ] Commit de cambios

**Criterios de éxito:**
- [ ] V_ATTACKS_ML tiene ≥ 4.9M registros
- [ ] V_ALERTS_24H tiene 100-1000 registros (24h window)
- [ ] V_ML_FEATURES tiene los 14 features (SRC_PORT, DST_PORT, etc.)
- [ ] V_MODEL_SCORES está vacía y lista para predicciones
- [ ] Sin errores SQL

---

### 2. 🔴 **DATA QUALITY VALIDATION** (BLOCKER)
**¿Qué es?** Validar que datos en HANA son correctos y limpios

**Status:** NO validado

**Tareas:**

```sql
-- 1. Validar V_ATTACKS_ML
SELECT 
  COUNT(*) as total_records,
  COUNT(DISTINCT IS_ATTACK) as attack_values,
  COUNT(DISTINCT ATTACK_CATEGORY) as categories,
  MIN(SRC_PORT) as min_port,
  MAX(DST_PORT) as max_port,
  COUNT(*) FILTER (WHERE SRC_PORT IS NULL) as null_src_ports
FROM "SOC_LOGS"."V_ATTACKS_ML";

-- Esperado:
-- total_records: ~5M
-- attack_values: 2 (0=benign, 1=attack)
-- categories: ≥5 (DDoS, PortScan, etc.)
-- null_src_ports: 0 (sin nulos en features críticas)

-- 2. Validar distribución de ataques
SELECT 
  ATTACK_CATEGORY,
  COUNT(*) as count,
  ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER(), 2) as pct
FROM "SOC_LOGS"."V_ATTACKS_ML"
WHERE IS_ATTACK = 1
GROUP BY ATTACK_CATEGORY
ORDER BY count DESC;

-- Esperado: Distribución balanceada (no 99% un tipo)

-- 3. Validar rango temporal
SELECT 
  MIN(TIMESTAMP) as earliest,
  MAX(TIMESTAMP) as latest,
  COUNT(DISTINCT DATE(TIMESTAMP)) as days_covered
FROM "SOC_LOGS"."V_ATTACKS_ML";

-- Esperado: Múltiples días cubiertos

-- 4. Validar V_ALERTS_24H (últimas 24 horas ONLY)
SELECT COUNT(*) FROM "SOC_LOGS"."V_ALERTS_24H";
-- Esperado: 100-500 registros (si hay data de últimas 24h)
```

**Documentar:**
- [ ] Row counts para cada vista
- [ ] Null value percentages
- [ ] Attack distribution
- [ ] Timestamp range
- [ ] Data quality score (✅ pass o ❌ fail)

---

### 3. 🟡 **PERFORMANCE INDEXES** (ARCHITECTURE)
**¿Qué es?** Asegurar queries rápidas para ML y dashboard

**Status:** Indexes creados pero NO validados

**Tareas:**

```sql
-- Crear índices (si no están):
CREATE INDEX IDX_V_ATTACKS_ML_IS_ATTACK 
  ON "SOC_LOGS"."SECURITY_DATA_FINAL" ("IS_ATTACK");

CREATE INDEX IDX_V_ATTACKS_ML_TIMESTAMP 
  ON "SOC_LOGS"."SECURITY_DATA_FINAL" ("TIMESTAMP");

CREATE INDEX IDX_V_ALERTS_24H_INGESTION 
  ON "SOC_LOGS"."SECURITY_DATA_FINAL" ("INGESTION_SOURCE");

-- Validar indexes
SELECT 
  INDEX_NAME, 
  TABLE_NAME, 
  COLUMN_NAME,
  COLUMN_ORDER
FROM SYS.M_RS_INDEXES
WHERE SCHEMA_NAME = 'SOC_LOGS'
ORDER BY TABLE_NAME, INDEX_NAME;

-- Test query performance:
EXPLAIN SELECT * FROM "SOC_LOGS"."V_ATTACKS_ML" WHERE IS_ATTACK = 1 LIMIT 1000;
-- Esperado: Uses index, execution time < 500ms
```

---

### 4. 🟡 **INTEGRATION WITH ML PIPELINE** (ARCHITECTURE)
**¿Qué es?** Asegurar que views actualizadas automáticamente cuando hay nuevas predicciones

**Status:** Views creadas pero sin auto-update

**Tareas:**

```sql
-- 1. Crear TRIGGER para V_MODEL_SCORES
CREATE TRIGGER TRIGGER_INSERT_MODEL_SCORES 
AFTER INSERT ON "SOC_LOGS"."V_MODEL_SCORES"
FOR EACH ROW
BEGIN
  -- Opcional: Update statistics
  -- ANALYZE TABLE "SOC_LOGS"."V_ALERTS_24H" COMPUTE STATISTICS;
  
  -- Opcional: Log evento de predicción
  INSERT INTO "SOC_LOGS"."PREDICTION_LOG" (...);
END;

-- 2. Crear stored procedure para refresh de views (si needed)
CREATE PROCEDURE REFRESH_ALERTS_24H()
LANGUAGE SQLSCRIPT
AS
BEGIN
  -- Limpia alertas viejas (>24h)
  DELETE FROM "SOC_LOGS"."V_ALERTS_24H" 
  WHERE TIMESTAMP < ADD_HOURS(CURRENT_TIMESTAMP, -24);
END;

-- 3. Schedule refresh (opcional, si HANA lo permite)
-- CALL REFRESH_ALERTS_24H();  -- Ejecutar diariamente
```

---

### 5. 🟡 **FORENSIC REPORTING TABLE** (DATA FOR REPORTING)
**¿Qué es?** Nueva tabla para almacenar reportes de incidents

**Status:** NO existe

**Tareas:**

```sql
-- Crear tabla para reports
CREATE COLUMN TABLE "SOC_LOGS"."INCIDENT_REPORTS" (
  INCIDENT_ID NVARCHAR(100) NOT NULL,
  ATTACK_TYPE NVARCHAR(50),
  SEVERITY NVARCHAR(20),  -- CRITICAL, HIGH, MEDIUM, LOW
  SRC_IP NVARCHAR(50),
  DST_IP NVARCHAR(50),
  CONFIDENCE DECIMAL(5,4),
  DETECTION_TIMESTAMP TIMESTAMP,
  ALERT_SENT_TIMESTAMP TIMESTAMP,
  REPORT_GENERATED_TIMESTAMP TIMESTAMP,
  REMEDIATION_STEPS NCLOB,
  FORENSIC_ANALYSIS NCLOB,
  PRIMARY KEY (INCIDENT_ID)
);

-- Crear índices
CREATE INDEX IDX_INCIDENTS_TIMESTAMP 
  ON "SOC_LOGS"."INCIDENT_REPORTS" ("DETECTION_TIMESTAMP");
CREATE INDEX IDX_INCIDENTS_SEVERITY 
  ON "SOC_LOGS"."INCIDENT_REPORTS" ("SEVERITY");
```

---

### 6. 🟡 **SECURITY & GRANTS** (ARCHITECTURE)
**¿Qué es?** Asegurar permisos correctos para usuarios/roles

**Status:** Parcialmente configurado

**Tareas:**

```sql
-- Crear roles (si no existen)
CREATE ROLE SOC_READER;
CREATE ROLE SOC_WRITER;
CREATE ROLE SOC_ADMIN;

-- Dar permisos para readers (dashboard, reportes)
GRANT SELECT ON "SOC_LOGS"."V_ATTACKS_ML" TO SOC_READER;
GRANT SELECT ON "SOC_LOGS"."V_ALERTS_24H" TO SOC_READER;
GRANT SELECT ON "SOC_LOGS"."V_MODEL_SCORES" TO SOC_READER;
GRANT SELECT ON "SOC_LOGS"."INCIDENT_REPORTS" TO SOC_READER;

-- Dar permisos para writers (ML, ingestor)
GRANT SELECT, INSERT ON "SOC_LOGS"."V_MODEL_SCORES" TO SOC_WRITER;
GRANT SELECT, INSERT ON "SOC_LOGS"."INCIDENT_REPORTS" TO SOC_WRITER;
GRANT SELECT ON "SOC_LOGS"."SECURITY_DATA_FINAL" TO SOC_WRITER;

-- Dar permisos admin
GRANT ALL PRIVILEGES ON "SOC_LOGS".* TO SOC_ADMIN;

-- Asignar usuarios a roles
CREATE USER carlos IDENTIFIED BY '<password>';
GRANT SOC_WRITER TO carlos;

CREATE USER gerardo IDENTIFIED BY '<password>';
GRANT SOC_READER TO gerardo;
```

---

## 📋 Dependencias

| Task | Depends On | Timeline |
|---|---|---|
| Execute SQL Views | Schema already exists | Day 1 (FIRST) |
| Data Quality Validation | Views executed | Day 1 (IMMEDIATE) |
| Performance Indexes | Views exist | Day 1-2 |
| ML Integration | Views + Model ready | Day 2-3 |
| Forensic Tables | Report requirements clear | Day 2-3 |
| Security Grants | All tables created | Day 2-3 |

---

## 🔄 Workflow para Rene/Luis

```
1. EXECUTE SQL VIEWS (01_hana_views_step3.sql)
   ↓ Verify: COUNT(*) for each view
   
2. VALIDATE DATA QUALITY (row counts, nulls, distribution)
   ↓ All checks pass
   
3. CREATE PERFORMANCE INDEXES
   ↓ Test query execution time
   
4. SETUP INTEGRATION WITH ML (triggers, procedures)
   ↓ Ready for model predictions
   
5. CREATE FORENSIC REPORTING TABLE
   ↓ Ready for incident reports
   
6. CONFIGURE SECURITY GRANTS
   ↓ Users have correct permissions
```

---

## 🧪 Validation Checklist

```
Views Created:
[ ] V_ATTACKS_ML exists and has ≥4.9M rows
[ ] V_ALERTS_24H exists with timestamp filter (24h)
[ ] V_ML_FEATURES exists with 14 numeric features
[ ] V_MODEL_SCORES exists and is empty

Data Quality:
[ ] No null values in critical features (SRC_PORT, DST_PORT, IS_ATTACK)
[ ] IS_ATTACK has values 0 and 1 only
[ ] ATTACK_CATEGORY has ≥5 attack types
[ ] Timestamp range covers multiple days
[ ] No duplicate rows

Performance:
[ ] V_ATTACKS_ML query < 500ms for 1M rows
[ ] V_ALERTS_24H query < 100ms
[ ] Indexes created and verified
[ ] Query plan uses indexes

Integration:
[ ] Triggers work correctly
[ ] Stored procedures callable
[ ] Permissions correctly assigned
[ ] No permission errors
```

---

## 📊 Scoring Impact

- ✅ **SAP Integration (25%)**: HANA setup, indexes, performance
- ✅ **Architecture Maturity (20%)**: Data quality, scalability, separation of concerns

---

## 🚨 CRITICAL PATH

```
EXECUTE SQL VIEWS (Day 1) ← BLOCKER
    ↓
DATA QUALITY VALIDATION (Day 1)
    ↓
ML TRAINING CAN BEGIN (Day 1-2)
    ↓
Performance Indexes (Day 2)
    ↓
Integration Setup (Day 2-3)
```

Sin las vistas en HANA, nada más puede empezar.

---

**Deadline:** SQL views ejecutadas y validadas EOD Day 1  
**Status:** ⏳ Por empezar  
**Owners:** Rene & Luis
