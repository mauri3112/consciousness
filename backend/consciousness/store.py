from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import (
    AuditorRecap,
    IntegrationStatus,
    ModelProfile,
    ProcedureSnapshot,
    ProcedureState,
    RunRecord,
    RunStatus,
    RunOutput,
    Transition,
)
from .guardrails import default_guardrails
from .seed import STARTER_MODELS, STARTER_STATES, STARTER_TRANSITIONS


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class ConsciousnessStore:
    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path)

    def connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    def setup(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            self._ensure_columns(conn)
            self._seed_if_empty(conn)

    def snapshot(self) -> ProcedureSnapshot:
        return ProcedureSnapshot(
            states=self.list_states(),
            transitions=self.list_transitions(),
            models=self.list_models(),
            runs=self.list_runs(limit=20),
            recaps=self.list_recaps(limit=20),
            integrations=self.list_integrations(),
            guardrails=default_guardrails(),
        )

    def list_states(self) -> list[ProcedureState]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM procedure_states ORDER BY name").fetchall()
        return [_state_from_row(row) for row in rows]

    def current_state(self) -> ProcedureState:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM procedure_states WHERE is_current = 1 LIMIT 1").fetchone()
            if row is None:
                row = conn.execute("SELECT * FROM procedure_states ORDER BY name LIMIT 1").fetchone()
        if row is None:
            raise RuntimeError("procedure has no states")
        return _state_from_row(row)

    def get_state(self, state_id: str) -> ProcedureState:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM procedure_states WHERE id = ?", (state_id,)).fetchone()
        if row is None:
            raise KeyError(state_id)
        return _state_from_row(row)

    def set_current_state(self, state_id: str) -> ProcedureState:
        with self.connect() as conn:
            existing = conn.execute("SELECT id FROM procedure_states WHERE id = ?", (state_id,)).fetchone()
            if existing is None:
                raise KeyError(state_id)
            conn.execute("UPDATE procedure_states SET is_current = 0")
            conn.execute("UPDATE procedure_states SET is_current = 1, updated_at = ? WHERE id = ?", (_now_iso(), state_id))
        return self.get_state(state_id)

    def list_transitions(self) -> list[Transition]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM transitions ORDER BY source_id, weight DESC").fetchall()
        return [_transition_from_row(row) for row in rows]

    def next_transition(self, source_id: str) -> Transition:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM transitions
                WHERE source_id = ? AND active = 1
                ORDER BY weight DESC, target_id
                LIMIT 1
                """,
                (source_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError(f"state {source_id!r} has no active outgoing transition")
        return _transition_from_row(row)

    def list_models(self) -> list[ModelProfile]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM model_profiles WHERE enabled = 1 ORDER BY relative_cost, quality_tier").fetchall()
        return [_model_from_row(row) for row in rows]

    def begin_run(self, state: ProcedureState, model: ModelProfile) -> RunRecord:
        now = utcnow()
        run = RunRecord(
            id=make_id("run"),
            state_id=state.id,
            goal=state.goal_template,
            status=RunStatus.running,
            model_id=model.id,
            context_window=model.context_window,
            context_used=0,
            started_at=now,
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO runs (
                  id, state_id, goal, status, model_id, context_window, context_used,
                  started_at, finished_at, final_thoughts, changes_json, output_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    run.state_id,
                    run.goal,
                    run.status.value,
                    run.model_id,
                    run.context_window,
                    run.context_used,
                    run.started_at.isoformat(),
                    None,
                    None,
                    "[]",
                    "{}",
                ),
            )
        return run

    def finish_run(
        self,
        run_id: str,
        *,
        status: RunStatus,
        context_used: int,
        final_thoughts: str,
        changes: list[dict[str, Any]],
        output: RunOutput,
    ) -> RunRecord:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            conn.execute(
                """
                UPDATE runs
                SET status = ?, context_used = ?, finished_at = ?, final_thoughts = ?, changes_json = ?, output_json = ?
                WHERE id = ?
                """,
                (
                    status.value,
                    context_used,
                    _now_iso(),
                    final_thoughts,
                    json.dumps(changes),
                    output.model_dump_json(),
                    run_id,
                ),
            )
            updated = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return _run_from_row(updated)

    def list_runs(self, limit: int = 50) -> list[RunRecord]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
        return [_run_from_row(row) for row in rows]

    def add_recap(
        self,
        *,
        run_id: str | None,
        auditor_model_id: str,
        summary: str,
        decision: str,
        procedure_changes: list[dict[str, Any]] | None = None,
    ) -> AuditorRecap:
        recap = AuditorRecap(
            id=make_id("recap"),
            run_id=run_id,
            auditor_model_id=auditor_model_id,
            summary=summary,
            decision=decision,
            procedure_changes=procedure_changes or [],
            created_at=utcnow(),
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO auditor_recaps (
                  id, run_id, auditor_model_id, summary, decision,
                  procedure_changes_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    recap.id,
                    recap.run_id,
                    recap.auditor_model_id,
                    recap.summary,
                    recap.decision,
                    json.dumps(recap.procedure_changes),
                    recap.created_at.isoformat(),
                ),
            )
        return recap

    def list_recaps(self, limit: int = 50) -> list[AuditorRecap]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM auditor_recaps ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [_recap_from_row(row) for row in rows]

    def upsert_integration(self, status: IntegrationStatus) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO integration_status (name, status, endpoint, last_checked_at, details_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                  status = excluded.status,
                  endpoint = excluded.endpoint,
                  last_checked_at = excluded.last_checked_at,
                  details_json = excluded.details_json
                """,
                (
                    status.name,
                    status.status,
                    status.endpoint,
                    status.last_checked_at.isoformat() if status.last_checked_at else None,
                    json.dumps(status.details),
                ),
            )

    def list_integrations(self) -> list[IntegrationStatus]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM integration_status ORDER BY name").fetchall()
        return [_integration_from_row(row) for row in rows]

    def _ensure_columns(self, conn: sqlite3.Connection) -> None:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(runs)").fetchall()}
        if "output_json" not in columns:
            conn.execute("ALTER TABLE runs ADD COLUMN output_json TEXT NOT NULL DEFAULT '{}'")

    def _seed_if_empty(self, conn: sqlite3.Connection) -> None:
        state_count = conn.execute("SELECT COUNT(*) FROM procedure_states").fetchone()[0]
        if state_count:
            return
        now = _now_iso()
        for state in STARTER_STATES:
            conn.execute(
                """
                INSERT INTO procedure_states (
                  id, name, kind, domain, goal_template, prompt_contract, output_contract,
                  tools_json, skills_json, context_minimum, model_policy, x, y, is_current,
                  created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    state["id"],
                    state["name"],
                    state["kind"],
                    state["domain"],
                    state["goal_template"],
                    state["prompt_contract"],
                    state["output_contract"],
                    json.dumps(state["tools"]),
                    json.dumps(state["skills"]),
                    state["context_minimum"],
                    state["model_policy"],
                    state["x"],
                    state["y"],
                    1 if state.get("is_current") else 0,
                    now,
                    now,
                ),
            )
        for source_id, target_id, weight, rationale in STARTER_TRANSITIONS:
            conn.execute(
                """
                INSERT INTO transitions (id, source_id, target_id, weight, guard, rationale, active)
                VALUES (?, ?, ?, ?, ?, ?, 1)
                """,
                (f"{source_id}_to_{target_id}", source_id, target_id, weight, "always", rationale),
            )
        for model in STARTER_MODELS:
            conn.execute(
                """
                INSERT INTO model_profiles (
                  id, provider, model, context_window, relative_cost, max_run_budget,
                  quality_tier, strengths_json, open_weights, enabled
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    model["id"],
                    model["provider"],
                    model["model"],
                    model["context_window"],
                    model["relative_cost"],
                    model["max_run_budget"],
                    model["quality_tier"],
                    json.dumps(model["strengths"]),
                    1 if model.get("open_weights") else 0,
                ),
            )
        conn.execute(
            """
            INSERT INTO integration_status (name, status, endpoint, last_checked_at, details_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("only-memories", "not_checked", "http://localhost:8765", now, json.dumps({"mode": "optional"})),
        )
        conn.execute(
            """
            INSERT INTO auditor_recaps (
              id, run_id, auditor_model_id, summary, decision,
              procedure_changes_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "recap_bootstrap",
                None,
                "bootstrap",
                "Starter procedure installed with gather, curate, synthesize, validate, publish, and audit states.",
                "ready",
                "[]",
                now,
            ),
        )


SCHEMA = """
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
  state_id TEXT NOT NULL REFERENCES procedure_states(id),
  goal TEXT NOT NULL,
  status TEXT NOT NULL,
  model_id TEXT NOT NULL,
  context_window INTEGER NOT NULL,
  context_used INTEGER NOT NULL DEFAULT 0,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  final_thoughts TEXT,
  changes_json TEXT NOT NULL,
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


def _now_iso() -> str:
    return utcnow().isoformat()


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _json(value: str) -> Any:
    return json.loads(value)


def _state_from_row(row: sqlite3.Row) -> ProcedureState:
    return ProcedureState(
        id=row["id"],
        name=row["name"],
        kind=row["kind"],
        domain=row["domain"],
        goal_template=row["goal_template"],
        prompt_contract=row["prompt_contract"],
        output_contract=row["output_contract"],
        tools=_json(row["tools_json"]),
        skills=_json(row["skills_json"]),
        context_minimum=row["context_minimum"],
        model_policy=row["model_policy"],
        x=row["x"],
        y=row["y"],
        is_current=bool(row["is_current"]),
    )


def _transition_from_row(row: sqlite3.Row) -> Transition:
    return Transition(
        id=row["id"],
        source_id=row["source_id"],
        target_id=row["target_id"],
        weight=row["weight"],
        guard=row["guard"],
        rationale=row["rationale"],
        active=bool(row["active"]),
    )


def _model_from_row(row: sqlite3.Row) -> ModelProfile:
    return ModelProfile(
        id=row["id"],
        provider=row["provider"],
        model=row["model"],
        context_window=row["context_window"],
        relative_cost=row["relative_cost"],
        max_run_budget=row["max_run_budget"],
        quality_tier=row["quality_tier"],
        strengths=_json(row["strengths_json"]),
        open_weights=bool(row["open_weights"]),
        enabled=bool(row["enabled"]),
    )


def _run_from_row(row: sqlite3.Row) -> RunRecord:
    output_payload = _json(row["output_json"]) if "output_json" in row.keys() else {}
    return RunRecord(
        id=row["id"],
        state_id=row["state_id"],
        goal=row["goal"],
        status=row["status"],
        model_id=row["model_id"],
        context_window=row["context_window"],
        context_used=row["context_used"],
        started_at=_parse_datetime(row["started_at"]) or utcnow(),
        finished_at=_parse_datetime(row["finished_at"]),
        final_thoughts=row["final_thoughts"],
        changes=_json(row["changes_json"]),
        output=RunOutput(**output_payload) if output_payload else None,
    )


def _recap_from_row(row: sqlite3.Row) -> AuditorRecap:
    return AuditorRecap(
        id=row["id"],
        run_id=row["run_id"],
        auditor_model_id=row["auditor_model_id"],
        summary=row["summary"],
        decision=row["decision"],
        procedure_changes=_json(row["procedure_changes_json"]),
        created_at=_parse_datetime(row["created_at"]) or utcnow(),
    )


def _integration_from_row(row: sqlite3.Row) -> IntegrationStatus:
    return IntegrationStatus(
        name=row["name"],
        status=row["status"],
        endpoint=row["endpoint"],
        last_checked_at=_parse_datetime(row["last_checked_at"]),
        details=_json(row["details_json"]),
    )
