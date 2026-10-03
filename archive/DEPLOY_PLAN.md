# Plan de Despliegue: soc-dashboard a Cloud Foundry

**Fecha:** 2026-04-19  
**Objetivo:** Desplegar soc-dashboard (Next.js) a CF correctamente

---

## Estado Actual
- [x] soc-api-ingestor → CF corriendo (1/1)
- [x] soc-api → CF corriendo (1/1)
- [ ] soc-dashboard → CF **FALLIDO** (código nunca subido, `last uploaded` vacío)

## Causa del Fallo Anterior
1. Se borró `.next` antes del push
2. El push se canceló durante el upload (sólo "Manifest applied", nunca "Uploading bits")
3. App queda registrada en CF pero sin código

---

## Pasos a Ejecutar

### PASO 1: Crear .cfignore
- [x] Crear `04_Security_Visualisation_Gerardo/soc-dashboard/.cfignore`
- Excluir: `node_modules/`, `.next/cache/`, `.git/`
- Mantener: `.next/` (artifacts del build, necesarios para `next start`)

### PASO 2: Reconstruir app localmente
- [x] Ejecutar `npm run build` en directorio soc-dashboard
- Verificar: build exitoso sin errores TypeScript
- Verificar: directorio `.next/` existe y contiene archivos

### PASO 3: Verificar .next antes del push
- [x] Confirmar que `.next/server` existe ✅
- [x] Verificar tamaño: `.next/` = 11MB (sin cache 146KB) ✅

### PASO 4: Push a Cloud Foundry
- [x] Ejecutar `cf push soc-dashboard -f manifest.yml` ✅
- [x] last uploaded: Sun 19 Apr 21:36:48 CST 2026 ✅
- [x] instances: 1/1 running ✅

### PASO 5: Verificar despliegue
- [x] `cf app soc-dashboard` → `running 1/1` ✅
- [x] `/api/health` responde: `{"api":"ok","hana":"error","ingestor":"error"}` ⚠️
- [ ] **BLOQUEADOR: HANA Cloud está hibernado** → "HANA Database instance is stopped"
  - Solución: Iniciar HANA Cloud desde SAP BTP cockpit manualmente
  - URL cockpit: https://hana.ondemand.com (buscar instancia y dar "Start")

---

## Rollback (si falla)
```bash
cf delete soc-dashboard -f
cf push soc-dashboard -f manifest.yml
```
