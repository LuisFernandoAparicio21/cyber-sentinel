# Plan: Integrar alert_loop como thread en dar_server.py
**Objetivo:** Que soc-api en CF arranque alert_loop automáticamente, sin depender de PC local.

---

## Situación actual

| Componente | Donde corre | Genera alertas |
|---|---|---|
| `dar_server.py` (Flask REST API) | ✅ CF (soc-api) | ❌ NO |
| `alert_loop.py` (loop generador) | ❌ Solo local | ✅ SI |

**Problema:** Cuando se cierra la PC local → no llegan alertas a HANA → dashboard vacío.

---

## Solución técnica

`alert_loop.py` ya expone una función `run_loop()`. Solo necesito:
1. Importarla en `dar_server.py`
2. Arrancarla en un **thread daemon** al inicio del Flask app
3. Controlar con env var `ENABLE_ALERT_LOOP` para no duplicar en local

---

## PASOS

### PASO 1: Modificar `dar_server.py`
- [ ] Agregar import: `from alert_loop import run_loop`
- [ ] Agregar threading launcher después de las instancias compartidas
- [ ] Default: solo arranca si `ENABLE_ALERT_LOOP=true`

### PASO 2: Modificar `manifest.yml` (soc-api)
- [ ] Agregar env var: `ENABLE_ALERT_LOOP: "true"`

### PASO 3: Push a CF
- [ ] `cf push soc-api -f manifest.yml`
- [ ] Esperar staging (~3 min)

### PASO 4: Verificar logs en CF
- [ ] `cf logs soc-api --recent | grep "Alert loop"`
- [ ] Esperado: "Alert loop started | interval=120s"
- [ ] Esperado: cada 2 min "ATTACK ... hana=OK"

### PASO 5: Verificar HANA
- [ ] Detener `alert_loop.py` local (Ctrl+C)
- [ ] Detener `monitor_alerts.py` local (Ctrl+C)
- [ ] Ejecutar `python monitor_alerts.py`
- [ ] Esperar 2-3 min y ver si **timestamps nuevos** aparecen

### PASO 6: Verificar dashboard
- [ ] Abrir https://soc-dashboard.cfapps.us10-001.hana.ondemand.com
- [ ] Confirmar alertas visibles y actualizándose

---

## Rollback
Si algo falla: quitar `ENABLE_ALERT_LOOP` del manifest, push otra vez.
