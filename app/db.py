"""SQLite via the standard library: zero setup, one file, plain SQL that is easy to read and to move to Postgres.
Every query is parameterised. A new connection per request keeps things simple and thread-safe."""
import hashlib
import json
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS wedding (id INTEGER PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS member (
  id INTEGER PRIMARY KEY, wedding_id INTEGER NOT NULL REFERENCES wedding(id) ON DELETE CASCADE,
  name TEXT NOT NULL, role TEXT NOT NULL CHECK (role IN ('bride','bridesmaid','family')),
  token_hash TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS ceremony (
  id INTEGER PRIMARY KEY, wedding_id INTEGER NOT NULL REFERENCES wedding(id) ON DELETE CASCADE,
  name TEXT NOT NULL, date TEXT NOT NULL, sort INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS task (
  id INTEGER PRIMARY KEY, wedding_id INTEGER NOT NULL REFERENCES wedding(id) ON DELETE CASCADE,
  ceremony_id INTEGER REFERENCES ceremony(id) ON DELETE SET NULL,
  title TEXT NOT NULL, description TEXT, status TEXT NOT NULL DEFAULT 'todo' CHECK (status IN ('todo','in_progress','done','blocked')),
  duration_days INTEGER NOT NULL DEFAULT 1 CHECK (duration_days >= 0),
  due_date TEXT, not_before TEXT, completed_on TEXT,
  assignee_id INTEGER REFERENCES member(id) ON DELETE SET NULL,
  visibility TEXT NOT NULL DEFAULT 'team' CHECK (visibility IN ('private','team','family')),
  amount REAL CHECK (amount IS NULL OR amount >= 0), notes_private TEXT,
  expedite_days INTEGER NOT NULL DEFAULT 0, expedite_cost REAL NOT NULL DEFAULT 0,
  is_decision INTEGER NOT NULL DEFAULT 0, source_doc_id INTEGER, evidence TEXT,
  created_by INTEGER REFERENCES member(id), created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dependency (
  task_id INTEGER NOT NULL REFERENCES task(id) ON DELETE CASCADE,
  depends_on_id INTEGER NOT NULL REFERENCES task(id) ON DELETE CASCADE,
  PRIMARY KEY (task_id, depends_on_id), CHECK (task_id <> depends_on_id));
CREATE TABLE IF NOT EXISTS document (
  id INTEGER PRIMARY KEY, wedding_id INTEGER NOT NULL REFERENCES wedding(id) ON DELETE CASCADE,
  filename TEXT NOT NULL, mime TEXT NOT NULL, path TEXT NOT NULL, size INTEGER NOT NULL,
  uploaded_by INTEGER REFERENCES member(id), created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS proposal (
  id INTEGER PRIMARY KEY, wedding_id INTEGER NOT NULL REFERENCES wedding(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('extraction','recovery')),
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected','partial')),
  payload TEXT NOT NULL, doc_id INTEGER, created_by INTEGER REFERENCES member(id),
  decided_by INTEGER REFERENCES member(id), created_at TEXT NOT NULL, decided_at TEXT);
CREATE TABLE IF NOT EXISTS approval (
  id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES task(id) ON DELETE CASCADE,
  requested_from INTEGER NOT NULL REFERENCES member(id) ON DELETE CASCADE,
  requested_by INTEGER REFERENCES member(id), question TEXT,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
  comment TEXT, created_at TEXT NOT NULL, decided_at TEXT);
CREATE TABLE IF NOT EXISTS activity (
  id INTEGER PRIMARY KEY, wedding_id INTEGER NOT NULL, actor_id INTEGER, action TEXT NOT NULL,
  entity TEXT NOT NULL, entity_id INTEGER, detail TEXT, ts TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_task_wedding ON task(wedding_id);
CREATE INDEX IF NOT EXISTS idx_activity_wedding ON activity(wedding_id, id);
"""


def db_path():
    return Path(os.getenv("PELLIPLAN_DB", ROOT / "data" / "pelliplan.db"))


def connect():
    p = db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, check_same_thread=False)   # one connection per request, used one step at a time
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def new_token():
    return secrets.token_urlsafe(24)


def one(conn, sql, params=()):
    r = conn.execute(sql, params).fetchone()
    return dict(r) if r else None


def many(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def log(conn, wedding_id, actor_id, action, entity, entity_id=None, **detail):
    """Append-only activity history. Called by every endpoint that changes anything."""
    conn.execute("INSERT INTO activity (wedding_id, actor_id, action, entity, entity_id, detail, ts) VALUES (?,?,?,?,?,?,?)",
                 (wedding_id, actor_id, action, entity, entity_id, json.dumps(detail, default=str, ensure_ascii=False), now()))
