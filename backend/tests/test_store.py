from __future__ import annotations

import json

from consciousness.context import assemble_prompt
from consciousness.llm import choose_model
from consciousness.maintenance import upgrade_bundled_profile
from consciousness.models import ContextManifest, RunStatus
from consciousness.runner import run_once
from consciousness.store import ConsciousnessStore


def test_store_seeds_strong_loop(tmp_path):
    store = ConsciousnessStore(tmp_path / "consciousness.db")
    store.setup()

    snapshot = store.snapshot()
    state_ids = {state.id for state in snapshot.states}
    outgoing = {transition.source_id for transition in snapshot.transitions if transition.active}

    assert {"gather", "curate", "synthesize", "validate", "publish", "audit"} <= state_ids
    assert state_ids <= outgoing
    assert store.current_state().id == "gather"


def test_store_reconciles_execution_mode_when_reopening_database(tmp_path):
    database_path = tmp_path / "consciousness.db"
    preview_store = ConsciousnessStore(database_path, execution_mode="preview")
    preview_store.setup()

    live_store = ConsciousnessStore(database_path, execution_mode="live")
    live_store.setup()

    assert live_store.runtime().execution_mode == "live"
    mode_events = [
        event for event in live_store.list_events(limit=50)
        if event.event_type == "runtime.execution_mode"
    ]
    assert len(mode_events) == 1
    assert mode_events[0].payload == {"previous": "preview", "current": "live"}

    live_store.setup()
    assert len([
        event for event in live_store.list_events(limit=50)
        if event.event_type == "runtime.execution_mode"
    ]) == 1


def test_runtime_interval_update_is_durable_and_audited(tmp_path):
    database_path = tmp_path / "interval.db"
    store = ConsciousnessStore(database_path)
    store.setup()

    runtime = store.set_runtime_interval(300)

    assert runtime.interval_seconds == 300
    assert ConsciousnessStore(database_path).runtime().interval_seconds == 300
    event = next(item for item in store.list_events(limit=20) if item.event_type == "runtime.interval")
    assert event.payload == {"previous_seconds": 60, "current_seconds": 300}


def test_bundled_profile_upgrade_is_versioned_audited_and_idempotent(tmp_path):
    store = ConsciousnessStore(tmp_path / "consciousness.db")
    store.setup()
    draft = store.create_draft()
    definition = draft.definition.model_copy(deep=True)
    definition.access_presets = []
    next(state for state in definition.states if state.id == "curate").tools = [
        "only_memories.reinforce_connection"
    ]
    definition.models = [model for model in definition.models if model.id != "local/qwen3.5-9b"]
    definition.models[0].id = "local/llama-3.1-8b-instruct"
    definition.models[0].provider = "ollama"
    definition.models[0].model = "llama-3.1-8b-instruct"
    for state in definition.states:
        if state.preferred_model_id == "local/ornith-1.0-9b-q4":
            state.preferred_model_id = None
            state.allow_model_fallback = True
    updated = store.update_draft(draft.id, definition, expected_revision=draft.revision)
    store.activate_version(updated.id, rationale="test legacy profile")
    store.set_current_state("validate")
    mutations_before = len(store.list_mutations())
    recaps_before = len(store.list_recaps())

    upgraded = upgrade_bundled_profile(store)

    assert upgraded is not None
    assert store.current_state().id == "validate"
    assert next(state for state in upgraded.definition.states if state.id == "curate").tools == [
        "only_memories.search",
        "only_memories.navigate",
        "only_memories.versions",
    ]
    assert "local/ornith-1.0-9b-q4" in {model.id for model in upgraded.definition.models}
    assert "coding-agent" in {preset.id for preset in upgraded.definition.access_presets}
    assert "local/llama-3.1-8b-instruct" not in {model.id for model in upgraded.definition.models}
    assert len(store.list_mutations()) == mutations_before + 1
    assert len(store.list_recaps()) == recaps_before + 1
    assert upgrade_bundled_profile(store) is None


def test_choose_model_prefers_cheapest_sufficient_model(tmp_path):
    store = ConsciousnessStore(tmp_path / "consciousness.db")
    store.setup()
    state = store.get_state("synthesize")

    model = choose_model(state, store.list_models())

    assert model.context_window >= state.context_minimum
    assert model.relative_cost <= 1.0


def test_starter_profile_pins_routine_states_local_and_audit_remote(tmp_path):
    store = ConsciousnessStore(tmp_path / "consciousness.db")
    store.setup()

    states = store.snapshot().states
    selected = {
        state.id: choose_model(state, store.list_models(), local_only=state.id != "audit").id
        for state in states
    }

    assert selected["audit"] == "minimax/MiniMax-M3"
    assert {selected[state.id] for state in states if state.id != "audit"} == {
        "local/ornith-1.0-9b-q4"
    }


def test_prompt_requires_run_output_envelope(tmp_path):
    store = ConsciousnessStore(tmp_path / "consciousness.db")
    store.setup()

    instructions, input_text = assemble_prompt(store.get_state("gather"), ContextManifest(), None)
    envelope = json.loads(input_text)["required_result_envelope"]

    assert "Do not return the state payload at the top level" in instructions
    assert envelope["next_transition_recommendation"] == "curate"
    assert envelope["payload"]["kind"] == "context_bundle"


def test_run_once_advances_state_and_records_run(tmp_path, monkeypatch):
    monkeypatch.setenv("ONLY_MEMORIES_URL", "")
    db_path = tmp_path / "consciousness.db"

    result = run_once(db_path)
    store = ConsciousnessStore(db_path)

    assert result.previous_state.id == "gather"
    assert result.next_state.id == "curate"
    assert result.run.status == RunStatus.succeeded
    assert result.run.final_thoughts
    assert result.run.output is not None
    assert result.run.output.changed_resources
    assert result.run.output.source_links
    assert store.current_state().id == "curate"
    assert store.list_recaps(limit=1)


def test_guardrails_are_first_class_snapshot_data(tmp_path):
    store = ConsciousnessStore(tmp_path / "consciousness.db")
    store.setup()

    guardrails = store.snapshot().guardrails

    assert guardrails.loop_control.manual_pause_enabled is True
    assert guardrails.evidence_policy.structured_output_required is True
    assert {policy.state_id for policy in guardrails.capability_policies} >= {"gather", "audit"}
    assert any(policy.requires_approval for policy in guardrails.capability_policies)
