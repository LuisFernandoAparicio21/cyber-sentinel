# Plan de Verificación - Pipeline de Alertas
**Objetivo:** Diagnosticar por qué las alertas no llegan al dashboard en CF

---

## Pipeline completo esperado

```
alert_loop.py → AlertManager → V_MODEL_SCORES (HANA) → soc-dashboard (CF) → pantalla
```

---

## PASO 1: Verificar HANA está UP
**Comando:**
```bash
python monitor_alerts.py
```
**Esperado:** Muestra alertas (no error de conexión)
**Si falla:** HANA Cloud aún dormido, iniciar desde BTP cockpit

---

## PASO 2: Verificar alert_loop.py está corriendo (local)
**Comando:**
```powershell
# En otra terminal:
python "02_Data_Science_Carlos_Iker/alert_loop.py"
```
**Esperado:** Logs de "Alerta enviada" cada 120 segundos
**Si no corre:** Necesita iniciarse manualmente

---

## PASO 3: Verificar que alertas llegan a HANA (local)
**Comando:** Dejar monitor_alerts.py corriendo y esperar 2 minutos
**Esperado:** Aparece una alerta nueva con timestamp reciente
**Si no aparece:** alert_loop no está escribiendo a HANA correctamente

---

## PASO 4: Verificar soc-api en CF tiene alert_loop activo
**Comando:**
```bash
cf logs soc-api --recent
```
**Esperado:** Logs de alert_loop en los últimos 2 minutos
**Si no hay:** alert_loop no corre en CF (solo corre localmente)

---

## PASO 5: Verificar soc-dashboard puede leer HANA desde CF
**Comando:**
```bash
curl https://soc-dashboard.cfapps.us10-001.hana.ondemand.com/api/alerts
```
**Esperado:** JSON con alertas
**Si da error:** HANA connection issue desde CF

---

## PASO 6: Verificar el dashboard en el navegador
**URL:** https://soc-dashboard.cfapps.us10-001.hana.ondemand.com
**Esperado:** Dashboard cargado con alertas visibles
**Si no muestra:** Problema en el frontend

---

## Ejecutar en orden. Parar en el primer paso que falle.
