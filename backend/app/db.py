"""Tiny SQLite layer. One connection per call keeps it thread-safe for agent threads."""
import json
import sqlite3
import threading
from contextlib import contextmanager

from .config import DB_PATH

_write_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);

-- ===== Merchants (DisputeSaathi side) =====
CREATE TABLE IF NOT EXISTS merchants (
  id TEXT PRIMARY KEY, name TEXT, business TEXT, category TEXT, phone TEXT, language TEXT,
  trust_level TEXT,              -- ASK_FIRST | AUTO
  auto_send_limit REAL,          -- max amount for auto-send when trust_level = AUTO
  deadline_rule_enabled INTEGER, -- "if I don't reply and all proof is there, fight for me"
  deadline_rule_limit REAL,      -- amount cap for that rule (fix #1)
  online INTEGER,                -- simulated network reachability
  good_cases INTEGER DEFAULT 0
);

-- ===== Mock Paytm systems =====
CREATE TABLE IF NOT EXISTS paytm_payments (
  txn_id TEXT PRIMARY KEY, merchant_id TEXT, order_id TEXT, amount REAL, method TEXT,
  customer_vpa TEXT, customer_phone TEXT, status TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS paytm_disputes (
  dispute_id TEXT PRIMARY KEY, txn_id TEXT, merchant_id TEXT, reason_code TEXT,
  customer_message TEXT, amount REAL, deadline_hours REAL, raised_at TEXT, deadline_at TEXT,
  status TEXT,                   -- NOT_RAISED | OPEN | DEFENDED | ACCEPTED | WON | LOST | PRE_ARB
  submission TEXT, idempotency_key TEXT,
  scenario_title TEXT, scenario_expect TEXT, scenario_order INTEGER, featured INTEGER DEFAULT 0
);

-- ===== Mock merchant systems =====
CREATE TABLE IF NOT EXISTS orders (
  order_id TEXT PRIMARY KEY, merchant_id TEXT, txn_id TEXT, items TEXT, amount REAL,
  customer_name TEXT, address TEXT, created_at TEXT, kind TEXT, notes TEXT
);
CREATE TABLE IF NOT EXISTS invoices (
  invoice_id TEXT PRIMARY KEY, order_id TEXT, merchant_id TEXT, amount REAL, issued_at TEXT,
  text TEXT, image_path TEXT, source TEXT
);
CREATE TABLE IF NOT EXISTS deliveries (
  awb TEXT PRIMARY KEY, order_id TEXT, courier TEXT, status TEXT, delivered_at TEXT,
  pod TEXT, events TEXT
);
CREATE TABLE IF NOT EXISTS refunds (
  refund_id TEXT PRIMARY KEY, txn_id TEXT, amount REAL, status TEXT, processed_at TEXT, rrn TEXT
);
CREATE TABLE IF NOT EXISTS chats (
  id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT, sender TEXT, text TEXT, ts TEXT
);
CREATE TABLE IF NOT EXISTS service_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT, kind TEXT, detail TEXT, ts TEXT
);
CREATE TABLE IF NOT EXISTS sample_bills (
  sha256 TEXT PRIMARY KEY, merchant_id TEXT, order_id TEXT, path TEXT, text TEXT
);

-- ===== DisputeSaathi case tracker =====
CREATE TABLE IF NOT EXISTS cases (
  id TEXT PRIMARY KEY, dispute_id TEXT UNIQUE, merchant_id TEXT, txn_id TEXT, order_id TEXT,
  stage TEXT, reason TEXT, reason_paytm TEXT, reason_note TEXT, amount REAL,
  deadline_at TEXT, created_at TEXT, updated_at TEXT, ready_at TEXT, closed_at TEXT,
  evidence TEXT, checklist TEXT, decision TEXT, pending_action TEXT,
  pack_path TEXT, pack_version INTEGER DEFAULT 0, summary TEXT, handover_note TEXT,
  injection_flags TEXT, ai_mode TEXT, merchant_taps INTEGER DEFAULT 0,
  evidence_requested INTEGER DEFAULT 0, resolved_by TEXT, outcome TEXT, precheck TEXT
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, ts TEXT, sim_ts TEXT, kind TEXT,
  title TEXT, detail TEXT, actor TEXT
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT, merchant_id TEXT, case_id TEXT, channel TEXT,
  direction TEXT, text TEXT, media_path TEXT, status TEXT, ts TEXT
);
CREATE TABLE IF NOT EXISTS approvals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, action TEXT, otp TEXT,
  created_at TEXT, used INTEGER DEFAULT 0
);
"""

JSON_COLS = {"evidence", "checklist", "decision", "pending_action", "injection_flags",
             "detail", "items", "events", "submission", "precheck"}


def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def conn_ctx():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _decode(row):
    if row is None:
        return None
    d = dict(row)
    for k, v in d.items():
        if k in JSON_COLS and isinstance(v, str) and v and v[0] in "[{":
            try:
                d[k] = json.loads(v)
            except ValueError:
                pass
    return d


def _encode(v):
    return json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v


def q(sql, args=()):
    with conn_ctx() as c:
        return [_decode(r) for r in c.execute(sql, args).fetchall()]


def q1(sql, args=()):
    rows = q(sql, args)
    return rows[0] if rows else None


def x(sql, args=()):
    with _write_lock, conn_ctx() as c:
        cur = c.execute(sql, [_encode(a) for a in args])
        return cur.lastrowid


def insert(table, row: dict):
    cols = ",".join(row)
    marks = ",".join("?" for _ in row)
    return x(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(row.values()))


def update(table, key_col, key, fields: dict):
    if not fields:
        return
    sets = ",".join(f"{k}=?" for k in fields)
    x(f"UPDATE {table} SET {sets} WHERE {key_col}=?", [*fields.values(), key])


def init_db(reset=False):
    with _write_lock, conn_ctx() as c:
        if reset:
            tables = [r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            for t in tables:
                c.execute(f"DROP TABLE IF EXISTS {t}")
        c.executescript(SCHEMA)


def get_setting(key, default=None):
    r = q1("SELECT value FROM settings WHERE key=?", (key,))
    return r["value"] if r else default


def set_setting(key, value):
    x("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
      (key, str(value)))
