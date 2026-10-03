"""
notifier.py — Fernando Aparicio
Layer 2: Webhook Client para notificaciones bidireccionales
- PUSH hacia SAP (alertas que detectamos)
- PULL desde SAP (confirmar que recibió los eventos)
"""

import os
import json
import requests
import logging
from datetime import datetime
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [NOTIFIER] %(message)s")
log = logging.getLogger(__name__)

# ── Configuración ──────────────────────────────────────────
# Nuestro endpoint (que Santiago llama)
OUR_WEBHOOK_URL    = "https://soc-api-ingestor.cfapps.us10-001.hana.ondemand.com/api/webhook"
OUR_WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]

# Endpoint de SAP (lo que Santiago nos proporciona — TO BE FILLED)
SAP_WEBHOOK_URL    = os.environ.get("SAP_WEBHOOK_URL", "")      # ← Santiago provee esto
SAP_API_KEY        = os.environ.get("SAP_API_KEY", "")           # ← Santiago provee esto

TIMEOUT = 15  # segundos


# ── Helper ─────────────────────────────────────────────────
def _build_alert_payload(event_id: str,
                          src_ip: str,
                          attack_category: str,
                          message: str,
                          severity: str = "high") -> dict:
    """Construye el payload JSON estándar para alertas de ciberseguridad."""
    return {
        "events": [{
            "_id":                         event_id,
            "sap_function_log_type":       "SECURITY_ALERT",
            "sap_source_type":             "IDS",
            "sap_function_application":    "upaep-soc-ingestor",
            "sap_function_message":        message,
            "sap_app_env":                 "production",
            "headers_http_request_method": "POST",
            "@timestamp":                  datetime.utcnow().isoformat() + "Z",
            "attack_src_ip":               src_ip,
            "attack_category":             attack_category,
            "severity":                    severity,
        }]
    }


# ── 1. Push a nuestro propio endpoint (Internal / Santiago) ──
def push_to_webhook(events: list[dict]) -> dict:
    """
    Envía eventos a nuestro /api/webhook. 
    Usar para:
    - Pruebas manuales
    - Alertas del modelo ML de Carlos/Iker
    - Datos que Santiago nos mande manualmente
    """
    headers = {
        "Content-Type": "application/json",
        "X-API-Key":    OUR_WEBHOOK_SECRET,
    }
    payload = {"events": events}
    try:
        resp = requests.post(OUR_WEBHOOK_URL, json=payload, headers=headers, timeout=TIMEOUT)
        resp.raise_for_status()
        result = resp.json()
        log.info(f"✅ Webhook exitoso: {result.get('received', 0)} registros procesados.")
        return result
    except Exception as e:
        log.error(f"❌ Error en Webhook: {e}")
        return {"error": str(e)}


# ── Smoke test rápido ───────────────────────────────────────
if __name__ == "__main__":
    print("=== WEBHOOK INTERNAL TEST ===")
    
    # Test: Simular alerta de Santiago o del Modelo ML
    result = push_to_webhook([{
        "_id":                      "internal-test-" + datetime.utcnow().strftime("%H%M%S"),
        "sap_function_log_type":    "SECURITY_ALERT",
        "sap_source_type":          "IDS",
        "sap_function_message":     "Internal monitoring test OK",
        "attack_category":          "test_heartbeat",
    }])
    print(f"Result: {result}")

