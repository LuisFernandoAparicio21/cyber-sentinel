# Cloud Integration — Implementación para Hackathon

**Equipo:** Fernando (01_Cloud_Integration_Fernando)  
**Responsabilidad:** Ingestion, API integration, SAP notifications  
**Peso en scoring:** 25% (SAP Ecosystem Integration) + 40% (Real-Time Response)

---

## 🎯 Objetivo del Hackathon para Cloud Integration

**Build automated notification system that dispatches alerts directly to SAP.**

Transformar detecciones de ataque (del ML) → alertas automáticas en SAP en tiempo real.

---

## ✅ Lo que YA ESTÁ HECHO

- ✅ Ingestor API (`01_ingest_api.py`) — descarga logs
- ✅ Flask REST API (`webhook_server.py`) — endpoint `/predict`
- ✅ Conexión HANA Cloud configurada
- ✅ Cloud Foundry deployment structure

---

## ❌ LO QUE FALTA (CRÍTICO)

### 1. 🔴 **Notification System** (BLOCKER)
**¿Qué es?** Sistema que envía alertas de ataques detectados → SAP en tiempo real

**Actualmente:** El modelo detecta ataques pero NO hay forma de notificarlo

**Tareas:**

```bash
# 1. Crear endpoint de notificación en Flask
POST /alert/notify
{
  "attack_id": "uuid",
  "severity": "CRITICAL|HIGH|MEDIUM|LOW",
  "type": "DDoS|PortScan|etc",
  "timestamp": "ISO8601",
  "affected_resource": "src_ip/dst_ip",
  "recommendation": "Block IP / Isolate subnet / etc"
}
```

**2. Implementar notificadores:**
- [ ] **SAP Alert Management** (si disponible en BTP)
- [ ] **Email notification** (SMTP)
- [ ] **Webhook externo** (para integración SAP)
- [ ] **Dashboard push** (WebSocket)

**3. Integración con V_MODEL_SCORES:**
```sql
-- Cuando se inserta en V_MODEL_SCORES, trigger envía alert a SAP
INSERT INTO V_MODEL_SCORES ... → trigger → POST /alert/notify
```

---

### 2. 🟡 **Data Stream Integration** (PERFORMANCE)
**¿Qué es?** Real-time log ingestion sin delay

**Actualmente:** Ingestor corre cada 120s (lento para hackathon)

**Tareas:**
- [ ] Cambiar a **event-driven ingestion** (webhook listener activo)
- [ ] Reducir latencia: 120s → <5s
- [ ] Medir **MTTD (Mean Time to Detect)** en logs
- [ ] Implementar **backpressure handling** si logs llegan > capacidad

---

### 3. 🟡 **SAP BTP Deployment** (INTEGRATION)
**¿Qué es?** Deploy del sistema completo en SAP Cloud Foundry

**Actualmente:** Código local, no deployado en CF

**Tareas:**
- [ ] Update `manifest.yml` con configuración correcta
- [ ] Deploy ingestor a CF: `cf push soc-ingestor`
- [ ] Deploy notification service: `cf push soc-alerts`
- [ ] Configurar environment vars en CF (HANA_HOST, API_TOKEN, etc.)
- [ ] Health check endpoints activos en CF

---

### 4. 🟡 **Integration Test with Dashboard** (VALIDATION)
**¿Qué es?** Validar que alertas llegan al dashboard en tiempo real

**Tareas:**
- [ ] Simular ataque → modelo lo detecta → alerta se envía → dashboard muestra
- [ ] Medir latencia total: attack occurrence → alert in dashboard
- [ ] Documenter MTTD en reporte final

---

## 📋 Dependencias

| Task | Depends On | Owner |
|---|---|---|
| Notification System | ML detecta ataques correctamente | Carlos/Iker (ML) |
| Data Stream Integration | HANA views actualizadas | Rene/Luis (Data Arch) |
| SAP BTP Deployment | Código estable + env vars | Fernando |
| Integration Test | Todas las anteriores | Todos |

---

## 🔄 Workflow para Fernando

```
1. Crear notification endpoints en webhook_server.py
   ↓
2. Implementar notificadores (email, webhook, SAP)
   ↓
3. Update manifest.yml + deploy a CF
   ↓
4. Medir MTTD (latency metrics)
   ↓
5. Integration test con dashboard
   ↓
6. Documentar en reporte final
```

---

## 📊 Scoring Impact

- ✅ **Real-Time Response (40%)**: MTTD, alert latency → implementar notification system
- ✅ **SAP Integration (25%)**: CF deployment, seamless connectivity → manifest.yml + CF push
- ✅ **Architecture Maturity (20%)**: Robustness, separation of concerns → event-driven design

---

## 🚨 BLOCKER ACTUAL

**La API (`<sap-provided-soc-api-host>`) envía LLM logs, NO datos de ataques.**

**Solución:** NO usar API live. Usar solo dataset histórico (parquet 5M registros).
- Los logs históricos ya tienen `is_attack` label
- El ML los clasifica correctamente
- No hay que esperar API de red

---

**Deadline:** Antes de que Rene/Luis termine SQL views  
**Status:** ⏳ Por empezar  
**Owner:** Fernando
