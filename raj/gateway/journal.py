"""Durable local execution journal (SQLite, synchronous=FULL).

Every write is recorded as INTENT before Tally is contacted, then SENT, then a final state.
On restart any SENT row becomes UNKNOWN and blocks further writes until reconciled.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS journal (
  nonce TEXT PRIMARY KEY,           -- execution envelope nonce: single use
  job_id TEXT NOT NULL, row_id TEXT NOT NULL, approval_id TEXT NOT NULL,
  ref TEXT NOT NULL, voucher_date TEXT NOT NULL, payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
  state TEXT NOT NULL,              -- INTENT SENT VERIFIED REJECTED UNKNOWN NOT_POSTED MISMATCH
  tally_id TEXT, detail TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS journal_state ON journal(state);
CREATE INDEX IF NOT EXISTS journal_row ON journal(row_id);
CREATE TABLE IF NOT EXISTS audit (ts REAL NOT NULL, event TEXT NOT NULL, detail TEXT);
"""

BLOCKING = ("INTENT", "SENT", "UNKNOWN", "MISMATCH")


class Journal:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript(SCHEMA)
        self.lock = threading.RLock()
        # crash recovery: anything not finished is unknown until reconciled against Tally
        with self.lock:
            self.db.execute("UPDATE journal SET state='UNKNOWN', updated_at=? WHERE state IN ('INTENT','SENT')",
                            (time.time(),))

    def audit(self, event: str, **detail) -> None:
        with self.lock:
            self.db.execute("INSERT INTO audit VALUES (?,?,?)", (time.time(), event, json.dumps(detail, default=str)))

    def nonce_used(self, nonce: str) -> bool:
        return self.db.execute("SELECT 1 FROM journal WHERE nonce=?", (nonce,)).fetchone() is not None

    def row_done(self, row_id: str) -> dict | None:
        r = self.db.execute("SELECT * FROM journal WHERE row_id=? AND state IN ('VERIFIED') LIMIT 1", (row_id,)).fetchone()
        return self._d(r) if r else None

    def blocking(self) -> list[dict]:
        rows = self.db.execute(f"SELECT * FROM journal WHERE state IN ({','.join('?' * len(BLOCKING))})", BLOCKING).fetchall()
        return [self._d(r) for r in rows]

    def intent(self, *, nonce, job_id, row_id, approval_id, ref, voucher_date, payload, payload_sha256) -> None:
        now = time.time()
        with self.lock:
            self.db.execute("INSERT INTO journal VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (nonce, job_id, row_id, approval_id, ref, voucher_date, json.dumps(payload, ensure_ascii=False),
                             payload_sha256, "INTENT", None, None, now, now))

    def set(self, nonce: str, state: str, tally_id: str | None = None, detail: str | None = None) -> None:
        with self.lock:
            self.db.execute("UPDATE journal SET state=?, tally_id=COALESCE(?, tally_id), detail=?, updated_at=? WHERE nonce=?",
                            (state, tally_id, detail, time.time(), nonce))

    def get(self, nonce: str) -> dict | None:
        r = self.db.execute("SELECT * FROM journal WHERE nonce=?", (nonce,)).fetchone()
        return self._d(r) if r else None

    def by_row(self, row_id: str) -> list[dict]:
        return [self._d(r) for r in self.db.execute("SELECT * FROM journal WHERE row_id=? ORDER BY created_at", (row_id,))]

    def recent(self, n: int = 50) -> list[dict]:
        return [self._d(r) for r in self.db.execute("SELECT * FROM journal ORDER BY created_at DESC LIMIT ?", (n,))]

    def _d(self, r) -> dict:
        cols = [c[0] for c in self.db.execute("SELECT * FROM journal LIMIT 0").description]
        d = dict(zip(cols, r))
        d["payload"] = json.loads(d["payload"])
        return d
