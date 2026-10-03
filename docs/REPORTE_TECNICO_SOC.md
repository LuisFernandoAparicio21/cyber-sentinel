# Reporte Tecnico - SAP Security Operations Center (SOC)

**Proyecto**: Sistema SOC basado en SAP BTP  
**Universidad**: UPAEP  
**Fecha**: 12 de Abril, 2026  
**Estado**: PRODUCTION READY

---

## 1. Resumen Ejecutivo

El proyecto SAP SOC es un sistema de deteccion de ataques de red desplegado en SAP Business Technology Platform (BTP). Combina ingestion de datos en tiempo real, almacenamiento en SAP HANA Cloud, un modelo de Machine Learning (RandomForest) entrenado en la nube, y un dashboard Next.js para visualizacion de alertas.

**Resultado principal**: Modelo con **97.58% de accuracy** entrenado sobre 500,000 registros de seguridad, persistido como BLOB en HANA Cloud.

---

## 2. Equipo

| Miembro | Rol | Directorio |
|---------|-----|-----------|
| **Fernando Aparicio** | Cloud Integration Engineer | `01_Cloud_Integration_Fernando/` |
| **Carlos & Iker** | Data Science / ML | `02_Data_Science_Carlos_Iker/` |
| **Rene & Luis** | Data Architecture (SQL Views) | `03_Data_Architecture_Rene_Luis/` |
| **Gerardo** | Dashboard / Visualizacion | `04_Security_Visualisation_Gerardo/` |
| **Manuel** | Project Management | `05_Project_Management_Manuel/` |

---

## 3. Arquitectura General

```
                        ARQUITECTURA SAP SOC
================================================================

  FUENTES DE DATOS                    SAP BTP CLOUD FOUNDRY
  ================                    =====================

  [5 Parquet Files]  ------>  +---------------------------+
   209MB total                |   soc-api-ingestor        |
                              |   Flask API (01_ingest)   |
  [API Webhooks]  ---------->|   manifest.yml            |
   Live Events                |   1024M RAM / 2GB Disk    |
                              +---------------------------+
                                         |
                                         | hdbcli (TLS)
                                         v
                              +---------------------------+
                              |   SAP HANA CLOUD          |
                              |   Schema: SOC_LOGS        |
                              |                           |
                              |   SECURITY_DATA_FINAL     |
                              |   1.2M+ registros         |
                              |   24 columnas             |
                              |                           |
                              |   SECURITY_API_LOGS       |
                              |   (API live events)       |
                              |                           |
                              |   ML_ARTIFACTS            |
                              |   (modelo como BLOB)      |
                              +---------------------------+
                                   |              |
                         +---------+    +---------+---------+
                         |              |                   |
                         v              v                   v
                   SQL VIEWS      ML TRAINING         DASHBOARD
                   =========      ===========         =========
                   V_ATTACKS_ML   baseml_model.py     Next.js
                   V_ALERTS_24H   CF Task (1GB)       /api/alerts
                   V_STATS_BY_    RandomForest        hdb client
                    CATEGORY      50 trees            polling 30s
```

---

## 4. Componentes por Fase

### FASE 1: Ingestion de Datos (Fernando)

**Archivo principal**: `01_Cloud_Integration_Fernando/01_ingest_api.py`

Aplicacion Flask desplegada en Cloud Foundry que:

1. **API Pull**: Descarga logs de `https://<sap-provided-soc-api-host>` cada 30 minutos
2. **Webhook Receiver** (`POST /api/webhook`): Recibe eventos push con autenticacion HMAC
3. **Bulk Replenishment** (`POST /api/replenish`): Endpoint para backfill de datos
4. **Persistencia dual**: Guarda en HANA Cloud + Parquet local como backup

**Endpoints REST**:

| Endpoint | Metodo | Funcion |
|----------|--------|---------|
| `/health` | GET | Health check (CF) |
| `/api/logs?limit=N` | GET | Ultimos N registros |
| `/api/stats` | GET | Estadisticas por tipo |
| `/api/webhook` | POST | Receptor de alertas push |
| `/api/replenish` | POST | Backfill masivo |

**Despliegue CF** (`manifest.yml`):

```yaml
applications:
- name: soc-api-ingestor
  memory: 1024M
  disk_quota: 2048M
  buildpack: python_buildpack
  command: python 01_ingest_api.py
  health-check-type: http
  health-check-http-endpoint: /health
```

**Tabla HANA creada**: `SOC_LOGS.SECURITY_API_LOGS` (23 columnas, incluyendo raw payload, tokens LLM, metadatos HTTP)

---

### FASE 2: SQL Views (Rene & Luis)

**Archivo**: `03_Data_Architecture_Rene_Luis/01_hana_views_step3.sql`

Vistas SQL sobre `SECURITY_DATA_FINAL` para separar datos de entrenamiento vs dashboard:

| Vista | Proposito | Filtro |
|-------|-----------|--------|
| `V_ATTACKS_ML` | Entrenamiento ML | Datos validos, ultimos 90 dias |
| `V_ALERTS_24H` | Dashboard real-time | Solo LIVE (api_live, webhook), ultimas 24h |
| `V_ML_FEATURES` | Inferencia (features normalizadas) | Ultimos 7 dias, COALESCE en nulls |
| `V_MODEL_SCORES` | Auditoria de predicciones | Donde MODEL_SCORE IS NOT NULL |

**Logica de Severidad** (calculada en `V_ALERTS_24H`):

```sql
CASE
  WHEN IS_ATTACK = 1 AND MODEL_CONFIDENCE >= 0.9 THEN 'CRITICAL'
  WHEN IS_ATTACK = 1 AND MODEL_CONFIDENCE >= 0.7 THEN 'HIGH'
  WHEN IS_ATTACK = 1 AND MODEL_CONFIDENCE >= 0.5 THEN 'MEDIUM'
  ELSE 'LOW'
END AS ALERT_SEVERITY
```

**Indices creados**:
- `IDX_ATTACKS_ML_TIMESTAMP` (INSERTION_DATE DESC, TIMESTAMP DESC)
- `IDX_ATTACKS_ML_SOURCE` (INGESTION_SOURCE)
- `IDX_ALERTS_24H_SEVERITY` (IS_ATTACK, INSERTION_DATE DESC)

**Roles con GRANT**:
- `SAP_ML_USER` → V_ATTACKS_ML, V_ML_FEATURES
- `SAP_DASHBOARD_USER` → V_ALERTS_24H
- `SAP_ANALYTICS_USER` → V_MODEL_SCORES

---

### FASE 3: ML Training (Carlos & Iker)

**Archivo**: `01_Cloud_Integration_Fernando/baseml_model.py` (desplegado en CF)  
**Copia local**: `02_Data_Science_Carlos_Iker/baseml_model.py`

#### Pipeline de Entrenamiento

```
HANA (SECURITY_DATA_FINAL)
  |
  | LIMIT/OFFSET chunks de 50K
  v
500,000 registros (~125 MB)
  |
  | Preprocesamiento:
  |   - PROTOCOL: TCP->6, UDP->17, ICMP->1
  |   - Fill nulls con mediana
  |   - Cap outliers al percentil 99.9
  |   - Dtype optimization (float32, int8)
  v
Train/Test Split (80/20, stratified)
  |
  | StandardScaler (fit on train)
  v
RandomForest (50 trees, depth=12, n_jobs=2)
  |
  | Accuracy: 97.58%
  v
Persist to HANA (ML_ARTIFACTS table, BLOB)
```

#### Features del Modelo (14)

| Feature | Dtype | Descripcion |
|---------|-------|-------------|
| DURATION | float32 | Duracion del flujo |
| SRC_PORT | int32 | Puerto origen |
| DST_PORT | int32 | Puerto destino |
| PROTOCOL | int8 | Protocolo codificado |
| FLAG_SYN | int8 | Flag TCP SYN |
| FLAG_ACK | int8 | Flag TCP ACK |
| FLAG_FIN | int8 | Flag TCP FIN |
| FLAG_RST | int8 | Flag TCP RST |
| TOTAL_FWD_PACKETS | int32 | Paquetes forward |
| TOTAL_BWD_PACKETS | int32 | Paquetes backward |
| FLOW_BYTES_PER_S | float64 | Bytes por segundo |
| FLOW_PACKETS_PER_S | float64 | Paquetes por segundo |
| FWD_PKT_LEN_MEAN | float64 | Media longitud pkt fwd |
| BWD_PKT_LEN_MEAN | float64 | Media longitud pkt bwd |

#### Resultados Finales (CF Task #17 - ml_persist_v3)

| Metrica | Valor |
|---------|-------|
| **Accuracy** | **97.58%** |
| Precision (ataque) | 0.98 |
| Recall (ataque) | 0.83 |
| F1-score (ataque) | 0.90 |
| F1-score (benigno) | 0.99 |
| Macro avg F1 | 0.94 |
| Tiempo entrenamiento | ~14 segundos |
| Tamano modelo | 2,285,769 bytes |
| Tamano scaler | 1,367 bytes |

#### Persistencia en HANA

El modelo se almacena como BLOB en `SOC_LOGS.ML_ARTIFACTS`:

```sql
CREATE COLUMN TABLE "SOC_LOGS"."ML_ARTIFACTS" (
    "ARTIFACT_NAME"  NVARCHAR(100) PRIMARY KEY,
    "ARTIFACT_DATA"  BLOB,
    "METADATA"       NCLOB,
    "UPDATED_AT"     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
```

Artefactos almacenados:
- `baseml_model` (RandomForest serializado, 2.2 MB)
- `baseml_scaler` (StandardScaler, 1.3 KB)
- `baseml_metadata` (JSON con metricas)

**Para cargar el modelo desde HANA**:
```python
cursor.execute('SELECT "ARTIFACT_DATA" FROM "SOC_LOGS"."ML_ARTIFACTS" WHERE "ARTIFACT_NAME"=?', ('baseml_model',))
blob = cursor.fetchone()[0]
model = joblib.load(io.BytesIO(blob))
```

---

### FASE 4: Dashboard (Gerardo)

**Directorio**: `04_Security_Visualisation_Gerardo/soc-dashboard/`  
**Stack**: Next.js + TypeScript + `hdb` (SAP HANA client para Node.js)

**Conexion a HANA** (`src/lib/hana.ts`):
- Usa `hdb` (createClient) con TLS
- Cache en memoria con TTL de 10 segundos
- Timeout de conexion: 12 segundos
- Query: `SELECT TOP 50 FROM V_ALERTS_24H ORDER BY INGESTION_TS DESC`

**API Route** (`src/app/api/alerts/route.ts`):
- `GET /api/alerts` → Retorna alertas de ultimas 24h
- `force-dynamic` (sin cache de Next.js)
- Headers: `Cache-Control: no-store, max-age=0`

**Paneles del Dashboard**:
1. Alertas en tiempo real (V_ALERTS_24H)
2. Severidad (CRITICAL / HIGH / MEDIUM / LOW)
3. Graficas por categoria de ataque
4. Drill-down por IP y protocolo

---

### FASE 5: Project Management (Manuel)

**Directorio**: `05_Project_Management_Manuel/`

Contenido:
- `ESTRUCTURA_PROYECTO.md` - Estructura del proyecto
- `PLAN_TRATAMIENTO.md` - Plan de tratamiento de datos
- `REPORTE_METRICAS.md` - Reporte de metricas
- `DocumentosEstudioSAP/` - Material de estudio SAP HANA (openSAP Week 2-4)

---

## 5. Esquema de Datos en HANA

### Tabla Principal: `SOC_LOGS.SECURITY_DATA_FINAL`

Tabla particionada por HASH en EVENT_ID (x4), con 24 columnas:

| Columna | Tipo | Descripcion |
|---------|------|-------------|
| EVENT_ID | NVARCHAR | Identificador unico (PK) |
| TIMESTAMP | TIMESTAMP | Fecha/hora del evento |
| SRC_IP / DST_IP | NVARCHAR | IPs origen/destino |
| SRC_PORT / DST_PORT | INTEGER | Puertos origen/destino |
| PROTOCOL | NVARCHAR | TCP, UDP, ICMP |
| DURATION | DOUBLE | Duracion del flujo |
| TOTAL_FWD/BWD_PACKETS | INTEGER | Conteo de paquetes |
| FLOW_BYTES_PER_S | DOUBLE | Throughput |
| FLAG_SYN/ACK/FIN/RST | TINYINT | Flags TCP |
| ATTACK_CATEGORY | NVARCHAR | Categoria del ataque |
| IS_ATTACK | TINYINT | 0=benigno, 1=ataque |
| DATASET_SOURCE | NVARCHAR | Origen del dataset |
| ... | ... | Campos adicionales |

**Volumen**: 1.2M+ registros (abril 2026)

### Tabla: `SOC_LOGS.SECURITY_API_LOGS`

Tabla para logs de la API en vivo (23 columnas): ID, TIMESTAMP, LOG_TYPE, SOURCE_TYPE, HTTP_METHOD, REQUEST_PATH, campos LLM (tokens, modelo, temperatura), RAW_PAYLOAD.

### Tabla: `SOC_LOGS.ML_ARTIFACTS`

Almacenamiento de artefactos ML como BLOBs (modelo, scaler, metadata).

---

## 6. Historial de Despliegue (CF Tasks)

El entrenamiento del modelo requirio 17 CF Tasks iterativos para resolver todos los problemas:

| Task | ID | Estado | Problema / Solucion |
|------|----|--------|---------------------|
| ml_final | 8 | FAILED | `logs/` dir no existia en CF → Agregado `mkdir` |
| ml_final_v2-v3 | 9-10 | FAILED | HANA detenida → Esperar wake-up |
| ml_final_v4 | 11 | FAILED | Columna `TOTAL_BYTES` no existe en la tabla real → Removida |
| ml_final_v5 | 12 | FAILED | Overflow float32 en FLOW_BYTES_PER_S → Cambiado a float64 |
| ml_final_v6 | 13 | FAILED | OOM: n_jobs=-1 = 64 threads → n_jobs=2, MAX_ROWS=500K |
| ml_final_v7 | 14 | **SUCCEEDED** | Primer entrenamiento exitoso (97.53%) |
| ml_persist_v1 | 15 | SUCCEEDED | Entrenamiento OK pero sin persist (droplet viejo) |
| ml_persist_v2 | 16 | SUCCEEDED | Mismo problema → `cf restage` para nuevo droplet |
| **ml_persist_v3** | **17** | **SUCCEEDED** | **Modelo entrenado + persistido en HANA (97.58%)** |

**Leccion aprendida**: Despues de hacer `cf push` con codigo nuevo, es necesario hacer `cf restage` antes de `cf run-task` para asegurar que el task use el droplet actualizado.

---

## 7. Configuracion y Despliegue

### Variables de Entorno (CF)

```
HANA_HOST=<your-hana-host>.hanacloud.ondemand.com
HANA_PORT=443
HANA_USER=DBADMIN
HANA_PASSWORD=********
SOC_API_TOKEN=<REDACTED>
WEBHOOK_SECRET=<REDACTED>
```

### Comandos de Despliegue

```bash
# Desplegar aplicacion
cf push soc-api-ingestor

# Ejecutar entrenamiento ML como CF Task
cf run-task soc-api-ingestor --name ml_persist_v3 --command "python baseml_model.py" -m 1G -k 2G

# Ver logs del task
cf logs soc-api-ingestor --recent | grep ml_persist_v3

# Verificar estado
cf tasks soc-api-ingestor
```

### Dependencias Python (`requirements.txt`)

```
flask
requests
pandas
pyarrow
hdbcli
schedule
scikit-learn>=1.3.0
joblib>=1.3.0
```

---

## 8. Datasets Fuente

Los datos provienen de multiples fuentes de seguridad consolidadas:

| Dataset | Tipo | Registros | Descripcion |
|---------|------|-----------|-------------|
| CIC-IDS 2017 | Parquet | ~2M | Canadian Institute for Cybersecurity |
| UNSW-NB15 | Parquet | ~2M | UNSW Sydney network data |
| SAP API Logs | Live | Variable | Logs LLM/HTTP en tiempo real |
| Webhooks | Live | Variable | Alertas push |

**Distribucion en entrenamiento** (500K muestra):
- Benigno (IS_ATTACK=0): 434,946 (87%)
- Ataque (IS_ATTACK=1): 65,054 (13%)

---

## 9. Seguridad

- Conexiones HANA siempre con TLS (`encrypt=True`)
- Credenciales inyectadas via variables de entorno en CF (nunca hardcoded en produccion)
- Webhook autenticado con HMAC (`X-API-Key` + `hmac.compare_digest`)
- Datos de red sin PII (solo IPs, puertos, metricas de flujo)
- Roles HANA con principio de minimo privilegio (SAP_ML_USER, SAP_DASHBOARD_USER, SAP_ANALYTICS_USER)

---

## 10. Limitaciones y Trabajo Futuro

### Limitaciones Actuales
- El modelo se entrena con batch (no online learning)
- V_ALERTS_24H depende de datos LIVE que aun no fluyen continuamente
- Dashboard en desarrollo (Next.js local, no desplegado en CF)
- No hay pipeline de re-entrenamiento automatico

### Trabajo Futuro
1. **Inferencia en tiempo real**: Cargar modelo desde ML_ARTIFACTS y scoring via `/predict`
2. **Re-entrenamiento programado**: CF Task semanal con nuevos datos
3. **Dashboard deployment**: Desplegar Next.js en CF o Vercel
4. **Monitoreo del modelo**: Drift detection, metricas de produccion
5. **Alertas activas**: Notificaciones cuando IS_ATTACK=1 con confidence >= 0.9

---

## 11. Estructura del Repositorio

```
SAP proyecto/
|
|-- 00_PROJECT_STATUS.md                    # Estado general
|-- 01_Cloud_Integration_Fernando/
|   |-- 01_ingest_api.py                    # Flask API + ingestion
|   |-- baseml_model.py                     # ML training (desplegado en CF)
|   |-- manifest.yml                        # CF deployment config
|   |-- requirements.txt                    # Dependencias Python
|   |-- raw_api_batches/                    # Parquet backups
|   +-- hana_config.json                    # Config HANA
|
|-- 02_Data_Science_Carlos_Iker/
|   |-- baseml_model.py                     # Copia sync del modelo
|   |-- ml_baseline_trainer.py              # Trainer original
|   |-- ml_inference.py                     # Modulo de inferencia
|   |-- MODEL_SPECS.md                      # Metricas del modelo
|   +-- README.md                           # Documentacion ML
|
|-- 03_Data_Architecture_Rene_Luis/
|   |-- 01_hana_views_step3.sql             # SQL Views (V_ATTACKS_ML, etc.)
|   |-- 01_create_user.sql                  # Usuarios HANA
|   |-- 02_create_table.sql                 # DDL tabla principal
|   +-- STEP3_INTEGRATION_GUIDE.md          # Guia de integracion
|
|-- 04_Security_Visualisation_Gerardo/
|   +-- soc-dashboard/                      # Next.js app
|       |-- src/app/page.tsx                # Dashboard principal
|       |-- src/app/api/alerts/route.ts     # API de alertas
|       |-- src/lib/hana.ts                 # Conexion HANA (hdb)
|       +-- .env.local                      # Variables locales
|
|-- 05_Project_Management_Manuel/
|   |-- ESTRUCTURA_PROYECTO.md
|   |-- PLAN_TRATAMIENTO.md
|   +-- REPORTE_METRICAS.md
|
|-- documentacion/                          # Diagramas Excalidraw
|   |-- GENERAL_ARQUITECTURA.excalidraw
|   +-- GERARDO_DASHBOARD.excalidraw
|
+-- documentacion_final/                    # Este reporte
    +-- REPORTE_TECNICO_SOC.md
```

---

**Generado el**: 2026-04-12  
**Ultima actualizacion del modelo**: 2026-04-12 06:23:20 UTC (CF Task #17)
