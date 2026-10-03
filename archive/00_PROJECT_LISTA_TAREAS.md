# SAP SOC - Lista de Tareas Pendientes

### Tareas de Seguridad y Conectividad
- [ ] **SQL Injection Patch:** Cambiar a prepared statements (`?`) en `01_ingest_api.py`.
- [ ] **Notificador Externo:** Implementar envío de alertas al Webhook de Santiago.

### Tareas de Dashboard y Visualización
- [ ] **Completar Sidebar:** Diseño de iconos y navegación lateral.
- [ ] **Status del Sistema (Health Check):** Nueva sección en el Sidebar con indicadores UP/DOWN para la API y HANA Cloud (para detectar caídas rápido).
- [ ] **Sección Threat Intelligence:** Métricas detalladas del modelo de IA.
- [ ] **Sección Salud de Infraestructura:** Estado y rendimiento de SAP HANA.
- [ ] **Mapa de Topología:** Visualización de tráfico Src/Dst IP.

### Tareas de Optimización y Otros
- [ ] **Compresión de Modelos:** Aplicar `compress=3` en la persistencia.
- [ ] **Escalado:** Ajustar a 2 instancias en el deployment de Cloud Foundry.
- [ ] **Documentación Final:** Revisión de diagramas Excalidraw generados.


Problemas reales que encontré
🔴 Problema 1: Desconexión entre tablas
El ingestor (01_ingest_api.py) escribe en SECURITY_API_LOGS, pero la vista V_ALERTS_24H hace UNION ALL entre SECURITY_DATA_FINAL y SECURITY_API_LOGS. El dashboard actualmente ve solo la rama 1 (SECURITY_DATA_FINAL con datos del 11-abril) porque la rama 2 necesita que los campos SRC_IP, DST_IP, etc. estén en RAW_PAYLOAD como JSON — y si el ingestor no los manda bien formateados, la vista los devuelve vacíos.

🔴 Problema 2: Filtro de 24h mata los datos históricos

WHERE INGESTION_TIMESTAMP >= ADD_SECONDS(CURRENT_TIMESTAMP, -86400)
Los 500 registros que ves son de SECURITY_DATA_FINAL con TIMESTAMP de 2027 (fecha futura en los datos sintéticos), que sí pasa el filtro. Cuando llegue la nueva API key y el ingestor inserte en SECURITY_API_LOGS, esos sí aparecerán porque INGESTION_TIMESTAMP será NOW.


🟡 Problema 4: V_ALERTS_24H del dashboard vs el SQL real
El SQL en 03_Data_Architecture_Rene_Luis/04_v_alerts_24h_unified_security_api.sql es la versión correcta (UNION de ambas tablas). Pero necesitamos confirmar que esa vista ya fue ejecutada en HANA — si no, la vista vieja no incluye SECURITY_API_LOGS.

formateo de hora utc time tomarlo en cuenta

hacer un cornograma un shegule de manera visual cada cuando se se procesan los batches ??

estamos consumiendo la infomrcaicon por batches, en este caso es asi, 

limitar la cantidad de veces que refesca alguien algo 

cambiar el rate limit a 2 minutos desde el front actualemnte esta a 15 donde se actualiza cada 15 segundo eso cambairalo a 2 minutos