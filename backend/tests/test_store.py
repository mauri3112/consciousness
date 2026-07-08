from __future__ import annotations

from consciousness.llm import choose_model
from consciousness.models import RunStatus
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


def test_choose_model_prefers_cheapest_sufficient_model(tmp_path):
    store = ConsciousnessStore(tmp_path / "consciousness.db")
    store.setup()
    state = store.get_state("synthesize")

    model = choose_model(state, store.list_models())

    assert model.context_window >= state.context_minimum
    assert model.relative_cost <= 1.0


def test_run_once_advances_state_and_records_run(tmp_path, monkeypatch):
    monkeypatch.setenv("ONLY_MEMORIES_URL", "")
    db_path = tmp_path / "consciousness.db"

    result = run_once(db_path)
    store = ConsciousnessStore(db_path)

    assert result.previous_state.id == "gather"
    assert result.next_state.id == "curate"
    assert result.run.status == RunStatus.succeeded
    assert result.run.final_thoughts
    assert store.current_state().id == "curate"
    assert store.list_recaps(limit=1)
