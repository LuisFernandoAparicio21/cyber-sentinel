# Plan Maestro de Tratamiento de Datos
## SAP AI Security Detection Challenge

---

## 1. Contexto y Restricciones

| Parámetro | Valor |
|---|---|
| **RAM HANA Cloud** | 16 GB |
| **Entorno local** | Python + Pandas (fallback a Colab) |
| **Datasets objetivo** | 5 |
| **Destino final** | SAP BTP → HANA Cloud → Alerting System |
| **Formato de salida** | CSV/Parquet comprimido, listo para ingestión |

### Datasets a integrar

| # | Dataset | Tipo de datos | Tamaño aprox. |
|---|---|---|---|
| 1 | `CIC-IDS2017` (Network-Flows) | Flujos de red | ~2.8M filas, 80+ cols |
| 2 | `UNSW-NB15` (Network-Flows) | Flujos de red | ~2.5M filas, 49 cols |
| 3 | `orkungedik/SAP-security-dataset` | Seguridad SAP | Variable |
| 4 | `darkknight25/Advanced_SIEM_Dataset` | Logs SIEM | Variable |
| 5 | `pyToshka/cyber-security-events` | Eventos de seguridad | Variable |

---

## 2. Estrategia de Unificación

### 2.1 Esquema Común (`unified_schema`)

Todos los datasets se mapearán a un **esquema unificado** con columnas estandarizadas:

```
ESQUEMA UNIFICADO (target)
├── event_id          (str)    — ID único del evento
├── timestamp         (datetime) — Marca temporal normalizada
├── src_ip            (str)    — IP origen
├── dst_ip            (str)    — IP destino
├── src_port          (int32)  — Puerto origen
├── dst_port          (int32)  — Puerto destino
├── protocol          (int8)   — Protocolo (TCP=6, UDP=17, etc.)
├── duration          (float32) — Duración del flujo en segundos
├── total_fwd_packets (int32)  — Paquetes forward
├── total_bwd_packets (int32)  — Paquetes backward
├── total_bytes       (int64)  — Bytes totales
├── flow_bytes_per_s  (float32) — Bytes por segundo
├── flow_packets_per_s(float32) — Paquetes por segundo
├── fwd_pkt_len_mean  (float32) — Media longitud paquetes fwd
├── bwd_pkt_len_mean  (float32) — Media longitud paquetes bwd
├── flag_syn          (int8)   — Flag SYN count
├── flag_ack          (int8)   — Flag ACK count
├── flag_fin          (int8)   — Flag FIN count
├── flag_rst          (int8)   — Flag RST count
├── label             (str)    — Etiqueta original del dataset
├── attack_category   (str)    — Categoría unificada (ver tabla 2.2)
├── is_attack         (int8)   — 0=benigno, 1=ataque (binario)
└── dataset_source    (str)    — Nombre del dataset origen
```

### 2.2 Mapeo de Labels a Categorías Unificadas

| Categoría unificada | CIC-IDS2017 | UNSW-NB15 |
|---|---|---|
| `benign` | BENIGN | normal |
| `brute_force` | FTP-Patator, SSH-Patator | — |
| `dos` | DoS slowloris, DoS Slowhttptest, DoS Hulk, DoS GoldenEye | dos |
| `ddos` | DDoS | — |
| `web_attack` | Web Attack – Brute Force, XSS, SQL Injection | — |
| `infiltration` | Infiltration | backdoor |
| `botnet` | Bot | — |
| `port_scan` | PortScan | reconnaissance |
| `exploit` | Heartbleed | exploits, shellcode |
| `malware` | — | worms |
| `fuzzing` | — | fuzzers |
| `generic` | — | generic |
| `analysis` | — | analysis |

> Los datasets 3-5 (SAP, SIEM, cyber-events) se mapearán al mismo esquema cuando los procesemos.

### 2.3 Convención de nombres de columnas

- Todo en **snake_case** minúsculas
- Sin espacios, tildes ni caracteres especiales
- Tipos de datos optimizados: `float64` → `float32`, `int64` → `int32`/`int8`, categorías → `category`

---

## 3. Criterios de Limpieza

### 3.1 Protocolo (aplicar en orden)

```
PIPELINE DE LIMPIEZA
│
├── 1. DUPLICADOS → Eliminar filas 100% idénticas (keep='first')
├── 2. INFINITOS  → Reemplazar ±inf con NaN
├── 3. NULOS
│   ├── Columna >40% nulos → ELIMINAR columna
│   ├── Numérica → rellenar con mediana
│   └── Categórica → rellenar con 'unknown'
├── 4. NEGATIVOS  → Clamp a 0 en conteos/longitudes
├── 5. OUTLIERS   → Cap al percentil 99.9 en bytes/paquetes
└── 6. DOWNCAST   → Optimizar tipos para reducir memoria
```

### 3.2 Umbrales de calidad

| Métrica | Umbral para descartar columna |
|---|---|
| % nulos | > 40% |
| Varianza cero | Eliminar |
| Correlación > 0.95 | Conservar solo una |

---

## 4. Selección de Features (Reducción)

### 4.1 ¿Qué necesita el Alerting System?

| Pregunta de seguridad | Features |
|---|---|
| **¿Quién?** | `src_ip`, `dst_ip`, `src_port`, `dst_port` |
| **¿Cuándo?** | `timestamp`, `duration` |
| **¿Cómo?** | `protocol`, flags (SYN, ACK, FIN, RST) |
| **¿Cuánto?** | `total_bytes`, `flow_bytes_per_s`, `flow_packets_per_s` |
| **¿Qué tan anómalo?** | Estadísticas de paquetes (mean, std, max, min) |
| **¿Es ataque?** | `label`, `attack_category`, `is_attack` |

### 4.2 Proceso de reducción

```
80+ columnas originales
    ↓ Fase 1: Eliminar metadata no útil (Flow ID, timestamps internos)
~70 columnas
    ↓ Fase 2: Eliminar varianza cero + correlación >0.95
~35-40 columnas
    ↓ Fase 3: Selección por importancia (Random Forest feature importance)
~20-25 columnas finales  ← TARGET PARA HANA
```

### 4.3 Estimación de tamaño en HANA

```
5 datasets × ~2M filas = ~10M filas
25 cols × 4 bytes avg = 100 bytes/fila
10M × 100 bytes ≈ 1 GB sin comprimir
Con compresión HANA (~5x) ≈ 200 MB  ✅ Cabe en 16 GB
```

---

## 5. Escalabilidad: Pipeline Modular

### 5.1 Arquitectura

```
Dataset crudo (HuggingFace)
    ↓ load_raw()        ← Carga optimizada
    ↓ clean()           ← Protocolo limpieza §3
    ↓ unify()           ← Mapeo esquema común §2
    ↓ select_features() ← Reducción §4
    ↓ export()          ← CSV/Parquet para BTP
Dataset limpio (unified_schema)
```

### 5.2 Estructura del proyecto

```
SAP proyecto/
└── data_pipeline/
    ├── config.py              # Esquema, mapeos, constantes
    ├── pipeline.py            # Clase DataPipeline
    ├── datasets/
    │   ├── cic_ids2017.py     # Adaptador CIC-IDS2017
    │   ├── unsw_nb15.py       # Adaptador UNSW-NB15
    │   ├── sap_security.py    # Adaptador SAP-security
    │   ├── siem_dataset.py    # Adaptador SIEM
    │   └── cyber_events.py    # Adaptador cyber-events
    ├── raw/                   # Datos crudos
    ├── cleaned/               # Datos intermedios
    └── output/                # Datos finales para BTP
```

### 5.3 Agregar dataset N+1
1. Crear `datasets/nuevo.py` con mapeo de columnas
2. Registrarlo en `config.py`
3. Ejecutar `pipeline.py` — mismo flujo aplica

---

## 6. Verificación

- [ ] Sin nulos en `label`, `is_attack`
- [ ] Sin infinitos en todo el dataset
- [ ] Columnas coinciden con `unified_schema`
- [ ] Tipos optimizados (no `float64` innecesario)
- [ ] Distribución de labels coherente
- [ ] Tamaño < 3 GB por dataset

---

## 7. Orden de ejecución

| Fase | Dataset | Estado |
|---|---|---|
| **1** | CIC-IDS2017 (Network-Flows) | 🔜 Siguiente |
| **2** | UNSW-NB15 (Network-Flows) | ⏳ Pendiente |
| **3** | SAP-security-dataset | ⏳ Pendiente |
| **4** | Advanced_SIEM_Dataset | ⏳ Pendiente |
| **5** | cyber-security-events | ⏳ Pendiente |
| **Final** | Merge + Export para SAP BTP | ⏳ Pendiente |
