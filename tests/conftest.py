import importlib.util
import os
import sys

import pytest

MODULE_DIR = os.path.join(os.path.dirname(__file__), "..", "01_Cloud_Integration_Fernando")


@pytest.fixture(scope="session")
def ingest():
    """Imports 01_ingest_api.py with dummy settings (no network, no HANA)."""
    os.environ.setdefault("SOC_API_BASE", "https://api.example.test")
    os.environ.setdefault("SOC_API_TOKEN", "test-token")
    os.environ.setdefault("WEBHOOK_SECRET", "test-secret")
    sys.path.insert(0, os.path.abspath(MODULE_DIR))
    spec = importlib.util.spec_from_file_location("ingest_api", os.path.join(MODULE_DIR, "01_ingest_api.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._result = []

    def execute(self, sql, params=None):
        if self.conn.fail_on_execute and params and params[0] in self.conn.fail_on_execute:
            raise RuntimeError("FK violation (simulated)")
        self.conn.pending.append(("execute", sql, params))
        if "SELECT \"REGION_ID\"" in sql:
            self._result = [(r,) for r in self.conn.regions]
        elif "FROM \"SOC_LOGS\".\"INGEST_RUNS\"" in sql:
            status = self.conn.runs.get(params[0])
            self._result = [(status,)] if status else []

    def executemany(self, sql, rows):
        if self.conn.fail_bulk and "INGEST_QUARANTINE" not in sql:
            raise RuntimeError("bulk rejected (simulated)")
        for row in rows:
            self.conn.pending.append(("execute", sql, row))

    def fetchall(self):
        return self._result

    def fetchone(self):
        return self._result[0] if self._result else None

    def close(self):
        pass


class FakeConn:
    """Minimal hdbcli-like connection: statements are only kept after commit()."""

    def __init__(self, regions=("unknown", "US-030"), fail_bulk=False, fail_on_execute=()):
        self.regions = list(regions)
        self.fail_bulk = fail_bulk
        self.fail_on_execute = set(fail_on_execute)
        self.pending, self.committed = [], []
        self.runs = {}
        self.autocommit = True
        self.commits = 0
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def setautocommit(self, value):
        self.autocommit = value

    def commit(self):
        self.commits += 1
        for op in self.pending:
            sql, params = op[1], op[2]
            if "INGEST_RUNS" in sql and sql.startswith("UPSERT"):
                self.runs[params[0]] = params[1]
        self.committed.extend(self.pending)
        self.pending = []

    def rollback(self):
        self.pending = []

    def close(self):
        self.closed = True

    def rows_for(self, table):
        return [p for _, sql, p in self.committed if f'"{table}"' in sql and sql.startswith("UPSERT")]


@pytest.fixture
def fake_conn():
    return FakeConn
