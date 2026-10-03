# 🏗️ Arquitectura de Proyecto: SAP AI Security Detection

Este documento propone la estructuración profesional de carpetas para el directorio `SAP proyecto`. A medida que el proyecto escala de simples scripts locales a una integración completa en la nube de **SAP BTP (HANA Cloud)**, es fundamental separar las responsabilidades del código mediante el patrón Arquitectónico de Capas de Datos.

---

## 📂 Propuesta de Estructura de Directorios

La nueva organización del proyecto se dividirá en **4 Pilares Principales**:

```text
📁 SAP proyecto/
│
├── 📁 01_Data_Ingestion/           ← (Conectividad y Extracción)
│   ├── 02_download_unsw.py
│   └── 06_ver_cyber.py
│
├── 📁 02_Data_Processing/          ← (Limpieza, Transformación y Merge)
│   ├── 01_clean_cicids2017.py
│   ├── 03_clean_unsw.py
│   ├── 04_clean_sap.py
│   ├── 05_clean_siem.py
│   ├── 06_clean_cyber.py
│   ├── 07_merge_datasets.py
│   └── Tratamiento.PY               
│
├── 📁 03_Infrastructure_SQL/       ← (Despliegue y Base de Datos SAP)
│   ├── mta.yaml                     (Próximamente para SAP BTP)
│   └── src/                         (HDI Container de Rene y Luis)
│
├── 📁 04_Documentation/            ← (Gobernanza de Datos y Estudio)
│   ├── PLAN_TRATAMIENTO.md
│   ├── REPORTE_METRICAS.md
│   └── 📁 DocumentosEstudioSAP/    (Slides, Transcripts y Plugins de Excalidraw)
│
└── final_sap_btp_dataset.parquet    ← 🚀 Output Maestro (Entregable Final)
```

---

## 🧠 Justificación Arquitectónica (¿Por qué esta estructura?)

### 1. `01_Data_Ingestion/` (Ingesta de Datos)
**Destino de:** Scripts enfocados *únicamente* en ir a fuentes externas (HuggingFace CLI, APIs, lecturas `.readline()`) para explorar colummnas y descargar crudos.
- **Por qué escalarlo aquí:** En SAP BTP, esta capa evolucionará para conectarse al **SAP Connectivity Service** o SAP Integration Suite. Los scripts aquí no alteran datos, sólo actúan como "Tuberías de entrada" (Pipes).

### 2. `02_Data_Processing/` (Procesamiento de Datos)
**Destino de:** Toda nuestra lógica densa de Pandas, mapeo estricto del `unified_schema`, downcasting de memoria y el mega-merge (`07_merge_datasets.py`).
- **Por qué escalarlo aquí:** Esta es la "Cocina" o capa de transformación (ETL). Separar la limpieza de la descarga significa que si HuggingFace cambia sus URLs mañana, tus scripts de `02_Data_Processing/` no se rompen, porque ellos solo asumen que la data cruda "ya existe" en local.

### 3. `03_Infrastructure_SQL/` (Infraestructura de BTP)
**Destino de:** Los futuros archivos `.sql`, las vistas CDS, y configuraciones de Cloud Foundry (archivos `mta.yaml`, Secret Managers).
- **Por qué escalarlo aquí:** Rene y Luis (Backend/Base de Datos) no necesitan entender Python ni cómo bajaste los datos. Ellos necesitan un ambiente limpio y directo en esta carpeta donde solo vean declaraciones DDL (Data Definition Language) para crear el **HDI Container** en SAP HANA y enlazar los roles de seguridad.

### 4. `04_Documentation/` (Documentación)
**Destino de:** `PLAN_TRATAMIENTO.md`, este mismo archivo y reportes de métricas.
- **Por qué escalarlo aquí:** Un proyecto sin documentación centralizada asume que el creador vivirá para siempre. Agrupar diagramas de Excalidraw y reglas de negocio aquí permite que cualquier Ingeniero Cloud nuevo que entre al reto se suba al barco leyendo una sola carpeta.

---

## 🔄 Plan de Migración (Siguiente Movimiento)

Si estás de acuerdo con esta arquitectura de datos, ejecutaremos una serie de comandos de terminal para crear literalmente este árbol de carpetas en tu Windows y moveremos cada uno de los archivos `.py` y `.md` a su habitación correspondiente de manera limpia, sin romper nada.
