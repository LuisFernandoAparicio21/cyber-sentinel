# 🚀 Plan de Deploy: Despliegue del Worker en SAP BTP Cloud Foundry

**Responsable:** Fernando Aparicio (Cloud Integration Engineer)  
**Fecha:** 24 de Marzo de 2026  
**Objetivo:** Subir el script `01_ingest_api.py` a SAP BTP para que corra en la nube 24/7 sin depender de tu laptop.

---

## 1. Prerequisitos (Lo que necesitas tener antes de empezar)

| Recurso | ¿Lo tienes? | Detalles Configurables |
|---|---|---|
| **SAP BTP Trial/Subaccount** | ✅ | `SAP_Innovation_Challenge` / `f16684...upaep-ferx` |
| **Cloud Foundry CLI (cf cli)** | ✅ | Instalado (`C:\Program Files\Cloud Foundry\cf.exe`) |
| **API Endpoint de tu subaccount** | ✅ | `https://api.cf.us10-001.hana.ondemand.com` |
| **Espacio (Space) creado en CF** | ✅ | Space: `Development` |

> [!IMPORTANT]
> Antes de empezar, confirma con Manuel si el equipo ya tiene un subaccount de BTP activo y quién es el Org Manager. Sin esto no podemos hacer `cf push`.

---

## 2. Archivos que necesitamos crear

Todos van dentro de tu carpeta `01_Cloud_Integration_Fernando/`:

### [NEW] `manifest.yml` — El "manual de montaje" para SAP

```yaml
---
applications:
- name: soc-api-ingestor
  memory: 256M
  disk_quota: 512M
  buildpack: python_buildpack
  command: python 01_ingest_api.py
  no-route: true           # No necesita URL pública, es un worker interno
  health-check-type: process
  env:
    SOC_API_TOKEN: "<REDACTED>"
```

**¿Por qué cada línea importa?**
- `no-route: true` → Le dice a SAP "no necesito una URL web, solo soy un worker que corre en background".
- `health-check-type: process` → SAP verificará que el proceso de Python siga vivo. Si muere, lo reinicia solo.
- `env: SOC_API_TOKEN` → El token vivará como variable de entorno segura, no hardcodeado en el código.

### [NEW] `requirements.txt` — Las dependencias de Python

```
requests
pandas
pyarrow
schedule
```

### [NEW] `runtime.txt` — La versión de Python

```
python-3.11.x
```

---

## 3. Cambio en `01_ingest_api.py` — Leer token desde variable de entorno

```diff
- TOKEN = "<REDACTED>"
+ TOKEN = os.environ.get("SOC_API_TOKEN", "token-no-configurado")
```

Esto es una **práctica de seguridad crítica**: el token ya no vive en el código fuente (que alguien podría ver en Git), sino que SAP BTP lo inyecta como variable de entorno desde el `manifest.yml`.

---

## 4. Comandos de Deploy (En orden)

```bash
# Paso 1: Loguearte en Cloud Foundry
.\cf.exe login -a https://api.cf.us10-001.hana.ondemand.com

# Paso 2: Verificar que estás en el org y space correctos
.\cf.exe target

# Paso 3: Desplegar el worker
.\cf.exe push

# Paso 4: Ver los logs en tiempo real
.\cf.exe logs soc-api-ingestor --recent

# Paso 5: Verificar que está corriendo
.\cf.exe app soc-api-ingestor
```

---

## 5. Verificación Post-Deploy

| Check | Comando | Resultado esperado |
|---|---|---|
| App está corriendo | `.\cf.exe app soc-api-ingestor` | `state: started` |
| Logs activos | `.\cf.exe logs soc-api-ingestor --recent` | `✅ Servidor vivo — status: ok` |
| Token inyectado | `.\cf.exe env soc-api-ingestor` | `SOC_API_TOKEN` visible en las variables |
| Parquets generados | Revisar en el bind de almacenamiento | Archivos `.parquet` creados cada 30 min |

---

## 6. User Review Required (✅ RESUELTO)

> [!NOTE]
> Todos los prerrequisitos fueron validados exitosamente:
> 1. ✅ Acceso a **subaccount de SAP BTP** confirmado (`SAP_Innovation_Challenge`).
> 2. ✅ **CF CLI** instalado y conectado (Login exitoso).
> 3. ✅ **Org y Space** (`Development`) confirmados en BTP.
>
> **Estamos listos para preparar los archivos (`manifest.yml`, etc.) y hacer el `cf push` final.**
