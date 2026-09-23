"""Shared test setup. No test here talks to Supabase or Postgres.

app/db.py and app/auth.py read these at import time. The pool is created with
open=False and PyJWKClient only fetches keys when a token is verified, so
placeholder values are enough; locally, the real .env (loaded by
app/__init__.py) wins because these are only defaults.
"""

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")

DEMO_DIR = ROOT / "demo"
DEMO_PDF = DEMO_DIR / "PesaScore_DEMO_synthetic_statement.pdf"
DEMO_PDF_PASSWORD = DEMO_DIR / "PesaScore_DEMO_synthetic_statement_pw1234.pdf"


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return self._rows


class FakeConn:
    """Records every statement; answers selects from a list of canned results
    matched by a substring of the SQL."""

    def __init__(self, answers: dict[str, list] | None = None):
        self.answers = answers or {}
        self.calls: list[tuple[str, tuple]] = []

    async def execute(self, sql, params=()):
        self.calls.append((sql, params))
        for needle, rows in self.answers.items():
            if needle in sql:
                return FakeResult(rows)
        return FakeResult([])


def fake_db_conn(conn: FakeConn):
    @asynccontextmanager
    async def _db_conn(_user_id):
        yield conn

    return _db_conn
