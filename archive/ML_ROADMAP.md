# PLAN: Machine Learning Real para SOC — Reemplazar Simulación

**Fecha**: 2026-04-20  
**Prioridad**: Alta — el pipeline de producción actualmente usa datos SIMULADOS  
**Objetivo**: Reemplazar `alert_loop.py` (random fake data) con predicciones reales de un modelo ML entrenado sobre `SECURITY_DATA_FINAL`

---

## Diagnóstico: ¿Qué hay y qué falta?

### Lo que YA existe en producción (funciona hoy)
| Componente | Estado | Rol |
|---|---|---|
| `SECURITY_DATA_FINAL` | ✅ ~2.93M rows en HANA | Datos históricos reales de red |
| `SECURITY_API_LOGS` | ✅ 225,790 rows | Logs live de la API |
| `MODEL_SCORES` | ✅ 588 rows | Destino de predicciones (funciona) |
| `dar_client.py` | ✅ Código completo | Cliente REST para SAP DAR |
| `dar_pipeline.py` | ✅ Código completo | Entrena modelo en DAR |
| `dar_inference.py` | ✅ Código completo | Inferencia contra modelo DAR |
| `dar_server.py /predict` | ✅ En CF | Endpoint de predicción REAL (nunca se llama) |
| `alert_loop.py` | ⚠️ SIMULADO | Genera datos RANDOM — no usa ML |

### Lo que falta para que el ML funcione
| Gap | Impacto | Solución |
|---|---|---|
| `dar-key.json` sin credenciales válidas | BLOQUEANTE para SAP DAR | Obtener desde BTP Service Instances |
| Modelo DAR nunca entrenado | BLOQUEANTE para SAP DAR | Ejecutar `dar_pipeline.py` |
| `alert_loop.py` no llama a `/predict` | ML no se usa en runtime | Modificar loop para llamar a la API |
| Dashboard no muestra métricas ML | UX incompleta | Nueva sección "ML Intelligence" |

---

## Dos caminos posibles

### Camino A: SAP Data Attribute Recommendation (DAR) — Oficial del proyecto
**Ventajas**: Integración nativa SAP, escalable  
**Desventajas**: Requiere credenciales BTP, setup complejo

### Camino B: RandomForest Local en CF — Más rápido, funciona hoy
**Ventajas**: Todo el código ya existe, sin dependencias externas, 0 costo adicional  
**Desventajas**: Modelo vive dentro del container CF

> **Recomendación**: Camino B primero para tener ML real funcionando, luego migrar a Camino A.

---

## CAMINO B — RandomForest en CF (Implementación Inmediata)

### PASO 1: Verificar schema de SECURITY_DATA_FINAL
```sql
-- Verificar si son duplicados o partitions
SELECT TABLE_NAME, RECORD_COUNT, CREATE_TIME 
FROM SYS.M_CS_TABLES 
WHERE SCHEMA_NAME = 'SOC_LOGS' AND TABLE_NAME = 'SECURITY_DATA_FINAL'
ORDER BY CREATE_TIME;

-- Ver columnas disponibles
SELECT COLUMN_NAME, DATA_TYPE_NAME 
FROM SYS.TABLE_COLUMNS 
WHERE SCHEMA_NAME = 'SOC_LOGS' AND TABLE_NAME = 'SECURITY_DATA_FINAL'
ORDER BY POSITION;
```

### PASO 2: Entrenar modelo localmente
```bash
cd "02_Data_Science_Carlos_Iker"
python baseml_trainer_hana.py
# Output: models/soc_model_v1.joblib
# Esperado: accuracy 85-95%
```

**Archivo a crear**: `baseml_trainer_hana.py`
- Lee SECURITY_DATA_FINAL de HANA (chunked 50k)
- Entrena RandomForest(n_estimators=100)
- Guarda `models/soc_model_v1.joblib`
- Imprime accuracy, classification report, feature importances

### PASO 3: Crear ml_predictor.py
```python
# Wrapper del modelo local
# Misma interfaz que DARInference:
# predict_one(event_dict) → {"label", "is_attack", "confidence", "category"}
```

### PASO 4: Modificar alert_loop.py
```python
# ANTES (simulado):
tpl = random.choices(_TEMPLATES, weights=_WEIGHTS, k=1)[0]

# DESPUÉS (ML real):
from ml_predictor import MLPredictor
_predictor = MLPredictor()
result = _predictor.predict_one(raw_event)
# raw_event ahora viene de SECURITY_API_LOGS o de tráfico sintético basado en SECURITY_DATA_FINAL
```

### PASO 5: Deploy a CF
```bash
# El modelo .joblib sube dentro del container
cf push soc-api -f manifest.yml
```

---

## CAMINO A — SAP DAR (Cuando se tenga credenciales)

### Pre-requisito: Obtener dar-key.json
Desde BTP Cockpit:
1. `Services → Instances and Subscriptions`
2. Buscar instancia de **Data Attribute Recommendation**
3. `Bindings → Create Binding → Download JSON`
4. Guardar como `02_Data_Science_Carlos_Iker/dar-key.json`

### Con credenciales listas:
```bash
cd "02_Data_Science_Carlos_Iker"
python dar_pipeline.py --rows 500000 --model-name soc_attack_detection
curl https://soc-api-ingestor.cfapps.us10-001.hana.ondemand.com/model-info
```

---

## Dashboard: Nueva sección "ML Intelligence"

### Sidebar — nuevo ícono entre Threats y Feed

```
[🏠]  alerts     Alerts (default)
[📊]  threats    Threats
[🧠]  ml         ML Intelligence   ← NUEVO
[📡]  feed       Live Feed
[💚]  status     Health
```

### Vista ML Intelligence — 4 paneles

```
┌─────────────────────┬─────────────────────┐
│  MODELO ACTIVO      │  MÉTRICAS           │
│  RandomForest v1    │  Accuracy: 93.2%    │
│  Entrenado: Apr 20  │  F1-Score: 0.91     │
│  500k samples       │  AUC-ROC: 0.97      │
├─────────────────────┼─────────────────────┤
│  DISTRIBUCIÓN       │  TOP FEATURES       │
│  Pie chart por cat  │  1. FLOW_BYTES_S    │
│  (DDoS, Scan, etc)  │  2. FLAG_SYN        │
│                     │  3. DST_PORT        │
└─────────────────────┴─────────────────────┘
┌───────────────────────────────────────────┐
│  ÚLTIMAS PREDICCIONES (stream en vivo)    │
│  [ATTACK] DDoS_SYN  conf=0.97  12:03:01  │
│  [BENIGN] normal    conf=0.99  12:01:01  │
└───────────────────────────────────────────┘
```

### Endpoints que necesita (todos ya existen en dar_server.py)
| Endpoint | Datos |
|---|---|
| `GET /model-info` | nombre, versión, accuracy, fecha |
| `GET /train/status` | estado del reentrenamiento |
| `GET /api/alerts/recent?limit=20` | últimas predicciones |
| `POST /train` | lanza reentrenamiento (botón) |

---

## Archivos a crear/modificar

```
CREAR:
  02_Data_Science_Carlos_Iker/baseml_trainer_hana.py
  02_Data_Science_Carlos_Iker/ml_predictor.py
  02_Data_Science_Carlos_Iker/models/soc_model_v1.joblib  (generado)
  04_Security_Visualisation_Gerardo/soc-dashboard/src/components/ml/ml-view.tsx

MODIFICAR:
  02_Data_Science_Carlos_Iker/alert_loop.py  (random → ml real)
  02_Data_Science_Carlos_Iker/manifest.yml   (ML_MODEL_PATH, ML_ENGINE)
  04_Security_Visualisation_Gerardo/soc-dashboard/src/components/sidebar.tsx
  04_Security_Visualisation_Gerardo/soc-dashboard/src/app/page.tsx
```

---

## Cronograma sugerido

| Semana | Tarea | Resultado |
|---|---|---|
| 1 | Verificar schema + entrenar modelo | `soc_model_v1.joblib` listo |
| 1 | Crear ml_predictor.py + modificar alert_loop | CF corriendo ML real |
| 2 | Dashboard ML view (4 paneles) | Métricas visibles en UI |
| 3 | Integrar SAP DAR (si hay credenciales) | ML cloud nativo |

---

## Antes vs Después

| Aspecto | HOY (simulado) | DESPUÉS (ML real) |
|---|---|---|
| Fuente de alertas | `random.choices()` | RandomForest sobre 2.93M rows |
| Confidence | Random 0.85-0.99 | Probabilidad real del modelo |
| Categorías | 3 hardcodeadas | Aprendidas de datos reales |
| Valor demostrativo | Bajo | **Alto** |

---
*2026-04-20 | Luis Fernando + Claude Code*
