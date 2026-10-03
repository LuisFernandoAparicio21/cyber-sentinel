# 📊 Reporte de Métricas: Data Pipeline de Ciberseguridad (SAP BTP)

Este documento registra los resultados cuantitativos del procesamiento, compresión y mapeo de los 5 datasets de seguridad originales hacia el modelo estandarizado (`unified_schema`) de 24 columnas, preparándolos para la ingesta en SAP HANA Cloud (límite de 16GB RAM).

---

## 1. Resumen Global de Reducción

| Dataset | Filas Iniciales | Columnas Iniciales | Filas Finales | Columnas Finales | Memoria Final (RAM) | Reducción de Columnas |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **CIC-IDS2017** | 2,827,677 | 84 | 2,827,677 | 23 | ~ 1.2 GB | -72% |
| **UNSW-NB15** | 2,059,415 | 50 | 2,059,415 | 23 | ~ 821 MB | -54% |
| **SAP-Security (NLP)** | 1,791 | 3 | 1,791 | 24 | ~ 1.5 MB | + Esquema Forzado |
| **Advanced SIEM** | 100,000 | 16 (JSONL Dinámico) | 100,000 | 24 | ~ 79.6 MB | + Esquema Forzado |
| **Cyber Events** | 15,098 | 120 (JSONL Dinámico) | 2,793 | 24 | ~ 2.0 MB | -80% |

> **Nota de Rendimiento:** Gracias a la limpieza de duplicados exactos y el downcast estricto de los tipos de datos en la fase de mapeo (`float64 -> float32`, `int64 -> int32`), el peso total combinado de los 5 datasets en RAM ronda apenas los **2.1 GB**, lo que los hace **100% seguros y estables** para los 16GB de límite de la instancia HANA Cloud.

---

## 2. Distribución de Clases (Ataque vs Benigno)

Para el entrenamiento y retrieval del Alerting System, es vital conocer el balance final de amenazas detectadas por el pipeline de limpieza:

| Dataset | Eventos Benignos (0) | Ataques/Amenazas (1) | Proporción de Ataque |
| :--- | :--- | :--- | :--- |
| **CIC-IDS2017** | 2,271,122 | 556,555 | 19.68% |
| **UNSW-NB15** | 1,959,772 | 99,643 | 4.83% |
| **SAP-Security (NLP)** | 1,791 (Analítica Base) | 0 | 0.00% |
| **Advanced SIEM** | 61,793 | 38,207 | 38.20% |
| **Cyber Events** | 0 | 2,793 | 100.00% |

> **Observación:** El dataset `Cyber Events (pyToshka)` resultó puramente malicioso después de remover duplicados exactos en el filtro de `event_id`, proveyendo 2,793 tácticas 100% confirmadas de ataque. Por contraparte, el `SAP-Security` aportará explicaciones benignas como baseline de comportamiento normal del RAG.

---

## 3. Estado de unificación
Todos los datasets han sido exportados exitosamente al formato columnar de compresión nativa `Parquet` (`.parquet`) usando el algoritmo Snappy, y están listos en la carpeta `D:\SAP_AI_Security\data_pipeline\cleaned\` para la fase final del proyecto: la Ingestión Conjunta (Merge).
