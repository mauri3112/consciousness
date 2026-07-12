from __future__ import annotations

import sqlite3
from collections.abc import Callable


Migration = tuple[int, str, Callable[[sqlite3.Connection], None]]


BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS procedure_states (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,
  domain TEXT NOT NULL,
  goal_template TEXT NOT NULL,
  prompt_contract TEXT NOT NULL,
  output_contract TEXT NOT NULL,
  tools_json TEXT NOT NULL,
  skills_json TEXT NOT NULL,
  context_minimum INTEGER NOT NULL,
  model_policy TEXT NOT NULL,
  x REAL NOT NULL,
  y REAL NOT NULL,
  is_current INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transitions (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES procedure_states(id),
  target_id TEXT NOT NULL REFERENCES procedure_states(id),
  weight REAL NOT NULL,
  guard TEXT NOT NULL,
  rationale TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS model_profiles (
  id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  context_window INTEGER NOT NULL,
  relative_cost REAL NOT NULL,
  max_run_budget REAL NOT NULL,
  quality_tier INTEGER NOT NULL,
  strengths_json TEXT NOT NULL,
  open_weights INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  state_id TEXT NOT NULL,
  goal TEXT NOT NULL,
  status TEXT NOT NULL,
  model_id TEXT NOT NULL,
  context_window INTEGER NOT NULL,
  context_used INTEGER NOT NULL DEFAULT 0,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  final_thoughts TEXT,
  changes_json TEXT NOT NULL DEFAULT '[]',
  output_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS auditor_recaps (
  id TEXT PRIMARY KEY,
  run_id TEXT REFERENCES runs(id),
  auditor_model_id TEXT NOT NULL,
  summary TEXT NOT NULL,
  decision TEXT NOT NULL,
  procedure_changes_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS integration_status (
  name TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  endpoint TEXT,
  last_checked_at TEXT,
  details_json TEXT NOT NULL
);
"""


V1_SCHEMA = """
CREATE TABLE IF NOT EXISTS procedure_versions (
  id TEXT PRIMARY KEY,
  version INTEGER NOT NULL UNIQUE,
  status TEXT NOT NULL CHECK(status IN ('draft', 'active', 'superseded')),
  digest TEXT NOT NULL,
  parent_id TEXT REFERENCES procedure_versions(id),
  revision INTEGER NOT NULL DEFAULT 1,
  definition_json TEXT NOT NULL,
  created_by_run_id TEXT,
  created_at TEXT NOT NULL,
  activated_at TEXT
);

CREATE TABLE IF NOT EXISTS procedure_runtime (
  singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
  active_version_id TEXT NOT NULL REFERENCES procedure_versions(id),
  current_state_id TEXT NOT NULL,
  status TEXT NOT NULL,
  interval_seconds INTEGER NOT NULL,
  worker_id TEXT,
  lease_expires_at TEXT,
  heartbeat_at TEXT,
  failure_count INTEGER NOT NULL DEFAULT 0,
  backoff_until TEXT,
  daily_budget_cap REAL NOT NULL DEFAULT 5,
  execution_mode TEXT NOT NULL DEFAULT 'preview',
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runtime_commands (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  claimed_at TEXT,
  completed_at TEXT,
  error TEXT
);

CREATE TABLE IF NOT EXISTS run_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT REFERENCES runs(id),
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_calls (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id),
  tool_name TEXT NOT NULL,
  status TEXT NOT NULL,
  mutation_level TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  arguments_json TEXT NOT NULL,
  result_json TEXT,
  approval_id TEXT,
  started_at TEXT NOT NULL,
  finished_at TEXT
);

CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id),
  label TEXT NOT NULL,
  kind TEXT NOT NULL,
  uri TEXT NOT NULL UNIQUE,
  path TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  mime_type TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL REFERENCES runs(id),
  provider TEXT NOT NULL,
  model_id TEXT NOT NULL,
  input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  cached_tokens INTEGER NOT NULL DEFAULT 0,
  cost REAL NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY,
  run_id TEXT REFERENCES runs(id),
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  risk TEXT NOT NULL,
  proposed_action_json TEXT NOT NULL,
  evidence_json TEXT NOT NULL DEFAULT '[]',
  requested_at TEXT NOT NULL,
  decided_at TEXT,
  decision_note TEXT
);

CREATE TABLE IF NOT EXISTS procedure_mutations (
  id TEXT PRIMARY KEY,
  base_version_id TEXT NOT NULL REFERENCES procedure_versions(id),
  proposed_version_id TEXT NOT NULL REFERENCES procedure_versions(id),
  proposer_run_id TEXT REFERENCES runs(id),
  status TEXT NOT NULL,
  diff_text TEXT NOT NULL,
  rationale TEXT NOT NULL,
  budget_impact_json TEXT NOT NULL DEFAULT '{}',
  rollback_version_id TEXT NOT NULL REFERENCES procedure_versions(id),
  created_at TEXT NOT NULL,
  decided_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_state ON runs(state_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_run ON run_events(run_id, id);
CREATE INDEX IF NOT EXISTS idx_events_created ON run_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_commands_status ON runtime_commands(status, id);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status, requested_at DESC);
CREATE INDEX IF NOT EXISTS idx_usage_created ON usage_ledger(created_at DESC);
"""


RUN_COLUMNS: dict[str, str] = {
    "procedure_version_id": "TEXT NOT NULL DEFAULT ''",
    "attempt": "INTEGER NOT NULL DEFAULT 1",
    "provider": "TEXT NOT NULL DEFAULT 'preview'",
    "provider_request_id": "TEXT",
    "input_tokens": "INTEGER NOT NULL DEFAULT 0",
    "output_tokens": "INTEGER NOT NULL DEFAULT 0",
    "cached_tokens": "INTEGER NOT NULL DEFAULT 0",
    "cost": "REAL NOT NULL DEFAULT 0",
    "context_manifest_json": "TEXT NOT NULL DEFAULT '{}'",
    "agent_access_json": "TEXT NOT NULL DEFAULT '{}'",
    "heartbeat_at": "TEXT",
    "error_category": "TEXT",
    "error_message": "TEXT",
}


def _migration_1(conn: sqlite3.Connection) -> None:
    conn.executescript(BASE_SCHEMA)


def _migration_2(conn: sqlite3.Connection) -> None:
    conn.executescript(V1_SCHEMA)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(runs)").fetchall()}
    for name, declaration in RUN_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE runs ADD COLUMN {name} {declaration}")


def _migration_3(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(runs)").fetchall()}
    if "agent_access_json" not in existing:
        conn.execute("ALTER TABLE runs ADD COLUMN agent_access_json TEXT NOT NULL DEFAULT '{}'")


MIGRATIONS: list[Migration] = [
    (1, "bootstrap scaffold schema", _migration_1),
    (2, "durable local v1 runtime and evidence", _migration_2),
    (3, "pin resolved agent access on every run", _migration_3),
]


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          version INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          applied_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()}
        for version, name, migration in MIGRATIONS:
            if version in applied:
                continue
            migration(conn)
            conn.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, datetime('now'))",
                (version, name),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
