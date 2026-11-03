"""Central product state (SQLite for single-host deployments; schema kept Postgres-compatible)."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS orgs (id TEXT PRIMARY KEY, name TEXT NOT NULL, settings TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY, org_id TEXT NOT NULL REFERENCES orgs(id), email TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
  pw_hash TEXT NOT NULL, role TEXT NOT NULL CHECK (role IN ('admin','preparer','reviewer','viewer')),
  active INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS clients (id TEXT PRIMARY KEY, org_id TEXT NOT NULL, name TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS client_access (user_id TEXT NOT NULL, client_id TEXT NOT NULL, PRIMARY KEY (user_id, client_id));
CREATE TABLE IF NOT EXISTS enrollments (
  code_hash TEXT PRIMARY KEY, org_id TEXT NOT NULL, client_id TEXT NOT NULL, created_by TEXT NOT NULL,
  expires_at REAL NOT NULL, used_at REAL, device_id TEXT);
CREATE TABLE IF NOT EXISTS devices (
  id TEXT PRIMARY KEY, org_id TEXT NOT NULL, client_id TEXT NOT NULL, hostname TEXT,
  company_name TEXT NOT NULL, company_guid TEXT NOT NULL, books_from TEXT,
  ssh_pubkey TEXT, tunnel_port INTEGER UNIQUE, gateway_url TEXT, cert_fingerprint TEXT NOT NULL,
  write_enabled INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'active', created_at REAL NOT NULL,
  last_health TEXT, last_seen REAL);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, org_id TEXT NOT NULL, client_id TEXT NOT NULL, device_id TEXT, type TEXT NOT NULL,
  state TEXT NOT NULL, initiated_by TEXT NOT NULL, request TEXT NOT NULL, result TEXT, created_at REAL NOT NULL,
  updated_at REAL NOT NULL, versions TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS job_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, ts REAL NOT NULL, actor TEXT NOT NULL,
  event TEXT NOT NULL, detail TEXT);
CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY, job_id TEXT NOT NULL, client_id TEXT NOT NULL, filename TEXT NOT NULL, sha256 TEXT NOT NULL,
  path TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS drafts (
  id TEXT PRIMARY KEY, job_id TEXT NOT NULL, client_id TEXT NOT NULL, version INTEGER NOT NULL,
  bank_ledger TEXT NOT NULL, created_by TEXT NOT NULL, summary TEXT, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS draft_rows (
  id TEXT PRIMARY KEY, draft_id TEXT NOT NULL, row_no INTEGER NOT NULL, source TEXT NOT NULL,
  match_status TEXT NOT NULL, suggestion TEXT, voucher TEXT, errors TEXT, include INTEGER NOT NULL DEFAULT 0,
  post_status TEXT NOT NULL DEFAULT 'draft', tally_id TEXT, readback TEXT, edited_by TEXT, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY, job_id TEXT NOT NULL, draft_id TEXT NOT NULL, draft_version INTEGER NOT NULL,
  row_id TEXT NOT NULL, payload_sha256 TEXT NOT NULL, envelope TEXT NOT NULL, reviewer_id TEXT NOT NULL,
  expires_at REAL NOT NULL, used_at REAL, revoked INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS execution_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, row_id TEXT NOT NULL, approval_id TEXT NOT NULL,
  ts REAL NOT NULL, outcome TEXT NOT NULL, detail TEXT);
CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY, job_id TEXT NOT NULL, client_id TEXT NOT NULL, kind TEXT NOT NULL, content TEXT NOT NULL,
  created_at REAL NOT NULL);
"""


class DB:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.lock = threading.RLock()

    def x(self, sql: str, args: tuple | list = ()) -> sqlite3.Cursor:
        with self.lock:
            return self.conn.execute(sql, args)

    def one(self, sql: str, args: tuple | list = ()) -> dict | None:
        with self.lock:
            r = self.conn.execute(sql, args).fetchone()
        return dict(r) if r else None

    def all(self, sql: str, args: tuple | list = ()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def tx(self):
        return _Tx(self)

    # ---------------------------------------------------------------- helpers
    def event(self, job_id: str, actor: str, event: str, **detail: Any) -> None:
        self.x("INSERT INTO job_events (job_id, ts, actor, event, detail) VALUES (?,?,?,?,?)",
               (job_id, time.time(), actor, event, json.dumps(detail, ensure_ascii=False, default=str)))

    def set_state(self, job_id: str, state: str, actor: str = "system", **detail: Any) -> None:
        self.event(job_id, actor, f"state:{state}", **detail)  # event first: persisted before effects
        self.x("UPDATE jobs SET state=?, updated_at=? WHERE id=?", (state, time.time(), job_id))


class _Tx:
    def __init__(self, db: DB):
        self.db = db

    def __enter__(self):
        self.db.lock.acquire()
        self.db.conn.execute("BEGIN IMMEDIATE")
        return self.db

    def __exit__(self, et, ev, tb):
        try:
            self.db.conn.execute("ROLLBACK" if et else "COMMIT")
        finally:
            self.db.lock.release()
