from __future__ import annotations

from pathlib import Path
from typing import Any

from consciousness.artifacts import ArtifactStore
from consciousness.guardrails import default_guardrails
from consciousness.seed import STARTER_STATES
from consciousness.store import ConsciousnessStore
from consciousness.tools import build_tool_registry


class FakeMemoryClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def search(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("search", (*args, kwargs)))
        return {"results": []}

    def supersede(self, memory_id: str, payload: dict[str, Any], _idempotency_key: str | None = None) -> dict[str, Any]:
        self.calls.append(("supersede", (memory_id, payload)))
        return {"id": "replacement"}

    def __getattr__(self, name: str):
        def handler(*args: Any, **kwargs: Any) -> dict[str, Any]:
            self.calls.append((name, (*args, kwargs)))
            return {"status": "ok"}

        return handler


def build_registry(tmp_path: Path):
    store = ConsciousnessStore(tmp_path / "tools.db")
    store.setup()
    client = FakeMemoryClient()
    registry = build_tool_registry(
        store,
        only_memories=client,  # type: ignore[arg-type]
        artifacts=ArtifactStore(tmp_path / "artifacts", store),
    )
    return store, client, registry


def test_seeded_only_memories_tools_are_canonical_and_registered(tmp_path: Path) -> None:
    _store, _client, registry = build_registry(tmp_path)
    registered = set(registry._tools)
    seeded = {
        tool
        for state in STARTER_STATES
        for tool in state["tools"]
        if str(tool).startswith("only_memories.")
    }

    assert "only_memories.reinforce_connection" not in seeded
    assert seeded <= registered


def test_curate_is_read_only_and_publish_can_use_canonical_mutations() -> None:
    policies = {policy.state_id: policy for policy in default_guardrails().capability_policies}

    assert policies["curate"].allowed_tool_patterns == [
        "only_memories.search",
        "only_memories.navigate",
        "only_memories.versions",
    ]
    assert policies["curate"].requires_approval is False
    assert "only_memories.supersede" in policies["publish"].allowed_tool_patterns
    assert "only_memories.reinforce" in policies["publish"].allowed_tool_patterns


def test_only_memories_schemas_match_public_request_contracts(tmp_path: Path) -> None:
    _store, _client, registry = build_registry(tmp_path)
    search = registry._tools["only_memories.search"].input_schema
    remember = registry._tools["only_memories.remember"].input_schema
    supersede = registry._tools["only_memories.supersede"].input_schema
    forget = registry._tools["only_memories.forget"].input_schema
    reinforce = registry._tools["only_memories.reinforce"].input_schema

    assert search["required"] == ["query"]
    assert search["properties"]["limit"] == {"type": "integer", "minimum": 1, "maximum": 50}
    assert search["properties"]["scope"]["enum"] == ["general", "remembering"]
    assert remember["required"] == ["content"]
    assert {"type", "source_links", "cadence", "base_importance", "metadata", "connections"} <= set(remember["properties"])
    assert "supersedes_id" not in remember["properties"]
    assert supersede["required"] == ["memory_id", "content"]
    assert forget["required"] == ["memory_id"]
    assert "reason" in forget["properties"]
    assert reinforce["required"] == ["source_id", "target_id"]
    assert reinforce["properties"]["amount"] == {"type": "number", "exclusiveMinimum": 0, "maximum": 1}


def test_search_forwards_optional_contract_fields(tmp_path: Path) -> None:
    store, client, registry = build_registry(tmp_path)
    policy = next(policy for policy in default_guardrails().capability_policies if policy.state_id == "curate")
    run = store.begin_run(store.get_state("curate"), store.list_models()[0])

    result = registry.execute(
        run_id=run.id,
        tool_name="only_memories.search",
        arguments={
            "query": "agent memory",
            "limit": 7,
            "type": "project",
            "scope": "remembering",
            "include_forgotten": True,
            "include_expired": True,
        },
        policy=policy,
        step_key="search-1",
    )

    assert result.status == "succeeded"
    assert client.calls == [
        (
            "search",
            (
                "agent memory",
                7,
                {
                    "memory_type": "project",
                    "scope": "remembering",
                    "include_forgotten": True,
                    "include_expired": True,
                },
            ),
        )
    ]
    assert len(store.list_tool_calls(run.id)) == 1


def test_supersede_is_approval_gated_and_executes_only_after_approval(tmp_path: Path) -> None:
    store, client, registry = build_registry(tmp_path)
    policy = next(policy for policy in default_guardrails().capability_policies if policy.state_id == "publish")
    run = store.begin_run(store.get_state("publish"), store.list_models()[0])

    pending = registry.execute(
        run_id=run.id,
        tool_name="only_memories.supersede",
        arguments={"memory_id": "memory-old", "content": "corrected", "source": "consciousness"},
        policy=policy,
        step_key="supersede-1",
    )

    assert pending.status == "pending_approval"
    assert pending.approval_id
    assert client.calls == []
    store.decide_approval(pending.approval_id, True, "Contract test approval")
    call = store.list_tool_calls(run.id)[0]
    executed = registry.execute_approved(call)
    assert executed.status == "succeeded"
    assert client.calls == [
        ("supersede", ("memory-old", {"content": "corrected", "source": "consciousness"}))
    ]


def test_ambiguous_approved_write_is_fenced_until_reconciled(tmp_path: Path) -> None:
    store, client, registry = build_registry(tmp_path)
    policy = next(policy for policy in default_guardrails().capability_policies if policy.state_id == "publish")
    run = store.begin_run(store.get_state("publish"), store.list_models()[0])
    pending = registry.execute(
        run_id=run.id,
        tool_name="only_memories.supersede",
        arguments={"memory_id": "memory-old", "content": "corrected"},
        policy=policy,
        step_key="ambiguous-write",
    )
    store.decide_approval(pending.approval_id, True, "approved")
    call = store.get_tool_call_by_approval(pending.approval_id)

    from consciousness.only_memories import RemoteWriteUncertain

    attempts = 0
    keys: list[str] = []

    def ambiguous(_memory_id: str, _payload: dict[str, Any], key: str) -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
        keys.append(key)
        if attempts == 1:
            raise RemoteWriteUncertain("response timed out after commit")
        return {"id": "replacement"}

    client.supersede = ambiguous  # type: ignore[method-assign]
    assert registry.execute_approved(call).status == "uncertain"
    assert registry.execute_approved(store.get_tool_call(call.id)).status == "uncertain"
    assert attempts == 1

    store.reconcile_tool_call(call.id, applied=False, result={"checked": "remote lookup"})
    assert registry.execute_approved(store.get_tool_call(call.id)).status == "succeeded"
    assert attempts == 2
    assert keys == [call.idempotency_key, call.idempotency_key]
