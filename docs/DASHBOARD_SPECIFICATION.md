# Especificación del Dashboard SOC - Gerardo

**Target Audience**: Security Analyst / CISO  
**Value Prop**: Detección proactiva de amenazas mediante IA + visualización de salud de infraestructura en tiempo real

---

## 1. Centro de Comando (Global Stats) - Hero Section

**Ubicación**: Top del dashboard (full width)  
**Propósito**: KPIs globales de un vistazo

| KPI | Fuente SQL | Actualización | Ejemplo |
|-----|-----------|----------------|---------|
| **Total de Eventos (24h)** | `SELECT COUNT(*) FROM V_ALERTS_24H` | Cada 2 min | 15,234 |
| **Alertas Críticas Activas** | `SELECT COUNT(*) FROM V_ALERTS_24H WHERE ALERT_SEVERITY='CRITICAL'` | Real-time | 8 |
| **Nivel de Amenaza Global** | `SELECT MAX(MODEL_CONFIDENCE) FROM V_ALERTS_24H WHERE IS_ATTACK=1` | Cada 2 min | 🔴 CRITICAL (92%) |
| **Tiempo Promedio de Respuesta** | `AVG(INGESTION_TS - TIMESTAMP)` | Cada 5 min | 2.3 segundos |

**Componentes React Necesarios**:
- `<StatCard icon="alert" value={criticalCount} label="Alertas Críticas" severity="critical" />`
- `<ThreatGauge value={globalThreatLevel} />`
- `<MetricBadge value={avgResponseTime} unit="s" />`

---

## 2. Motor de Inteligencia (SAP DAR Ops)

**Ubicación**: Lado izquierdo del dashboard (30% ancho)  
**Propósito**: Metricas del modelo ML y confianza

| Elemento | Fuente | SQL Query | Visualización |
|----------|--------|-----------|----------------|
| **Confidence Level Score** | `V_ALERTS_24H.MODEL_CONFIDENCE` | `SELECT AVG(MODEL_CONFIDENCE) WHERE IS_ATTACK=1` | Gauge 0-100% |
| **Attack Type Distribution** | `V_ATTACKS_ML.ATTACK_CATEGORY` | `SELECT ATTACK_CATEGORY, COUNT(*) FROM V_ALERTS_24H WHERE IS_ATTACK=1 GROUP BY ATTACK_CATEGORY` | Pie Chart |
| **Top Threat Vectors** | `V_ALERTS_24H.SRC_IP` | `SELECT SRC_IP, COUNT(*) FROM V_ALERTS_24H WHERE IS_ATTACK=1 GROUP BY SRC_IP ORDER BY COUNT(*) DESC LIMIT 5` | Bar Chart |
| **Status de Re-entrenamiento** | Metadata en HANA | `SELECT UPDATED_AT FROM ML_ARTIFACTS WHERE ARTIFACT_NAME='baseml_metadata'` | Timestamp badge |

**Componentes React**:
- `<ConfidenceGauge avgConfidence={94.2} />`
- `<AttackTypesPie categories={['DDoS', 'SQLi', 'BruteForce']} />`
- `<TopThreatsBar ips={['192.168.1.100', '10.0.0.50']} counts={[120, 85]} />`
- `<LastTrainingBadge date="2026-04-12 06:23:20 UTC" />`

---

## 3. Tráfico y Topología (Network Intelligence)

**Ubicación**: Centro-derecha (35% ancho)  
**Propósito**: Visualización de patrones de tráfico

| Elemento | Fuente SQL | Tipo | Actualización |
|----------|-----------|------|----------------|
| **Mapa de Calor Tráfico** | `SELECT SRC_IP, DST_IP, PROTOCOL, COUNT(*) FROM V_ALERTS_24H GROUP BY SRC_IP, DST_IP, PROTOCOL` | Heatmap/Map | Cada 2 min |
| **Ports in Danger** | `SELECT DST_PORT, COUNT(*) FROM V_ALERTS_24H WHERE IS_ATTACK=1 GROUP BY DST_PORT` | Bar Chart (Top 10 ports) | Cada 2 min |
| **Duración vs Bytes** | `SELECT DURATION, FLOW_BYTES_PER_S FROM V_ALERTS_24H WHERE IS_ATTACK=1` | Scatter Plot (anomalía detection) | Cada 2 min |

**Componentes React**:
- `<TrafficHeatmap sourceIPs={data} destinationIPs={data} />`
- `<DangerousPortsBar ports={[80, 443, 8080, 22]} />`
- `<DurationVsFlowScatter anomalyThreshold={0.9} />`

---

## 4. Salud de la Infraestructura SAP (System Status)

**Ubicación**: Lado derecho (35% ancho)  
**Propósito**: Health check del stack completo

| Componente | Endpoint / Query | Check | Status |
|------------|-----------------|-------|--------|
| **HANA Cloud Status** | `GET /api/health/hana` | Latencia + Memory % | 🟢 Healthy (45ms, 67% RAM) |
| **Ingestor Pulse** | `GET /api/health` (Flask) | Heartbeat cada 10s | 🟢 Alive |
| **API Connectivity** | `GET /api/alerts` | Respuesta < 3s | 🟢 Connected |
| **Model Loading** | `SELECT COUNT(*) FROM ML_ARTIFACTS` | Artifacts exist | 🟢 OK (3 artefactos) |

**Componentes React**:
- `<StatusIndicator service="HANA Cloud" latency={45} memory={67} />`
- `<PulseIndicator service="Ingestor" lastHeartbeat="2min ago" />`
- `<HealthDashboard services={healthStatus} />`

---

## 5. Feed de Eventos en Tiempo Real (Live Alert Stream)

**Ubicación**: Bottom (full width, scrollable)  
**Propósito**: Timeline interactivo de alertas

| Columna | Fuente | Tipo | Ejemplo |
|---------|--------|------|---------|
| **Timestamp** | `V_ALERTS_24H.TIMESTAMP` | ISO datetime | 2026-04-16 14:35:22 UTC |
| **Severity Badge** | Calculado | 🔴 CRITICAL / 🟠 HIGH / 🟡 MEDIUM | 🔴 CRITICAL |
| **Event Type** | `ATTACK_CATEGORY` | Tag | DDoS, SQLi, BruteForce |
| **Source IP → Dest IP** | `SRC_IP → DST_IP` | Network flow | 192.168.1.100 → 10.0.0.1 |
| **Confidence** | `MODEL_CONFIDENCE` | % Badge | 94% |
| **Acciones** | Botones | Interactive | [Ignore] [Mitigate] [Investigate] |

**Componentes React**:
```tsx
<AlertTimeline>
  {alerts.map(alert => (
    <AlertRow 
      key={alert.EVENT_ID}
      timestamp={alert.TIMESTAMP}
      severity={alert.ALERT_SEVERITY}
      category={alert.ATTACK_CATEGORY}
      sourceIP={alert.SRC_IP}
      destIP={alert.DST_IP}
      confidence={alert.MODEL_CONFIDENCE}
      onIgnore={() => handleIgnore(alert.EVENT_ID)}
      onMitigate={() => handleMitigate(alert.EVENT_ID)}
      onInvestigate={() => showPayload(alert.PAYLOAD)}
    />
  ))}
</AlertTimeline>
```

---

## Data Flow & Polling Strategy

```
Dashboard (Next.js)
    ↓
GET /api/alerts (polling every 2 min)
    ↓
src/lib/hana.ts (fetchAlerts)
    ↓
SAP HANA Cloud
    ├─ V_ALERTS_24H (main view)
    ├─ V_STATS_BY_CATEGORY (for charts)
    └─ ML_ARTIFACTS (for model metadata)
```

**Cache Strategy**: 
- In-memory cache TTL: 10 seconds (per route handler)
- Browser cache: `Cache-Control: no-store, max-age=0` (force refresh)
- Poll interval: 30-120 seconds (configurable)

---

## UX Priorities

| Prioridad | Implementación |
|-----------|-----------------|
| **Dark Mode** | Tailwind dark mode + CSS custom properties |
| **Critical Alerts Animation** | Pulse/blink animation con `animate-pulse` |
| **Responsive** | Mobile-first (hero metrics stack on mobile) |
| **Performance** | Lazy loading de gráficos, virtualized list para timeline |
| **Accessibility** | ARIA labels, semantic HTML, color contrast WCAG AA |

---

## API Endpoints Necesarios

| Endpoint | Método | Propósito | Response |
|----------|--------|-----------|----------|
| `/api/alerts` | GET | Últimas 50 alertas 24h | `{ alerts: Alert[], count: number, timestamp: ISO }` |
| `/api/alerts/stats` | GET | Estadísticas globales | `{ totalEvents: number, criticalAlerts: number, threatLevel: string }` |
| `/api/alerts/distribution` | GET | Distribución por categoría | `{ category: string, count: number }[]` |
| `/api/alerts/top-ips` | GET | Top 5 IPs amenaza | `{ ip: string, count: number, severity: string }[]` |
| `/api/health` | GET | Status de servicios | `{ hana: Status, ingestor: Status, api: Status }` |
| `/api/alerts/{id}/action` | POST | Action on alert | `{ action: 'ignore' \| 'mitigate' \| 'investigate' }` |

---

## Mockup Layout

```
╔══════════════════════════════════════════════════════════════════════╗
║  SISTEMA SOC - Security Operations Center Dashboard                  ║
╠══════════════════════════════════════════════════════════════════════╣
║                         HERO METRICS (KPIs)                           ║
║  ┌────────────────┬────────────────┬────────────────┬──────────────┐ ║
║  │ Events 24h     │ Critical⚠️      │ Threat Level 🔴│ Resp Time    │ ║
║  │ 15,234         │ 8              │ CRITICAL (92%) │ 2.3s         │ ║
║  └────────────────┴────────────────┴────────────────┴──────────────┘ ║
╠══════════════════════════════════════════════════════════════════════╣
║ AI ENGINE (30%)          │ NETWORK TOPOLOGY (35%)    │ HEALTH (35%)    ║
║ ┌──────────────────────┐ │ ┌──────────────────────┐  │ ┌────────────┐ ║
║ │ Confidence: 94%      │ │ │ Heatmap Tráfico      │  │ │ HANA: 45ms │ ║
║ │ ┌──────────────────┐ │ │ │ [Grafica de calor]   │  │ │ 🟢 Healthy │ ║
║ │ │ Attack Types:    │ │ │ │                      │  │ │            │ ║
║ │ │ DDoS: 45%        │ │ │ Ports Peligrosos:     │  │ │ Ingestor:  │ ║
║ │ │ SQLi: 30%        │ │ │ [Bar chart 80,443...]  │  │ │ 🟢 Alive   │ ║
║ │ │ Brute: 25%       │ │ │                      │  │ │            │ ║
║ │ └──────────────────┘ │ │ Duration vs Bytes:    │  │ │ API Conn:  │ ║
║ │                      │ │ [Scatter plot]        │  │ │ 🟢 OK      │ ║
║ │ Top IPs:             │ │                      │  │ │            │ ║
║ │ 192.168.1.100 (120)  │ │                      │  │ │ Model: ✓   │ ║
║ │ 10.0.0.50 (85)       │ │                      │  │ │ Last: 2h   │ ║
║ │                      │ │                      │  │ │            │ ║
║ │ Last Training:       │ │                      │  │ │            │ ║
║ │ 2026-04-12 06:23 UTC │ │                      │  │ │            │ ║
║ └──────────────────────┘ │ └──────────────────────┘  │ └────────────┘ ║
╠══════════════════════════════════════════════════════════════════════╣
║                    LIVE ALERT STREAM (Scrollable)                     ║
║ ┌─────┬──────┬─────────┬──────────────────┬────────┬─────────────┐  ║
║ │Time │Sev   │Type     │Source → Dest     │Conf    │Actions      │  ║
║ ├─────┼──────┼─────────┼──────────────────┼────────┼─────────────┤  ║
║ │14:35│🔴CR  │DDoS     │192.168.1.1 →10.0 │94%     │[I][M][Inv]  │  ║
║ │14:34│🟠HI  │SQLi     │10.20.0.5 →10.0   │78%     │[I][M][Inv]  │  ║
║ │14:33│🟡MD  │BruteFrc │172.16.0.100→10.0 │62%     │[I][M][Inv]  │  ║
║ │...  │...   │...      │...               │...     │...          │  ║
║ └─────┴──────┴─────────┴──────────────────┴────────┴─────────────┘  ║
╚══════════════════════════════════════════════════════════════════════╝
```

---

## Implementación Roadmap

| Tarea | Componente | Estado | Prioridad |
|-------|-----------|--------|-----------|
| Hero Stats Card | `<StatCard />` | TODO | P0 |
| Confidence Gauge | `<ConfidenceGauge />` | TODO | P0 |
| Attack Distribution Pie | `<AttackTypesPie />` | TODO | P0 |
| Top IPs Bar Chart | `<TopThreatsBar />` | TODO | P0 |
| Traffic Heatmap | `<TrafficHeatmap />` | TODO | P1 |
| Ports Danger Chart | `<DangerousPortsBar />` | TODO | P1 |
| Health Dashboard | `<HealthDashboard />` | TODO | P0 |
| Alert Timeline | `<AlertTimeline />` | PARTIAL | P0 |
| Alert Actions | `handleIgnore/Mitigate` | TODO | P1 |
| Dark Mode | Tailwind config | TODO | P0 |
| Responsive Layout | CSS Grid | TODO | P0 |
| API Routes | `GET /api/alerts*` | PARTIAL | P0 |

---

## Success Criteria

- [ ] Dashboard carga en < 2 segundos
- [ ] Alertas críticas se actualizan cada 30 segundos
- [ ] Mobile responsive (< 768px)
- [ ] Dark mode by default
- [ ] WCAG AA accessibility
- [ ] Pruebas unitarias para componentes críticos
- [ ] Documentación de API endpoints

