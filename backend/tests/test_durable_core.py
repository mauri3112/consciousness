from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import pytest

from consciousness.artifacts import ArtifactStore
from consciousness.graph import validate_procedure
from consciousness.models import ApprovalStatus, CommandKind, RuntimeStatus
from consciousness.procedure_patch import apply_procedure_patch
from consciousness.runner import _LeaseHeartbeat, run_once
from consciousness.store import ConsciousnessStore, utcnow


@pytest.fixture()
def store(tmp_path: Path) -> ConsciousnessStore:
    value = ConsciousnessStore(tmp_path / "consciousness.db")
    value.setup()
    return value


def test_migrations_and_sqlite_safety_are_applied(store: ConsciousnessStore):
    with store.connect() as conn:
        versions = [row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
        foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert versions == [1, 2, 3]
    assert foreign_keys == 1
    assert journal_mode == "wal"


def test_concurrent_first_setup_serializes_migrations(tmp_path: Path):
    database_path = tmp_path / "concurrent-setup.db"

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _index: ConsciousnessStore(database_path).setup(), range(4)))

    store = ConsciousnessStore(database_path)
    with store.connect() as conn:
        versions = [row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
    assert versions == [1, 2, 3]
    assert store.integrity_check() == "ok"


def test_procedure_drafts_validate_activate_and_rollback(store: ConsciousnessStore):
    active = store.current_version()
    draft = store.create_draft()
    definition = draft.definition.model_copy(deep=True)
    definition.states[0].name = "Gather evidence"

    updated = store.update_draft(draft.id, definition, expected_revision=draft.revision)
    assert updated.revision == 2
    assert store.validate_version(updated.id) == []
    assert "Gather evidence" in store.diff_versions(active.id, updated.id)

    activated = store.activate_version(updated.id)
    assert activated.status == "active"
    assert store.current_version().id == activated.id
    rolled_back = store.rollback(active.id)
    assert store.current_version().id == rolled_back.id
    assert store.current_version().definition.states[0].name != "Gather evidence"
    assert len(store.list_mutations()) == 2


def test_stale_draft_revision_is_rejected(store: ConsciousnessStore):
    draft = store.create_draft()
    store.update_draft(draft.id, draft.definition, expected_revision=1)
    with pytest.raises(RuntimeError, match="revision_conflict"):
        store.update_draft(draft.id, draft.definition, expected_revision=1)


def test_stale_draft_parent_cannot_replace_newer_active_version(store: ConsciousnessStore):
    first = store.create_draft()
    stale = store.create_draft()

    store.activate_version(first.id)

    with pytest.raises(RuntimeError, match="stale_procedure_parent"):
        store.activate_version(stale.id)
    assert store.current_version().id == first.id


def test_invalid_graph_is_rejected(store: ConsciousnessStore):
    definition = store.current_version().definition.model_copy(deep=True)
    definition.transitions = [edge for edge in definition.transitions if edge.source_id != "gather"]
    errors = validate_procedure(definition)
    assert "state gather has no active outgoing transition" in errors


def test_worker_lease_commands_and_recovery_are_durable(store: ConsciousnessStore):
    assert store.acquire_lease("worker-a", 30)
    assert not store.acquire_lease("worker-b", 30)
    assert store.renew_lease("worker-a", 30)
    store.release_lease("worker-a")
    assert store.acquire_lease("worker-b", 30)

    command = store.enqueue_command(CommandKind.step)
    claimed = store.claim_command()
    assert claimed and claimed.id == command.id
    assert store.complete_command(command.id).status == "completed"
    assert store.set_runtime_status(RuntimeStatus.paused).status == RuntimeStatus.paused


def test_pending_step_commands_are_deduplicated(store: ConsciousnessStore):
    first = store.enqueue_command(CommandKind.step)
    duplicate = store.enqueue_command(CommandKind.step)

    assert duplicate.id == first.id
    with store.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM runtime_commands WHERE kind = 'step' AND status IN ('pending', 'claimed')"
        ).fetchone()[0]
    assert count == 1


def test_lease_is_renewed_during_long_running_work(store: ConsciousnessStore):
    assert store.acquire_lease("worker-a", 1)
    heartbeat = _LeaseHeartbeat(store, "worker-a", 1)
    heartbeat.start()
    try:
        time.sleep(1.2)
        assert not store.acquire_lease("worker-b", 1)
        heartbeat.assert_owned()
    finally:
        heartbeat.stop()
        store.release_lease("worker-a")


def test_stale_runs_commands_and_uncertain_writes_are_recovered_without_replay(store: ConsciousnessStore):
    run = store.begin_run(store.current_state(), store.list_models()[0])
    command = store.enqueue_command(CommandKind.step)
    assert store.claim_command().id == command.id
    call = store.record_tool_call(
        run.id,
        "only_memories.remember",
        "additive_memory",
        {"content": "possibly written"},
        "stable-key",
    )
    store.start_tool_call(call.id)
    stale = (utcnow() - timedelta(minutes=10)).isoformat()
    with store.connect() as conn:
        conn.execute("UPDATE runs SET heartbeat_at = ? WHERE id = ?", (stale, run.id))
        conn.execute("UPDATE runtime_commands SET claimed_at = ? WHERE id = ?", (stale, command.id))

    recovered = store.recover_stale_work(60)

    assert recovered == {"runs": 1, "commands": 1, "tool_calls": 1}
    assert store.get_run(run.id).status == "interrupted"
    assert store.get_command(command.id).status == "pending"
    assert store.get_tool_call(call.id).status == "uncertain"
    assert store.record_tool_call(
        run.id,
        "only_memories.remember",
        "additive_memory",
        {"content": "possibly written"},
        "stable-key",
    ).status == "uncertain"


def test_failures_schedule_bounded_backoff_and_degraded_mode(store: ConsciousnessStore, monkeypatch):
    monkeypatch.setattr("consciousness.store.random.uniform", lambda _low, _high: 1.0)
    policy = store.current_version().definition.guardrails.loop_control.model_copy(
        update={"base_backoff_seconds": 1, "max_backoff_seconds": 2, "max_consecutive_failures": 2}
    )
    store.set_runtime_status(RuntimeStatus.running)

    first = store.record_execution_failure(policy)
    assert first.failure_count == 1
    assert first.backoff_until is not None
    assert first.status == RuntimeStatus.running

    second = store.record_execution_failure(policy)
    assert second.failure_count == 2
    assert second.status == RuntimeStatus.degraded

    store.record_execution_success()
    recovered = store.runtime()
    assert recovered.failure_count == 0
    assert recovered.backoff_until is None


def test_artifacts_and_approvals_preserve_evidence(store: ConsciousnessStore, tmp_path: Path):
    result = run_once(store.database_path)
    artifact_store = ArtifactStore(tmp_path / "artifacts", store)
    pointer = artifact_store.write_text(result.run.id, "note.md", "durable evidence", label="Evidence")
    artifacts = store.list_artifacts(result.run.id)
    assert pointer.content_hash == artifacts[0].content_hash
    assert Path(artifacts[0].path).read_text() == "durable evidence"

    approval = store.request_approval(
        kind="tool_call",
        risk="destructive_memory",
        proposed_action={"tool": "only_memories.forget", "memory_id": "memory-1"},
        run_id=result.run.id,
    )
    assert approval.status == ApprovalStatus.pending
    approved = store.decide_approval(approval.id, True, "operator checked evidence")
    assert approved.status == ApprovalStatus.approved


def test_preview_run_records_version_events_usage_and_payload(store: ConsciousnessStore, monkeypatch):
    monkeypatch.setenv("ONLY_MEMORIES_URL", "")
    result = run_once(store.database_path)
    run = store.get_run(result.run.id)
    assert run.procedure_version_id == store.current_version().id
    assert run.output and run.output.payload and run.output.payload.kind == "context_bundle"
    assert run.input_tokens > 0
    assert {event.event_type for event in store.list_events(run_id=run.id)} >= {
        "run.started",
        "provider.requested",
        "output.validated",
        "run.finished",
        "transition.committed",
    }


def test_finishing_a_terminal_run_is_idempotent_and_does_not_double_charge(store: ConsciousnessStore):
    result = run_once(store.database_path)
    run = result.run
    store.finish_run(
        run.id,
        status=run.status,
        context_used=run.context_used,
        final_thoughts=run.final_thoughts or "done",
        changes=run.changes,
        output=run.output,
        provider_request_id=run.provider_request_id,
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        cached_tokens=run.cached_tokens,
        cost=run.cost,
    )
    with store.connect() as conn:
        usage_rows = conn.execute("SELECT COUNT(*) FROM usage_ledger WHERE run_id = ?", (run.id,)).fetchone()[0]
    assert usage_rows == 1


def test_direct_tick_respects_persisted_backoff(store: ConsciousnessStore):
    future = (utcnow() + timedelta(minutes=5)).isoformat()
    with store.connect() as conn:
        conn.execute("UPDATE procedure_runtime SET backoff_until = ? WHERE singleton = 1", (future,))
    before = len(store.list_runs())
    with pytest.raises(RuntimeError, match="backoff remains active"):
        run_once(store.database_path)
    assert len(store.list_runs()) == before


def test_exhausted_cloud_budget_falls_back_to_an_eligible_local_model(store: ConsciousnessStore):
    with store.connect() as conn:
        conn.execute("UPDATE procedure_runtime SET daily_budget_cap = 0 WHERE singleton = 1")
    result = run_once(store.database_path)
    assert result.run.provider == "ollama"


def test_auditor_mutation_is_versioned_and_requires_approval(store: ConsciousnessStore):
    result = run_once(store.database_path)
    draft = store.create_draft(created_by_run_id=result.run.id)
    changed = apply_procedure_patch(
        draft.definition,
        [{"op": "replace", "path": "/states/0/name", "value": "Audited state name"}],
    )
    updated = store.update_draft(draft.id, changed, expected_revision=draft.revision)
    mutation, approval = store.propose_mutation(
        proposed_version_id=updated.id,
        proposer_run_id=result.run.id,
        rationale="Test an auditable mutation.",
    )
    assert mutation.status == ApprovalStatus.pending
    assert store.current_version().id != updated.id
    decided = store.decide_approval(approval.id, True)
    assert decided.status == ApprovalStatus.approved
    store.activate_version(updated.id, record_mutation=False)
    store.mark_mutation_executed(mutation.id)
    store.mark_approval_executed(approval.id)
    assert store.current_version().definition.states[0].name == "Audited state name"
    assert next(item for item in store.list_mutations() if item.id == mutation.id).status == ApprovalStatus.executed
