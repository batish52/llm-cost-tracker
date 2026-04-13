from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS pricing_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pricing_snapshot_id TEXT UNIQUE NOT NULL,
  provider TEXT NOT NULL,
  model_name TEXT NOT NULL,
  input_price_per_1m_tokens REAL NOT NULL,
  output_price_per_1m_tokens REAL NOT NULL,
  cached_input_price_per_1m_tokens REAL,
  currency TEXT NOT NULL,
  effective_from REAL,
  effective_to REAL,
  source TEXT,
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS request_usage_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id TEXT NOT NULL,
  timestamp REAL NOT NULL,
  session_key TEXT,
  route_mode TEXT NOT NULL,
  intercept_class TEXT,
  provider TEXT,
  model_name TEXT,
  counterfactual_model TEXT,
  pricing_snapshot_id TEXT,
  prompt_tokens INTEGER NOT NULL DEFAULT 0,
  completion_tokens INTEGER NOT NULL DEFAULT 0,
  total_tokens INTEGER NOT NULL DEFAULT 0,
  cost_usd REAL NOT NULL DEFAULT 0,
  counterfactual_prompt_tokens INTEGER NOT NULL DEFAULT 0,
  counterfactual_completion_tokens INTEGER NOT NULL DEFAULT 0,
  counterfactual_total_tokens INTEGER NOT NULL DEFAULT 0,
  saved_prompt_only_usd REAL NOT NULL DEFAULT 0,
  saved_full_modeled_usd REAL NOT NULL DEFAULT 0,
  estimation_method TEXT,
  metadata_json TEXT,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_ledger_created ON request_usage_ledger(created_at);
CREATE INDEX IF NOT EXISTS idx_usage_ledger_session ON request_usage_ledger(session_key);

CREATE TABLE IF NOT EXISTS cost_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id TEXT UNIQUE NOT NULL,
  job_name TEXT NOT NULL,
  started_at REAL NOT NULL,
  finished_at REAL NOT NULL,
  status TEXT NOT NULL,
  local_count INTEGER NOT NULL DEFAULT 0,
  external_count INTEGER NOT NULL DEFAULT 0,
  external_prompt_tokens INTEGER NOT NULL DEFAULT 0,
  external_completion_tokens INTEGER NOT NULL DEFAULT 0,
  external_cost_usd REAL NOT NULL DEFAULT 0,
  saved_prompt_only_usd REAL NOT NULL DEFAULT 0,
  saved_full_modeled_usd REAL NOT NULL DEFAULT 0,
  net_savings_conservative_usd REAL NOT NULL DEFAULT 0,
  net_savings_modeled_usd REAL NOT NULL DEFAULT 0,
  pricing_snapshot_ids TEXT,
  notes TEXT,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cost_snapshots_created ON cost_snapshots(created_at);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=15.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=15000;")
    conn.execute("BEGIN;")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn
