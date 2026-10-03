# ML & Data Science — Implementación para Hackathon

**Equipo:** Carlos & Iker (02_Data_Science_Carlos_Iker)  
**Responsabilidad:** ML model training, inference, forensic reporting  
**Peso en scoring:** 40% (Real-Time Detection) + 20% (Architecture) + 15% (Business Impact)

---

## 🎯 Objetivo del Hackathon para ML

**Build ML pipeline that detects network attacks with high precision, low false positives, and fast inference.**

---

## ✅ Lo que YA ESTÁ HECHO

- ✅ `baseml_model.py` — RandomForest trainer (chunked, memory-safe)
- ✅ `webhook_server.py` — Flask REST API con endpoints `/predict`, `/predict-batch`
- ✅ `ml_inference.py` — Scoring logic
- ✅ Dataset histórico — 5M network attack records (UNSW-NB15, CIC-IDS2017)
- ✅ Feature selection — 14 numeric features (SRC_PORT, DST_PORT, DURATION, etc.)
- ✅ Model serialization — `.joblib` format

---

## ❌ LO QUE FALTA (CRÍTICO)

### 1. 🔴 **ACTUAL MODEL TRAINING** (BLOCKER)
**¿Qué es?** Entrenar el modelo RandomForest con datos REALES

**Status:** Código existe pero modelo NO ha sido entrenado aún

**Tareas:**
```bash
# Ejecutar entrenamiento
cd 02_Data_Science_Carlos_Iker
python baseml_model.py

# Validar output
ls -la models/baseml_model.joblib          # Debe existir
cat logs/baseml_model.log                  # Ver accuracy
```

**Esperado:**
```
✓ Fetched 500,000 records from V_ATTACKS_ML
✓ Preprocessed: 500k rows
✓ Trained RandomForest (50 trees)
✓ Test Accuracy: 0.88 (88%)
✓ Saved: models/baseml_model.joblib (45 MB)
```

**Criterios de éxito:**
- [ ] Accuracy ≥ 85%
- [ ] Precision ≥ 85% (few false positives)
- [ ] Recall ≥ 80% (catch most attacks)
- [ ] Model size < 100 MB
- [ ] Inference time < 100ms per sample

---

### 2. 🔴 **REAL-TIME INFERENCE** (BLOCKER)
**¿Qué es?** Modelo debe predecir ataques en tiempo real (<100ms latency)

**Status:** Endpoints existen pero no hay load testing

**Tareas:**
```bash
# 1. Iniciar servidor
python webhook_server.py &

# 2. Test single prediction
curl -X POST http://localhost:5000/predict \
  -H "Content-Type: application/json" \
  -d '{
    "DURATION": 10.5,
    "SRC_PORT": 443,
    "DST_PORT": 5000,
    "PROTOCOL": 6,
    "FLAG_SYN": 1,
    "FLAG_ACK": 0,
    "TOTAL_FWD_PACKETS": 10,
    "TOTAL_BWD_PACKETS": 5,
    "FLOW_BYTES_PER_S": 1024.5,
    "FLOW_PACKETS_PER_S": 2.5,
    "FWD_PKT_LEN_MEAN": 512.0,
    "BWD_PKT_LEN_MEAN": 256.0,
    "FLAG_FIN": 0,
    "FLAG_RST": 0
  }'

# 3. Test batch prediction
curl -X POST http://localhost:5000/predict-batch \
  -H "Content-Type: application/json" \
  -d '{"predictions": [...]}'

# 4. Latency test (1000 requests, measure p95, p99)
ab -n 1000 -c 10 http://localhost:5000/predict
```

**Criterios de éxito:**
- [ ] `/predict` latency < 100ms (p95)
- [ ] `/predict-batch` latency < 500ms for 100 samples
- [ ] Uptime > 99.9%
- [ ] Error handling for bad input

---

### 3. 🟡 **FORENSIC REPORTING** (IMPORTANT)
**¿Qué es?** Generar reportes ejecutivos de ataques detectados

**Status:** NO existe

**Tareas:**

Crear endpoint `/api/report/incident`:
```python
# webhook_server.py - agregar:

@app.route("/api/report/incident", methods=["POST"])
def generate_incident_report():
    """
    Genera reporte forense de ataque detectado.
    
    Input:
    {
      "flow_id": "uuid",
      "attack_type": "DDoS",
      "src_ip": "192.168.1.1",
      "dst_ip": "10.0.0.1",
      "severity": "CRITICAL|HIGH|MEDIUM",
      "confidence": 0.92,
      "timestamp": "2026-04-17T10:30:00Z"
    }
    
    Output:
    {
      "report_id": "uuid",
      "executive_summary": "...",
      "technical_details": "...",
      "impact_assessment": "...",
      "remediation_steps": [
        "1. Block src_ip 192.168.1.1 at firewall",
        "2. Isolate affected subnet",
        "3. Increase monitoring"
      ],
      "forensic_analysis": "..."
    }
    """
```

**Información que debe incluir:**
- Tipo de ataque + confianza
- IPs afectadas + puertos
- Timeline de ataque
- Impacto estimado en SAP
- Pasos de remediación
- Recomendaciones de hardening

**Formato de output:**
- [ ] JSON (para API)
- [ ] HTML (para ejecutivos)
- [ ] PDF (para archivo)

---

### 4. 🟡 **MODEL ROBUSTNESS** (ARCHITECTURE)
**¿Qué es?** Validar modelo frente a pattern shifts y data inconsistencies

**Status:** NO testado

**Tareas:**

```python
# tests/test_model_robustness.py

def test_pattern_shift():
    """Validar que modelo sigue detectando con datos nuevos"""
    # Entrenar con 2020-2022 data
    # Testear con 2025 data (patrón nuevo)
    # Accuracy debe ser ≥ 80%
    
def test_data_inconsistency():
    """Validar manejo de valores nulos, outliers"""
    # Null values en features → imputar mediana
    # Outliers en FLOW_BYTES_PER_S → cap en p99.9
    # Model debe dar predicción válida siempre
    
def test_batch_vs_single():
    """Validar consistencia entre /predict y /predict-batch"""
    # 1000 samples via /predict
    # 1000 samples via /predict-batch
    # Resultados deben ser idénticos
```

---

### 5. 🟡 **MTTD MEASUREMENT** (PERFORMANCE)
**¿Qué es?** Medir Mean Time to Detect (velocidad de detección)

**Status:** NO medido

**Tareas:**

Agregar a V_MODEL_SCORES:
```sql
-- Columnas nuevas:
DETECTION_TIMESTAMP   -- Cuándo se detectó
ALERT_SENT_TIMESTAMP  -- Cuándo se envió alerta
MTTD_SECONDS          -- Diferencia (detección → alerta)

-- Ejemplo:
Attack occurrence:     2026-04-17 10:30:00.000
Detection:             2026-04-17 10:30:02.345
Alert sent:            2026-04-17 10:30:03.120
MTTD:                  3.12 seconds
```

**Métricas a calcular:**
- [ ] MTTD promedio
- [ ] MTTD p95 (95th percentile)
- [ ] MTTD p99 (99th percentile)
- [ ] False positive rate
- [ ] Attack types detected vs missed

---

## 📋 Dependencias

| Task | Depends On | Timeline |
|---|---|---|
| Model Training | SQL views (Rene/Luis) | Day 1 |
| Real-Time Inference | Model trained | Day 2 |
| Forensic Reporting | Inference working | Day 3 |
| Model Robustness | Model trained | Day 3-4 |
| MTTD Measurement | Notification system (Fernando) | Day 4-5 |

---

## 🔄 Workflow para Carlos/Iker

```
1. TRAIN MODEL (python baseml_model.py)
   ↓ Validar accuracy ≥ 85%
   
2. TEST INFERENCE (/predict, /predict-batch)
   ↓ Latency < 100ms
   
3. IMPLEMENT FORENSIC REPORTING (/api/report/incident)
   ↓ JSON + HTML + PDF output
   
4. VALIDATE ROBUSTNESS (pattern shifts, nulls, outliers)
   ↓ Pass all tests
   
5. SETUP MTTD MEASUREMENT (V_MODEL_SCORES timestamps)
   ↓ Metrics dashboard ready
```

---

## 🧪 Testing Checklist

```
Unit Tests:
[ ] test_preprocess_attacks_ml() — dtype conversion, nulls, outliers
[ ] test_model_predict() — RandomForest predictions
[ ] test_batch_predict() — /predict-batch endpoint
[ ] test_forensic_report() — incident report generation

Integration Tests:
[ ] Attack → Detection → Alert flow
[ ] MTTD measurement end-to-end
[ ] False positive rate < 15%
[ ] Model accuracy on holdout set ≥ 85%

Load Tests:
[ ] 100 req/sec on /predict
[ ] 10 req/sec on /predict-batch
[ ] p95 latency < 100ms
[ ] p99 latency < 200ms
```

---

## 📊 Scoring Impact

- ✅ **Real-Time Response (40%)**: Model accuracy + MTTD metrics
- ✅ **Architecture Maturity (20%)**: Robustness tests + batch vs single consistency
- ✅ **Business Impact (15%)**: Forensic reporting quality + remediation steps

---

## 🚨 CRITICAL PATH

```
Data Architecture (Day 1)
    ↓
MODEL TRAINING (Day 1-2) ← BLOCKER
    ↓
REAL-TIME INFERENCE (Day 2) ← BLOCKER
    ↓
Forensic Reporting (Day 3)
    ↓
MTTD Metrics (Day 4-5)
```

Sin modelo entrenado y funcionando, el resto del proyecto NO AVANZA.

---

**Deadline:** Modelo entrenado y validado antes de Day 3  
**Status:** ⏳ Por empezar  
**Owners:** Carlos & Iker
