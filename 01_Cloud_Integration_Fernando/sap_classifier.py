"""
SAP SOC Classifier - Rule-based detection and ML hook.
=====================================================
This module provides the logic to classify SAP logs into 'ATTACK' or 'BENIGN'.
It follows the pattern: SAP Log Record + Network Features -> Prediction Dict.

While the DAR model is not ready, it uses a rule-based system to ensure the 
pipeline works with real data immediately.
"""

from typing import Any, Dict, Optional

def rule_based_classify(record: Dict[str, Any], features: Dict[str, Any]) -> Dict[str, Any]:
    """
    Classifies a log based on expert rules while the ML model is being trained.
    Returns a format compatible with DARInference.predict_one().
    """
    log_type = str(
        record.get("sap_function_log_type") or
        record.get("LOG_TYPE") or ""
    ).strip().upper()
    status = int(
        record.get("http_status_code") or
        record.get("HTTP_STATUS_CODE") or 0
    )
    rtime = float(
        record.get("llm_response_time_ms") or
        record.get("LLM_RESPONSE_TIME_MS") or 0
    )
    finish = str(
        record.get("llm_finish_reason") or
        record.get("LLM_FINISH_REASON") or ""
    ).strip().lower()

    # Rule 1: Unauthorized cross-tenant access attempts
    if log_type == "AUDIT" and status == 403:
        return {"label": "ATTACK", "is_attack": True, "confidence": 0.88, "reason": "Audit 403: Cross-tenant/Forbidden"}

    # Rule 2: Explicit Security events
    if log_type == "SECURITY":
        return {"label": "ATTACK", "is_attack": True, "confidence": 0.75, "reason": "Security: Access Anomaly"}

    # Rule 3: Abusive latency (Possible DoS or resource abuse)
    if log_type == "LLM_TIMEOUT" and rtime > 30000:
        return {"label": "ATTACK", "is_attack": True, "confidence": 0.72, "reason": "LLM Timeout > 30s"}

    # Rule 4: System instability or corruption indicators
    if log_type == "ERROR" and status >= 500:
        return {"label": "ATTACK", "is_attack": True, "confidence": 0.70, "reason": "Server Error 5xx"}

    # Rule 5: Prompt Injection / Content Safety violations
    if finish == "content_filter":
        return {"label": "ATTACK", "is_attack": True, "confidence": 0.80, "reason": "LLM Content Filter Triggered"}

    # Rule 6: Brute force / Rate limit signs
    if status in (401, 429):
        return {"label": "ATTACK", "is_attack": True, "confidence": 0.65, "reason": "Auth Failure / Rate Limited"}

    # Default: Benign
    return {"label": "BENIGN", "is_attack": False, "confidence": 0.95}


# ──────────────────────────────────────────────────────────────────
# ML HOOK - When Carlos/Iker finish the model, swap this part:
# ──────────────────────────────────────────────────────────────────
# try:
#     from dar_inference import DARInference
#     _dar = DARInference()
#     USE_ML = True
# except Exception:
#     USE_ML = False

USE_ML = False # Force rules for now as per plan

def classify(record: Dict[str, Any], features: Dict[str, Any]) -> Dict[str, Any]:
    """
    Main entry point for log classification.
    Automatically handles the transition between rules and ML.
    """
    if USE_ML:
        # result = _dar.predict_one(features, event_id=record.get("_id"))
        # return result
        pass # Placeholder for when USE_ML becomes True

    # Fallback/Current: Rules
    prediction = rule_based_classify(record, features)
    prediction["id"] = record.get("_id")
    return prediction

if __name__ == "__main__":
    # Quick test
    sample_record = {"sap_function_log_type": "AUDIT", "http_status_code": 403, "_id": "test_rule"}
    sample_features = {} # Not used by rules yet
    print(f"Test Classify: {classify(sample_record, sample_features)}")
