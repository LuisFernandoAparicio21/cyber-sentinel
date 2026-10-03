from collections import Counter
from datetime import datetime

import pytest


def event(i, **extra):
    base = {
        "_id": f"evt-{i}",
        "@timestamp": "2026-04-20T10:00:00+02:00",
        "sap_function_log_type": "LLM_REQUEST",
        "sap_function_application": "soc-api",
        "region_id": "US-030",
        "http_status": 200,
        "llm_model_id": "gpt-4o",
        "llm_finish_reason": "stop",
        "llm_total_tokens": "120",
    }
    base.update(extra)
    return base


# ── idempotency ───────────────────────────────────────────────────────────────
def test_content_hash_ignores_key_order_and_empty_values(ingest):
    a = {"x": 1, "y": "b", "z": None}
    b = {"y": "b", "x": 1, "w": ""}
    assert ingest.content_hash(a) == ingest.content_hash(b)


def test_event_without_id_gets_a_deterministic_id(ingest):
    rec = event(1)
    del rec["_id"]
    r1 = ingest.build_rows(dict(rec), datetime(2026, 4, 20), frozenset({"US-030"}), Counter())
    r2 = ingest.build_rows(dict(reversed(list(rec.items()))), datetime(2026, 4, 21), frozenset({"US-030"}), Counter())
    assert r1["event_id"] == r2["event_id"]


def test_timestamps_are_normalized_to_utc(ingest):
    rows = ingest.build_rows(event(1), datetime(2026, 4, 20), frozenset({"US-030"}), Counter())
    assert rows["event"][2] == datetime(2026, 4, 20, 8, 0, 0)


# ── schema drift ──────────────────────────────────────────────────────────────
def test_unknown_values_are_mapped_and_counted(ingest):
    unmapped = Counter()
    rows = ingest.build_rows(event(1, region_id="XX-999", sap_function_application="new-app",
                                   llm_total_tokens="N/A"),
                             datetime(2026, 4, 20), frozenset({"US-030", "unknown"}), unmapped)
    assert rows["event"][17] == "unknown"          # region falls back to an FK-safe value
    assert rows["event"][16] == "unknown"          # application too
    assert rows["llm"][7] == 0                     # bad number does not crash the batch
    assert unmapped["region_id"] == 1
    assert unmapped["application_id"] == 1
    assert unmapped["llm_total_tokens"] == 1


# ── transactional load ────────────────────────────────────────────────────────
def test_batch_is_loaded_in_one_commit(ingest, fake_conn):
    conn = fake_conn()
    result = ingest.load_records(conn, [event(i) for i in range(5)], "w1", "api")
    assert result == {"loaded": 5, "quarantined": 0, "unmapped": {}}
    assert len(conn.rows_for("SOC_EVENTS")) == 5
    assert len(conn.rows_for("MODEL_PREDICTIONS")) == 5
    assert conn.commits == 1
    assert conn.autocommit is True                 # restored after the load


def test_records_that_are_not_objects_go_to_quarantine(ingest, fake_conn):
    conn = fake_conn()
    result = ingest.load_records(conn, [event(1), "garbage", 42], "w1", "api")
    assert result["loaded"] == 1
    assert result["quarantined"] == 2
    assert len(conn.rows_for("INGEST_QUARANTINE")) == 2


def test_rejected_bulk_falls_back_to_row_by_row(ingest, fake_conn):
    conn = fake_conn(fail_bulk=True, fail_on_execute={"evt-2"})
    result = ingest.load_records(conn, [event(i) for i in range(4)], "w1", "api")
    assert result["loaded"] == 3
    assert result["quarantined"] == 1
    loaded_ids = {row[0] for row in conn.rows_for("SOC_EVENTS")}
    assert loaded_ids == {"evt-0", "evt-1", "evt-3"}
    assert "evt-2" not in {row[0] for row in conn.rows_for("MODEL_PREDICTIONS")}


# ── run control (watermark) ───────────────────────────────────────────────────
@pytest.fixture
def api(ingest, monkeypatch):
    """Fakes the SAP API: page 1 announces the window, the rest return data."""
    calls = {"pages": []}

    def fake_fetch(page):
        calls["pages"].append(page)
        if page in calls.get("fail", set()):
            return None
        return {"window_start": "2026-04-20T10:00", "window_end": "2026-04-20T10:02",
                "total_pages": 3, "total_records": 3, "data": [event(page)]}

    monkeypatch.setattr(ingest, "check_server_alive", lambda: True)
    monkeypatch.setattr(ingest, "_fetch_page", fake_fetch)
    monkeypatch.setattr(ingest, "save_raw_batch", lambda records, name: None)
    monkeypatch.setattr(ingest.time, "sleep", lambda s: None)
    return calls


def test_window_marked_loaded_only_after_commit(ingest, fake_conn, api, monkeypatch):
    conn = fake_conn()
    monkeypatch.setattr(ingest, "get_hana_connection", lambda: conn)
    ingest.main()
    assert conn.runs["2026-04-20T10:00"] == "LOADED"
    assert len(conn.rows_for("SOC_EVENTS")) == 3
    assert ingest.STATE["last_status"] == "LOADED"


def test_failed_load_is_not_marked_loaded(ingest, fake_conn, api, monkeypatch):
    conn = fake_conn()

    def broken_load(*args, **kwargs):
        raise RuntimeError("HANA went away")

    monkeypatch.setattr(ingest, "get_hana_connection", lambda: conn)
    monkeypatch.setattr(ingest, "load_records", broken_load)
    ingest.main()
    assert conn.runs["2026-04-20T10:00"] == "FAILED"   # retried next cycle


def test_window_with_failed_pages_is_partial(ingest, fake_conn, api, monkeypatch):
    conn = fake_conn()
    api["fail"] = {3}
    monkeypatch.setattr(ingest, "get_hana_connection", lambda: conn)
    ingest.main()
    assert conn.runs["2026-04-20T10:00"] == "PARTIAL"


def test_loaded_window_is_skipped_before_downloading(ingest, fake_conn, api, monkeypatch):
    conn = fake_conn()
    conn.runs["2026-04-20T10:00"] = "LOADED"
    monkeypatch.setattr(ingest, "get_hana_connection", lambda: conn)
    ingest.main()
    assert api["pages"] == [1]                      # only page 1 was requested


# ── bootstrap ─────────────────────────────────────────────────────────────────
def test_ddl_ignores_only_already_exists_errors(ingest):
    class Cur:
        def __init__(self, msg):
            self.msg = msg

        def execute(self, sql):
            raise RuntimeError(self.msg)

    ingest._ddl(Cur("cannot use duplicate table name"), "CREATE ...")
    with pytest.raises(RuntimeError):
        ingest._ddl(Cur("insufficient privilege"), "CREATE ...")
