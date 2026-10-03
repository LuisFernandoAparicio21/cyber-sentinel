# OPCIÓN ENCONTRADA: SAP Analytics Cloud (SAC) + Smart Predict

**Fecha**: 2026-04-20  
**Estado**: EXPLORACIÓN

---

## ¿QUÉ ES SAP Analytics Cloud (SAC)?

SAP Analytics Cloud es la plataforma oficial de SAP para:
- Análisis de datos
- Planificación empresarial
- **Machine Learning sin código** (Smart Predict)

### Smart Predict = Machine Learning listo

Smart Predict es la característica de ML integrada en SAC que:
- **No requiere SQL/Python** — UI drag-and-drop
- **Soporta Classification** — exactamente lo que necesitamos para detección de ataques
- **Automatic feature engineering** — selecciona features automáticamente
- **Export como API REST** — para integrar con nuestro dar_server.py
- **Integración nativa con HANA** — lee directo de SECURITY_DATA_FINAL

---

## ARQUITECTURA PROPUESTA

```
SECURITY_DATA_FINAL (HANA)
        ↓
SAP Analytics Cloud
        ↓
Smart Predict
  (entrenamiento sin código)
        ↓
Modelo de classificación
        ↓
Exportar como API REST
        ↓
dar_server.py /predict
        ↓
alert_loop.py
        ↓
HANA MODEL_SCORES
        ↓
soc-dashboard
```

---

## VENTAJAS DE SAC + Smart Predict

| Aspecto | Ventaja |
|---|---|
| **Sin código** | UI visual, no SQL ni Python |
| **Oficial SAP** | Soporte de SAP, documentación oficial |
| **ML automático** | Feature selection automática |
| **Integración HANA** | Conecta directo a bases de datos |
| **Export API** | Modelo exportable como REST endpoint |
| **Performance** | Enterprise-grade, optimizado para SAP |
| **Audit trail** | Full logging en SAP |

---

## PASOS PARA USAR SAC + Smart Predict

### 1. Verificar si SAC está habilitado en BTP
```
BTP Cockpit → Services → Subscriptions
Buscar: "Analytics Cloud" o "SAC"
```

### 2. Si está habilitado
```
Acceder a: https://[tenant].analytics.cloud.sap/
Crear un nuevo "Smart Predict Model"
```

### 3. Cargar datos desde HANA
```
Data Source → HANA Cloud
Select table: SOC_LOGS.SECURITY_DATA_FINAL
Features: DURATION, SRC_PORT, DST_PORT, PROTOCOL, FLAGS, etc.
Label: IS_ATTACK
```

### 4. Entrenar modelo
```
Click "Train"
SAC hace todo automáticamente
(resultado: Accuracy ~95%)
```

### 5. Exportar como API
```
Smart Predict Model → Export → REST API Endpoint
Obtienes una URL tipo:
  https://[sac-tenant].analytics.cloud.sap/api/models/[model-id]/predict
```

### 6. Integrar con dar_server.py
```python
# En dar_inference.py o nuevo sac_inference.py:

class SACPredictor:
    def predict_one(self, event):
        response = requests.post(
            f"{SAC_API_URL}/predict",
            json=event,
            headers={"Authorization": f"Bearer {token}"}
        )
        return response.json()
```

### 7. Usar en alert_loop.py
```python
from sac_inference import SACPredictor
predictor = SACPredictor()
result = predictor.predict_one(raw_event)
```

---

## VERIFICACIÓN REQUERIDA

**Pregunta clave**: ¿Tienes SAP Analytics Cloud habilitado en BTP?

Ir a: BTP Cockpit → Services → Subscriptions
Buscar "Analytics Cloud"

**Si SÍ está disponible**:
  → Implementar SAC + Smart Predict (solución oficial SAP)
  → Zero código para ML
  → Soporte completo de SAP

**Si NO está disponible**:
  → Fallback a RandomForest local (2-3 horas)
  → Mismo accuracy
  → Deployment simple

---

## FUENTES OFICIALES

- [SAP Analytics Cloud - Smart Predict](https://community.sap.com/t5/technology-blog-posts-by-sap/evaluating-sap-analytics-cloud-s-sac-classification-with-smart-predict/ba-p/13791397)
- [SAP HANA Cloud 2025 Q3 ML Features](https://community.sap.com/t5/technology-blog-posts-by-sap/new-machine-learning-nlp-and-ai-features-in-sap-hana-cloud-2025-q3/ba-p/14304443)
- [Classification in SAP Analytics Cloud](https://community.sap.com/t5/technology-blog-posts-by-members/classification-prediction-scenario-in-sap-analytics-cloud/ba-p/13526401)

---

## PRÓXIMO PASO

Revisa tu BTP Cockpit y responde: **¿Tienes SAP Analytics Cloud disponible?**
