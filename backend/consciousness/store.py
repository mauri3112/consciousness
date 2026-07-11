from __future__ import annotations

import base64
import difflib
import hashlib
import json
import random
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .graph import choose_transition, validate_procedure
from .guardrails import default_guardrails
from .migrations import migrate
from .models import (
    ApprovalRecord,
    ApprovalStatus,
    ArtifactRecord,
    AuditorRecap,
    CommandKind,
    ContextManifest,
    IntegrationStatus,
    ModelProfile,
    ProcedureDefinition,
    ProcedureMutation,
    ProcedureSnapshot,
    ProcedureState,
    ProcedureVersion,
    RunEvent,
    RunOutput,
    RunRecord,
    RunStatus,
    RuntimeCommand,
    RuntimeState,
    RuntimeStatus,
    SourceLink,
    ToolCallRecord,
    Transition,
)
from .seed import STARTER_MODELS, STARTER_STATES, STARTER_TRANSITIONS


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _load(value: str | None, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


def _encode_cursor(timestamp: str, record_id: str) -> str:
    payload = json.dumps([timestamp, record_id], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[str, str]:
    try:
        padding = "=" * (-len(cursor) % 4)
        timestamp, record_id = json.loads(base64.urlsafe_b64decode(cursor + padding))
        if not isinstance(timestamp, str) or not isinstance(record_id, str):
            raise ValueError
        datetime.fromisoformat(timestamp)
        return timestamp, record_id
    except Exception as exc:
        raise ValueError("invalid_cursor") from exc


class ConsciousnessStore:
    def __init__(self, database_path: Path | str, *, execution_mode: str = "preview") -> None:
        self.database_path = Path(database_path)
        self.execution_mode = execution_mode

    def connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.database_path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def setup(self) -> None:
        with self.connect() as conn:
            migrate(conn)
            self._seed_if_empty(conn)

    def integrity_check(self) -> str:
        with self.connect() as conn:
            return str(conn.execute("PRAGMA integrity_check").fetchone()[0])

    def snapshot(self) -> ProcedureSnapshot:
        version = self.current_version()
        definition = version.definition
        current_id = self.runtime().current_state_id
        states = [state.model_copy(update={"is_current": state.id == current_id}) for state in definition.states]
        return ProcedureSnapshot(
            version=version,
            runtime=self.runtime(),
            states=states,
            transitions=definition.transitions,
            models=[model for model in definition.models if model.enabled],
            runs=self.list_runs(limit=50),
            recaps=self.list_recaps(limit=50),
            integrations=self.list_integrations(),
            guardrails=definition.guardrails,
            approvals=self.list_approvals(limit=50),
            mutations=self.list_mutations(limit=50),
        )

    # Procedure versions -------------------------------------------------

    def current_version(self) -> ProcedureVersion:
        runtime = self.runtime()
        return self.get_version(runtime.active_version_id)

    def get_version(self, version_id: str) -> ProcedureVersion:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM procedure_versions WHERE id = ?", (version_id,)).fetchone()
        if row is None:
            raise KeyError(version_id)
        return _version_from_row(row)

    def list_versions(self) -> list[ProcedureVersion]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM procedure_versions ORDER BY version DESC").fetchall()
        return [_version_from_row(row) for row in rows]

    def create_draft(self, parent_id: str | None = None, *, created_by_run_id: str | None = None) -> ProcedureVersion:
        parent = self.get_version(parent_id) if parent_id else self.current_version()
        now = utcnow()
        draft_id = make_id("procedure")
        with self.connect() as conn:
            next_version = int(conn.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM procedure_versions").fetchone()[0])
            definition_json, digest = _serialize_definition(parent.definition)
            conn.execute(
                """
                INSERT INTO procedure_versions(
                  id, version, status, digest, parent_id, revision, definition_json,
                  created_by_run_id, created_at, activated_at
                ) VALUES (?, ?, 'draft', ?, ?, 1, ?, ?, ?, NULL)
                """,
                (draft_id, next_version, digest, parent.id, definition_json, created_by_run_id, now.isoformat()),
            )
        return self.get_version(draft_id)

    def update_draft(self, version_id: str, definition: ProcedureDefinition, *, expected_revision: int) -> ProcedureVersion:
        version = self.get_version(version_id)
        if version.status != "draft":
            raise ValueError("only draft procedure versions can be edited")
        if version.revision != expected_revision:
            raise RuntimeError("revision_conflict")
        definition_json, digest = _serialize_definition(definition)
        with self.connect() as conn:
            result = conn.execute(
                """
                UPDATE procedure_versions
                SET definition_json = ?, digest = ?, revision = revision + 1
                WHERE id = ? AND revision = ? AND status = 'draft'
                """,
                (definition_json, digest, version_id, expected_revision),
            )
            if result.rowcount != 1:
                raise RuntimeError("revision_conflict")
        return self.get_version(version_id)

    def validate_version(self, version_id: str) -> list[str]:
        return validate_procedure(self.get_version(version_id).definition)

    def activate_version(
        self,
        version_id: str,
        *,
        rationale: str = "operator activation",
        record_mutation: bool = True,
    ) -> ProcedureVersion:
        version = self.get_version(version_id)
        errors = validate_procedure(version.definition)
        if errors:
            raise ValueError("; ".join(errors))
        base = self.current_version()
        now = utcnow()
        current = next(state.id for state in version.definition.states if state.is_current)
        diff = self.diff_versions(base.id, version.id)
        mutation_id = make_id("mutation")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE procedure_versions SET status = 'superseded' WHERE status = 'active'")
            conn.execute(
                "UPDATE procedure_versions SET status = 'active', activated_at = ? WHERE id = ?",
                (now.isoformat(), version.id),
            )
            conn.execute(
                """
                UPDATE procedure_runtime
                SET active_version_id = ?, current_state_id = ?, updated_at = ?
                WHERE singleton = 1
                """,
                (version.id, current, now.isoformat()),
            )
            if base.id != version.id and record_mutation:
                conn.execute(
                    """
                    INSERT INTO procedure_mutations(
                      id, base_version_id, proposed_version_id, proposer_run_id, status,
                      diff_text, rationale, budget_impact_json, rollback_version_id,
                      created_at, decided_at
                    ) VALUES (?, ?, ?, NULL, 'executed', ?, ?, '{}', ?, ?, ?)
                    """,
                    (mutation_id, base.id, version.id, diff, rationale, base.id, now.isoformat(), now.isoformat()),
                )
        self.add_event("procedure.activated", {"version_id": version.id, "previous_version_id": base.id})
        return self.get_version(version.id)

    def rollback(self, version_id: str) -> ProcedureVersion:
        target = self.get_version(version_id)
        if target.status == "draft":
            raise ValueError("cannot roll back to a draft")
        draft = self.create_draft(target.id)
        return self.activate_version(draft.id, rationale=f"rollback to procedure version {target.version}")

    def diff_versions(self, base_id: str, target_id: str) -> str:
        base = json.dumps(self.get_version(base_id).definition.model_dump(mode="json"), indent=2, sort_keys=True).splitlines()
        target = json.dumps(self.get_version(target_id).definition.model_dump(mode="json"), indent=2, sort_keys=True).splitlines()
        return "\n".join(difflib.unified_diff(base, target, fromfile=base_id, tofile=target_id, lineterm=""))

    def list_states(self) -> list[ProcedureState]:
        current_id = self.runtime().current_state_id
        return [
            state.model_copy(update={"is_current": state.id == current_id})
            for state in self.current_version().definition.states
        ]

    def current_state(self) -> ProcedureState:
        return self.get_state(self.runtime().current_state_id)

    def get_state(self, state_id: str) -> ProcedureState:
        for state in self.current_version().definition.states:
            if state.id == state_id:
                return state.model_copy(update={"is_current": state.id == self.runtime().current_state_id})
        raise KeyError(state_id)

    def set_current_state(self, state_id: str) -> ProcedureState:
        state = self.get_state(state_id)
        with self.connect() as conn:
            conn.execute(
                "UPDATE procedure_runtime SET current_state_id = ?, updated_at = ? WHERE singleton = 1",
                (state_id, utcnow().isoformat()),
            )
        return state.model_copy(update={"is_current": True})

    def list_transitions(self) -> list[Transition]:
        return self.current_version().definition.transitions

    def next_transition(self, source_id: str, output: RunOutput | None = None) -> Transition:
        return choose_transition(self.list_transitions(), source_id, output)

    def list_models(self) -> list[ModelProfile]:
        return [model for model in self.current_version().definition.models if model.enabled]

    # Runtime ------------------------------------------------------------

    def runtime(self) -> RuntimeState:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM procedure_runtime WHERE singleton = 1").fetchone()
        if row is None:
            raise RuntimeError("procedure runtime is not initialized")
        return _runtime_from_row(row)

    def set_runtime_status(self, status: RuntimeStatus) -> RuntimeState:
        with self.connect() as conn:
            conn.execute(
                "UPDATE procedure_runtime SET status = ?, updated_at = ? WHERE singleton = 1",
                (status.value, utcnow().isoformat()),
            )
        self.add_event("runtime.status", {"status": status.value})
        return self.runtime()

    def enqueue_command(self, kind: CommandKind, payload: dict[str, Any] | None = None) -> RuntimeCommand:
        now = utcnow()
        with self.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO runtime_commands(kind, status, payload_json, created_at) VALUES (?, 'pending', ?, ?)",
                (kind.value, json.dumps(payload or {}), now.isoformat()),
            )
            command_id = int(cursor.lastrowid)
        self.add_event("command.queued", {"command_id": command_id, "kind": kind.value})
        return self.get_command(command_id)

    def get_command(self, command_id: int) -> RuntimeCommand:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM runtime_commands WHERE id = ?", (command_id,)).fetchone()
        if row is None:
            raise KeyError(command_id)
        return _command_from_row(row)

    def claim_command(self) -> RuntimeCommand | None:
        now = utcnow()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM runtime_commands WHERE status = 'pending' ORDER BY id LIMIT 1").fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE runtime_commands SET status = 'claimed', claimed_at = ? WHERE id = ?",
                (now.isoformat(), row["id"]),
            )
        return self.get_command(int(row["id"]))

    def recover_stale_commands(self, stale_after_seconds: int = 60) -> int:
        """Return commands claimed by a lost worker to the durable queue."""
        cutoff = utcnow() - timedelta(seconds=stale_after_seconds)
        with self.connect() as conn:
            result = conn.execute(
                """
                UPDATE runtime_commands
                SET status = 'pending', claimed_at = NULL,
                    error = 'Recovered after the claiming worker lease expired.'
                WHERE status = 'claimed' AND claimed_at < ?
                """,
                (cutoff.isoformat(),),
            )
        if result.rowcount:
            self.add_event("recovery.commands_requeued", {"count": result.rowcount})
        return result.rowcount

    def complete_command(self, command_id: int, error: str | None = None) -> RuntimeCommand:
        status = "failed" if error else "completed"
        with self.connect() as conn:
            conn.execute(
                "UPDATE runtime_commands SET status = ?, completed_at = ?, error = ? WHERE id = ?",
                (status, utcnow().isoformat(), error, command_id),
            )
        return self.get_command(command_id)

    def acquire_lease(self, worker_id: str, lease_seconds: int = 30) -> bool:
        now = utcnow()
        expires = now + timedelta(seconds=lease_seconds)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT worker_id, lease_expires_at FROM procedure_runtime WHERE singleton = 1").fetchone()
            lease_expiry = _dt(row["lease_expires_at"])
            if row["worker_id"] and row["worker_id"] != worker_id and lease_expiry and lease_expiry > now:
                return False
            conn.execute(
                """
                UPDATE procedure_runtime
                SET worker_id = ?, lease_expires_at = ?, heartbeat_at = ?, updated_at = ?
                WHERE singleton = 1
                """,
                (worker_id, expires.isoformat(), now.isoformat(), now.isoformat()),
            )
        return True

    def renew_lease(self, worker_id: str, lease_seconds: int = 30) -> bool:
        now = utcnow()
        with self.connect() as conn:
            result = conn.execute(
                """
                UPDATE procedure_runtime
                SET lease_expires_at = ?, heartbeat_at = ?, updated_at = ?
                WHERE singleton = 1 AND worker_id = ?
                """,
                ((now + timedelta(seconds=lease_seconds)).isoformat(), now.isoformat(), now.isoformat(), worker_id),
            )
        return result.rowcount == 1

    def release_lease(self, worker_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE procedure_runtime
                SET worker_id = NULL, lease_expires_at = NULL, updated_at = ?
                WHERE singleton = 1 AND worker_id = ?
                """,
                (utcnow().isoformat(), worker_id),
            )

    def recover_interrupted_runs(self, stale_after_seconds: int = 60) -> int:
        cutoff = utcnow() - timedelta(seconds=stale_after_seconds)
        now = utcnow()
        with self.connect() as conn:
            result = conn.execute(
                """
                UPDATE runs
                SET status = 'interrupted', finished_at = ?, error_category = 'worker_lost',
                    error_message = 'Worker heartbeat expired before run completion.'
                WHERE status = 'running' AND COALESCE(heartbeat_at, started_at) < ?
                """,
                (now.isoformat(), cutoff.isoformat()),
            )
        if result.rowcount:
            self.add_event("recovery.runs_interrupted", {"count": result.rowcount})
        return result.rowcount

    def recover_uncertain_tool_calls(self) -> int:
        """Fence tool writes whose outcome cannot be known after interruption.

        An executing call is never automatically replayed. The operator can inspect
        the durable arguments and remote system before deciding how to reconcile it.
        """
        with self.connect() as conn:
            result = conn.execute(
                """
                UPDATE tool_calls
                SET status = 'uncertain', finished_at = ?
                WHERE status IN ('running', 'executing')
                  AND run_id IN (SELECT id FROM runs WHERE status = 'interrupted')
                """,
                (utcnow().isoformat(),),
            )
        if result.rowcount:
            self.add_event("recovery.tool_calls_uncertain", {"count": result.rowcount})
        return result.rowcount

    def recover_stale_work(self, stale_after_seconds: int = 60) -> dict[str, int]:
        runs = self.recover_interrupted_runs(stale_after_seconds)
        commands = self.recover_stale_commands(stale_after_seconds)
        tool_calls = self.recover_uncertain_tool_calls()
        return {"runs": runs, "commands": commands, "tool_calls": tool_calls}

    def record_execution_success(self) -> None:
        now = utcnow()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE procedure_runtime
                SET failure_count = 0, backoff_until = NULL, updated_at = ?
                WHERE singleton = 1
                """,
                (now.isoformat(),),
            )

    def record_execution_failure(self, policy) -> RuntimeState:
        """Persist bounded exponential backoff with jitter and degraded-mode escalation."""
        now = utcnow()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT failure_count FROM procedure_runtime WHERE singleton = 1").fetchone()
            failure_count = int(row["failure_count"]) + 1
            raw_delay = min(
                policy.max_backoff_seconds,
                policy.base_backoff_seconds * (2 ** (failure_count - 1)),
            )
            delay = max(1, round(raw_delay * random.uniform(0.8, 1.2)))
            status = (
                RuntimeStatus.degraded.value
                if failure_count >= policy.max_consecutive_failures and policy.degraded_mode == "local_only"
                else RuntimeStatus.paused.value
                if failure_count >= policy.max_consecutive_failures
                else None
            )
            conn.execute(
                """
                UPDATE procedure_runtime
                SET failure_count = ?, backoff_until = ?,
                    status = COALESCE(?, status), updated_at = ?
                WHERE singleton = 1
                """,
                (failure_count, (now + timedelta(seconds=delay)).isoformat(), status, now.isoformat()),
            )
        self.add_event(
            "runtime.backoff_scheduled",
            {"failure_count": failure_count, "delay_seconds": delay, "status": status},
        )
        return self.runtime()

    def daily_spend(self) -> float:
        day = utcnow().date().isoformat()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(cost), 0) FROM usage_ledger WHERE substr(created_at, 1, 10) = ?",
                (day,),
            ).fetchone()
        return float(row[0])

    # Runs and evidence --------------------------------------------------

    def begin_run(self, state: ProcedureState, model: ModelProfile, *, attempt: int = 1, manifest: ContextManifest | None = None) -> RunRecord:
        now = utcnow()
        version = self.current_version()
        run_id = make_id("run")
        context_manifest = manifest or ContextManifest(reserved_output_tokens=state.output_reserve)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO runs(
                  id, state_id, procedure_version_id, goal, status, attempt, model_id,
                  provider, context_window, context_used, input_tokens, output_tokens,
                  cached_tokens, cost, context_manifest_json, started_at, heartbeat_at,
                  finished_at, final_thoughts, changes_json, output_json
                ) VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?, 0, 0, 0, 0, 0, ?, ?, ?, NULL, NULL, '[]', '{}')
                """,
                (
                    run_id,
                    state.id,
                    version.id,
                    state.goal_template,
                    attempt,
                    model.id,
                    model.provider,
                    model.context_window,
                    context_manifest.model_dump_json(),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
        self.add_event("run.started", {"state_id": state.id, "model_id": model.id}, run_id=run_id)
        return self.get_run(run_id)

    def heartbeat_run(self, run_id: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE runs SET heartbeat_at = ? WHERE id = ? AND status = 'running'", (utcnow().isoformat(), run_id))

    def finish_run(
        self,
        run_id: str,
        *,
        status: RunStatus,
        context_used: int,
        final_thoughts: str,
        changes: list[dict[str, Any]],
        output: RunOutput,
        provider_request_id: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cached_tokens: int = 0,
        cost: float = 0,
    ) -> RunRecord:
        now = utcnow()
        run = self.get_run(run_id)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            updated = conn.execute(
                """
                UPDATE runs SET status = ?, context_used = ?, input_tokens = ?, output_tokens = ?,
                  cached_tokens = ?, cost = ?, provider_request_id = ?, finished_at = ?,
                  heartbeat_at = ?, final_thoughts = ?, changes_json = ?, output_json = ?
                WHERE id = ? AND status = 'running'
                """,
                (
                    status.value,
                    context_used,
                    input_tokens,
                    output_tokens,
                    cached_tokens,
                    cost,
                    provider_request_id,
                    now.isoformat(),
                    now.isoformat(),
                    final_thoughts,
                    json.dumps(changes),
                    output.model_dump_json(),
                    run_id,
                ),
            )
            if updated.rowcount != 1:
                return self.get_run(run_id)
            conn.execute(
                """
                INSERT INTO usage_ledger(run_id, provider, model_id, input_tokens, output_tokens, cached_tokens, cost, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, run.provider, run.model_id, input_tokens, output_tokens, cached_tokens, cost, now.isoformat()),
            )
        self.add_event("run.finished", {"status": status.value, "cost": cost}, run_id=run_id)
        return self.get_run(run_id)

    def fail_run(self, run_id: str, category: str, message: str) -> RunRecord:
        now = utcnow()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE runs SET status = 'failed', finished_at = ?, heartbeat_at = ?,
                  error_category = ?, error_message = ? WHERE id = ? AND status = 'running'
                """,
                (now.isoformat(), now.isoformat(), category, message, run_id),
            )
        self.add_event("run.failed", {"category": category, "message": message}, run_id=run_id)
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> RunRecord:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return _run_from_row(row, fallback_version_id=self.runtime().active_version_id)

    def list_runs(
        self,
        limit: int = 50,
        *,
        state_id: str | None = None,
        status: str | None = None,
        cursor: str | None = None,
    ) -> list[RunRecord]:
        rows, _ = self.list_runs_page(limit=limit, state_id=state_id, status=status, cursor=cursor)
        return rows

    def list_runs_page(
        self,
        limit: int = 50,
        *,
        state_id: str | None = None,
        status: str | None = None,
        cursor: str | None = None,
    ) -> tuple[list[RunRecord], str | None]:
        where: list[str] = []
        params: list[Any] = []
        if state_id:
            where.append("state_id = ?")
            params.append(state_id)
        if status:
            where.append("status = ?")
            params.append(status)
        if cursor:
            started_at, run_id = _decode_cursor(cursor)
            where.append("(started_at < ? OR (started_at = ? AND id < ?))")
            params.extend((started_at, started_at, run_id))
        sql = "SELECT * FROM runs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY started_at DESC, id DESC LIMIT ?"
        params.append(limit + 1)
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        fallback = self.runtime().active_version_id
        items = [_run_from_row(row, fallback_version_id=fallback) for row in rows]
        next_cursor = _encode_cursor(rows[-1]["started_at"], rows[-1]["id"]) if has_more and rows else None
        return items, next_cursor

    def add_event(self, event_type: str, payload: dict[str, Any], *, run_id: str | None = None) -> RunEvent:
        now = utcnow()
        with self.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO run_events(run_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (run_id, event_type, json.dumps(payload), now.isoformat()),
            )
            event_id = int(cursor.lastrowid)
        return RunEvent(id=event_id, run_id=run_id, event_type=event_type, payload=payload, created_at=now)

    def list_events(self, *, after_id: int = 0, limit: int = 200, run_id: str | None = None) -> list[RunEvent]:
        with self.connect() as conn:
            if run_id:
                rows = conn.execute(
                    "SELECT * FROM run_events WHERE id > ? AND run_id = ? ORDER BY id LIMIT ?",
                    (after_id, run_id, limit),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM run_events WHERE id > ? ORDER BY id LIMIT ?", (after_id, limit)).fetchall()
        return [_event_from_row(row) for row in rows]

    def record_tool_call(
        self,
        run_id: str,
        tool_name: str,
        mutation_level: str,
        arguments: dict[str, Any],
        idempotency_key: str,
        *,
        status: str = "prepared",
        approval_id: str | None = None,
    ) -> ToolCallRecord:
        call_id = make_id("tool")
        now = utcnow()
        with self.connect() as conn:
            existing = conn.execute("SELECT * FROM tool_calls WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
            if existing:
                return _tool_from_row(existing)
            conn.execute(
                """
                INSERT INTO tool_calls(id, run_id, tool_name, status, mutation_level, idempotency_key,
                  arguments_json, result_json, approval_id, started_at, finished_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, NULL)
                """,
                (call_id, run_id, tool_name, status, mutation_level, idempotency_key, json.dumps(arguments), approval_id, now.isoformat()),
            )
        self.add_event("tool.requested", {"tool_call_id": call_id, "tool_name": tool_name}, run_id=run_id)
        return self.get_tool_call(call_id)

    def get_tool_call_by_idempotency_key(self, idempotency_key: str) -> ToolCallRecord | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM tool_calls WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
        return _tool_from_row(row) if row else None

    def start_tool_call(self, call_id: str) -> ToolCallRecord:
        with self.connect() as conn:
            conn.execute(
                "UPDATE tool_calls SET status = 'executing' WHERE id = ? AND status IN ('prepared', 'pending_approval')",
                (call_id,),
            )
        return self.get_tool_call(call_id)

    def finish_tool_call(self, call_id: str, status: str, result: dict[str, Any]) -> ToolCallRecord:
        with self.connect() as conn:
            conn.execute(
                "UPDATE tool_calls SET status = ?, result_json = ?, finished_at = ? WHERE id = ?",
                (status, json.dumps(result), utcnow().isoformat(), call_id),
            )
        call = self.get_tool_call(call_id)
        self.add_event("tool.finished", {"tool_call_id": call_id, "status": status}, run_id=call.run_id)
        return call

    def get_tool_call(self, call_id: str) -> ToolCallRecord:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM tool_calls WHERE id = ?", (call_id,)).fetchone()
        if row is None:
            raise KeyError(call_id)
        return _tool_from_row(row)

    def list_tool_calls(self, run_id: str) -> list[ToolCallRecord]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM tool_calls WHERE run_id = ? ORDER BY started_at", (run_id,)).fetchall()
        return [_tool_from_row(row) for row in rows]

    def add_artifact(self, artifact: ArtifactRecord) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO artifacts(id, run_id, label, kind, uri, path, content_hash, mime_type, size_bytes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact.id,
                    artifact.run_id,
                    artifact.label,
                    artifact.kind,
                    artifact.uri,
                    artifact.path,
                    artifact.content_hash,
                    artifact.mime_type,
                    artifact.size_bytes,
                    artifact.created_at.isoformat(),
                ),
            )

    def list_artifacts(self, run_id: str) -> list[ArtifactRecord]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM artifacts WHERE run_id = ? ORDER BY created_at", (run_id,)).fetchall()
        return [_artifact_from_row(row) for row in rows]

    # Approvals and governance ------------------------------------------

    def request_approval(
        self,
        *,
        kind: str,
        risk: str,
        proposed_action: dict[str, Any],
        run_id: str | None = None,
        evidence: list[SourceLink] | None = None,
    ) -> ApprovalRecord:
        approval_id = make_id("approval")
        now = utcnow()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO approvals(id, run_id, kind, status, risk, proposed_action_json,
                  evidence_json, requested_at, decided_at, decision_note)
                VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, NULL, NULL)
                """,
                (
                    approval_id,
                    run_id,
                    kind,
                    risk,
                    json.dumps(proposed_action),
                    json.dumps([item.model_dump(mode="json") for item in evidence or []]),
                    now.isoformat(),
                ),
            )
        self.add_event("approval.requested", {"approval_id": approval_id, "kind": kind}, run_id=run_id)
        return self.get_approval(approval_id)

    def get_approval(self, approval_id: str) -> ApprovalRecord:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        if row is None:
            raise KeyError(approval_id)
        return _approval_from_row(row)

    def list_approvals(
        self,
        limit: int = 50,
        status: str | None = None,
        *,
        cursor: str | None = None,
    ) -> list[ApprovalRecord]:
        rows, _ = self.list_approvals_page(limit=limit, status=status, cursor=cursor)
        return rows

    def list_approvals_page(
        self,
        limit: int = 50,
        status: str | None = None,
        *,
        cursor: str | None = None,
    ) -> tuple[list[ApprovalRecord], str | None]:
        where: list[str] = []
        params: list[Any] = []
        if status:
            where.append("status = ?")
            params.append(status)
        if cursor:
            requested_at, approval_id = _decode_cursor(cursor)
            where.append("(requested_at < ? OR (requested_at = ? AND id < ?))")
            params.extend((requested_at, requested_at, approval_id))
        sql = "SELECT * FROM approvals"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY requested_at DESC, id DESC LIMIT ?"
        params.append(limit + 1)
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = [_approval_from_row(row) for row in rows]
        next_cursor = _encode_cursor(rows[-1]["requested_at"], rows[-1]["id"]) if has_more and rows else None
        return items, next_cursor

    def decide_approval(self, approval_id: str, approved: bool, note: str | None = None) -> ApprovalRecord:
        approval = self.get_approval(approval_id)
        if approval.status != ApprovalStatus.pending:
            raise ValueError("approval has already been decided")
        status = ApprovalStatus.approved if approved else ApprovalStatus.rejected
        with self.connect() as conn:
            conn.execute(
                "UPDATE approvals SET status = ?, decided_at = ?, decision_note = ? WHERE id = ?",
                (status.value, utcnow().isoformat(), note, approval_id),
            )
            if approval.kind == "procedure_mutation" and approval.proposed_action.get("mutation_id"):
                conn.execute(
                    "UPDATE procedure_mutations SET status = ?, decided_at = ? WHERE id = ?",
                    (status.value, utcnow().isoformat(), approval.proposed_action["mutation_id"]),
                )
        self.add_event("approval.decided", {"approval_id": approval_id, "status": status.value}, run_id=approval.run_id)
        return self.get_approval(approval_id)

    def mark_approval_executed(self, approval_id: str, note: str | None = None) -> ApprovalRecord:
        approval = self.get_approval(approval_id)
        if approval.status != ApprovalStatus.approved:
            raise ValueError("only approved actions can be marked executed")
        with self.connect() as conn:
            conn.execute(
                "UPDATE approvals SET status = 'executed', decision_note = COALESCE(?, decision_note) WHERE id = ?",
                (note, approval_id),
            )
        self.add_event("approval.executed", {"approval_id": approval_id}, run_id=approval.run_id)
        return self.get_approval(approval_id)

    def get_tool_call_by_approval(self, approval_id: str) -> ToolCallRecord:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM tool_calls WHERE approval_id = ?", (approval_id,)).fetchone()
        if row is None:
            raise KeyError(approval_id)
        return _tool_from_row(row)

    def list_mutations(self, limit: int = 50) -> list[ProcedureMutation]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM procedure_mutations ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [_mutation_from_row(row) for row in rows]

    def propose_mutation(
        self,
        *,
        proposed_version_id: str,
        proposer_run_id: str,
        rationale: str,
        budget_impact: dict[str, Any] | None = None,
    ) -> tuple[ProcedureMutation, ApprovalRecord]:
        base = self.current_version()
        proposed = self.get_version(proposed_version_id)
        if proposed.status != "draft":
            raise ValueError("procedure mutation proposals must target a draft")
        mutation_id = make_id("mutation")
        now = utcnow()
        diff = self.diff_versions(base.id, proposed.id)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO procedure_mutations(id, base_version_id, proposed_version_id, proposer_run_id,
                  status, diff_text, rationale, budget_impact_json, rollback_version_id, created_at, decided_at)
                VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, NULL)
                """,
                (
                    mutation_id,
                    base.id,
                    proposed.id,
                    proposer_run_id,
                    diff,
                    rationale,
                    json.dumps(budget_impact or {}),
                    base.id,
                    now.isoformat(),
                ),
            )
        approval = self.request_approval(
            kind="procedure_mutation",
            risk="procedure_mutation",
            proposed_action={"mutation_id": mutation_id, "version_id": proposed.id},
            run_id=proposer_run_id,
            evidence=[SourceLink(label="Proposed diff", kind="procedure-diff", uri=f"consciousness://mutations/{mutation_id}")],
        )
        return next(item for item in self.list_mutations() if item.id == mutation_id), approval

    def mark_mutation_executed(self, mutation_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE procedure_mutations SET status = 'executed', decided_at = ? WHERE id = ?",
                (utcnow().isoformat(), mutation_id),
            )

    # Recaps and integrations -------------------------------------------

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
                INSERT INTO auditor_recaps(id, run_id, auditor_model_id, summary, decision, procedure_changes_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
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
        return [
            AuditorRecap(
                id=row["id"],
                run_id=row["run_id"],
                auditor_model_id=row["auditor_model_id"],
                summary=row["summary"],
                decision=row["decision"],
                procedure_changes=_load(row["procedure_changes_json"], []),
                created_at=_dt(row["created_at"]) or utcnow(),
            )
            for row in rows
        ]

    def upsert_integration(self, status: IntegrationStatus) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO integration_status(name, status, endpoint, last_checked_at, details_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET status=excluded.status, endpoint=excluded.endpoint,
                  last_checked_at=excluded.last_checked_at, details_json=excluded.details_json
                """,
                (status.name, status.status, status.endpoint, _iso(status.last_checked_at), json.dumps(status.details)),
            )

    def list_integrations(self) -> list[IntegrationStatus]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM integration_status ORDER BY name").fetchall()
        return [
            IntegrationStatus(
                name=row["name"],
                status=row["status"],
                endpoint=row["endpoint"],
                last_checked_at=_dt(row["last_checked_at"]),
                details=_load(row["details_json"], {}),
            )
            for row in rows
        ]

    def _seed_if_empty(self, conn: sqlite3.Connection) -> None:
        count = int(conn.execute("SELECT COUNT(*) FROM procedure_versions").fetchone()[0])
        if count:
            return
        definition = self._definition_from_legacy(conn) or _starter_definition()
        errors = validate_procedure(definition)
        if errors:
            raise RuntimeError("invalid starter procedure: " + "; ".join(errors))
        definition_json, digest = _serialize_definition(definition)
        now = utcnow()
        version_id = "procedure_v1"
        current = next(state.id for state in definition.states if state.is_current)
        conn.execute(
            """
            INSERT INTO procedure_versions(id, version, status, digest, parent_id, revision,
              definition_json, created_by_run_id, created_at, activated_at)
            VALUES (?, 1, 'active', ?, NULL, 1, ?, NULL, ?, ?)
            """,
            (version_id, digest, definition_json, now.isoformat(), now.isoformat()),
        )
        conn.execute(
            """
            INSERT INTO procedure_runtime(singleton, active_version_id, current_state_id, status,
              interval_seconds, worker_id, lease_expires_at, heartbeat_at, failure_count,
              backoff_until, daily_budget_cap, execution_mode, updated_at)
            VALUES (1, ?, ?, 'stopped', 60, NULL, NULL, NULL, 0, NULL, ?, ?, ?)
            """,
            (
                version_id,
                current,
                definition.guardrails.loop_control.daily_budget_cap,
                self.execution_mode,
                now.isoformat(),
            ),
        )
        if not conn.execute("SELECT 1 FROM integration_status WHERE name = 'only-memories'").fetchone():
            conn.execute(
                "INSERT INTO integration_status VALUES ('only-memories', 'not_checked', 'http://localhost:8765', ?, ?)",
                (now.isoformat(), json.dumps({"mode": "optional"})),
            )
        if not conn.execute("SELECT 1 FROM auditor_recaps LIMIT 1").fetchone():
            conn.execute(
                """
                INSERT INTO auditor_recaps VALUES ('recap_bootstrap', NULL, 'bootstrap', ?, 'ready', '[]', ?)
                """,
                ("Starter durable procedure installed.", now.isoformat()),
            )

    def _definition_from_legacy(self, conn: sqlite3.Connection) -> ProcedureDefinition | None:
        rows = conn.execute("SELECT * FROM procedure_states ORDER BY name").fetchall()
        if not rows:
            return None
        states = [
            ProcedureState(
                id=row["id"],
                name=row["name"],
                kind=row["kind"],
                domain=row["domain"],
                goal_template=row["goal_template"],
                prompt_contract=row["prompt_contract"],
                output_contract=row["output_contract"],
                tools=_load(row["tools_json"], []),
                skills=_load(row["skills_json"], []),
                context_minimum=row["context_minimum"],
                model_policy=row["model_policy"],
                x=row["x"],
                y=row["y"],
                is_current=bool(row["is_current"]),
            )
            for row in rows
        ]
        transitions = [
            Transition(
                id=row["id"],
                source_id=row["source_id"],
                target_id=row["target_id"],
                weight=row["weight"],
                guard=row["guard"],
                rationale=row["rationale"],
                active=bool(row["active"]),
            )
            for row in conn.execute("SELECT * FROM transitions").fetchall()
        ]
        models = [
            ModelProfile(
                id=row["id"],
                provider=row["provider"],
                model=row["model"],
                context_window=row["context_window"],
                relative_cost=row["relative_cost"],
                max_run_budget=row["max_run_budget"],
                quality_tier=row["quality_tier"],
                strengths=_load(row["strengths_json"], []),
                open_weights=bool(row["open_weights"]),
                enabled=bool(row["enabled"]),
            )
            for row in conn.execute("SELECT * FROM model_profiles").fetchall()
        ]
        return ProcedureDefinition(name="Research Loop", states=states, transitions=transitions, models=models, guardrails=default_guardrails())


def _starter_definition() -> ProcedureDefinition:
    states = [ProcedureState(**state) for state in STARTER_STATES]
    transitions = [
        Transition(
            id=f"{source}_to_{target}",
            source_id=source,
            target_id=target,
            weight=weight,
            guard="always",
            rationale=rationale,
        )
        for source, target, weight, rationale in STARTER_TRANSITIONS
    ]
    models = [ModelProfile(**model) for model in STARTER_MODELS]
    return ProcedureDefinition(name="Research Loop", states=states, transitions=transitions, models=models, guardrails=default_guardrails())


def _serialize_definition(definition: ProcedureDefinition) -> tuple[str, str]:
    compact = json.dumps(definition.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return compact, hashlib.sha256(compact.encode()).hexdigest()


def _version_from_row(row: sqlite3.Row) -> ProcedureVersion:
    return ProcedureVersion(
        id=row["id"],
        version=row["version"],
        status=row["status"],
        digest=row["digest"],
        parent_id=row["parent_id"],
        revision=row["revision"],
        definition=ProcedureDefinition.model_validate_json(row["definition_json"]),
        created_by_run_id=row["created_by_run_id"],
        created_at=_dt(row["created_at"]) or utcnow(),
        activated_at=_dt(row["activated_at"]),
    )


def _runtime_from_row(row: sqlite3.Row) -> RuntimeState:
    return RuntimeState(
        active_version_id=row["active_version_id"],
        current_state_id=row["current_state_id"],
        status=row["status"],
        interval_seconds=row["interval_seconds"],
        worker_id=row["worker_id"],
        lease_expires_at=_dt(row["lease_expires_at"]),
        heartbeat_at=_dt(row["heartbeat_at"]),
        failure_count=row["failure_count"],
        backoff_until=_dt(row["backoff_until"]),
        daily_budget_cap=row["daily_budget_cap"],
        execution_mode=row["execution_mode"],
        updated_at=_dt(row["updated_at"]) or utcnow(),
    )


def _run_from_row(row: sqlite3.Row, *, fallback_version_id: str) -> RunRecord:
    output_payload = _load(row["output_json"], {})
    manifest_payload = _load(row["context_manifest_json"], {}) if "context_manifest_json" in row.keys() else {}
    return RunRecord(
        id=row["id"],
        state_id=row["state_id"],
        procedure_version_id=row["procedure_version_id"] or fallback_version_id,
        goal=row["goal"],
        status=row["status"],
        attempt=row["attempt"],
        model_id=row["model_id"],
        provider=row["provider"],
        provider_request_id=row["provider_request_id"],
        context_window=row["context_window"],
        context_used=row["context_used"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        cached_tokens=row["cached_tokens"],
        cost=row["cost"],
        context_manifest=ContextManifest(**manifest_payload) if manifest_payload else ContextManifest(),
        started_at=_dt(row["started_at"]) or utcnow(),
        heartbeat_at=_dt(row["heartbeat_at"]),
        finished_at=_dt(row["finished_at"]),
        final_thoughts=row["final_thoughts"],
        changes=_load(row["changes_json"], []),
        output=RunOutput(**output_payload) if output_payload else None,
        error_category=row["error_category"],
        error_message=row["error_message"],
    )


def _command_from_row(row: sqlite3.Row) -> RuntimeCommand:
    return RuntimeCommand(
        id=row["id"],
        kind=row["kind"],
        status=row["status"],
        payload=_load(row["payload_json"], {}),
        created_at=_dt(row["created_at"]) or utcnow(),
        claimed_at=_dt(row["claimed_at"]),
        completed_at=_dt(row["completed_at"]),
        error=row["error"],
    )


def _event_from_row(row: sqlite3.Row) -> RunEvent:
    return RunEvent(
        id=row["id"],
        run_id=row["run_id"],
        event_type=row["event_type"],
        payload=_load(row["payload_json"], {}),
        created_at=_dt(row["created_at"]) or utcnow(),
    )


def _approval_from_row(row: sqlite3.Row) -> ApprovalRecord:
    return ApprovalRecord(
        id=row["id"],
        run_id=row["run_id"],
        kind=row["kind"],
        status=row["status"],
        risk=row["risk"],
        proposed_action=_load(row["proposed_action_json"], {}),
        evidence=[SourceLink(**item) for item in _load(row["evidence_json"], [])],
        requested_at=_dt(row["requested_at"]) or utcnow(),
        decided_at=_dt(row["decided_at"]),
        decision_note=row["decision_note"],
    )


def _mutation_from_row(row: sqlite3.Row) -> ProcedureMutation:
    return ProcedureMutation(
        id=row["id"],
        base_version_id=row["base_version_id"],
        proposed_version_id=row["proposed_version_id"],
        proposer_run_id=row["proposer_run_id"],
        status=row["status"],
        diff=row["diff_text"],
        rationale=row["rationale"],
        budget_impact=_load(row["budget_impact_json"], {}),
        rollback_version_id=row["rollback_version_id"],
        created_at=_dt(row["created_at"]) or utcnow(),
        decided_at=_dt(row["decided_at"]),
    )


def _tool_from_row(row: sqlite3.Row) -> ToolCallRecord:
    return ToolCallRecord(
        id=row["id"],
        run_id=row["run_id"],
        tool_name=row["tool_name"],
        status=row["status"],
        mutation_level=row["mutation_level"],
        idempotency_key=row["idempotency_key"],
        arguments=_load(row["arguments_json"], {}),
        result=_load(row["result_json"], None),
        approval_id=row["approval_id"],
        started_at=_dt(row["started_at"]) or utcnow(),
        finished_at=_dt(row["finished_at"]),
    )


def _artifact_from_row(row: sqlite3.Row) -> ArtifactRecord:
    return ArtifactRecord(
        id=row["id"],
        run_id=row["run_id"],
        label=row["label"],
        kind=row["kind"],
        uri=row["uri"],
        path=row["path"],
        content_hash=row["content_hash"],
        mime_type=row["mime_type"],
        size_bytes=row["size_bytes"],
        created_at=_dt(row["created_at"]) or utcnow(),
    )
