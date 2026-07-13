from __future__ import annotations

from pathlib import Path

from consciousness.artifacts import ArtifactStore
from consciousness.guardrails import default_guardrails
from consciousness.models import (
    AuditDecision,
    MemoryChange,
    MemoryChangeProposal,
    RunOutput,
    RunStatus,
    ValidationFinding,
    ValidationReport,
)
from consciousness.providers import PreviewProvider, ProviderError, ProviderRequest
from consciousness.runner import _publish_validated_changes, run_once
from consciousness.store import ConsciousnessStore
from consciousness.tools import build_tool_registry


def prepare_synthesize_store(path: Path) -> ConsciousnessStore:
    store = ConsciousnessStore(path)
    store.setup()
    store.set_current_state("synthesize")
    return store


class ToolCallingProvider:
    def __init__(self, *, fail_first: bool = False) -> None:
        self.fail_first = fail_first
        self.requests: list[ProviderRequest] = []
        self.tool_results: list[dict[str, object]] = []

    def execute(self, request: ProviderRequest):
        self.requests.append(request)
        assert request.execute_tool is not None
        self.tool_results.append(
            request.execute_tool(
                "artifact.write",
                {
                    "filename": "provider-note.md",
                    "content": "durable provider tool output",
                    "label": "Provider note",
                },
            )
        )
        if self.fail_first and len(self.requests) == 1:
            raise ProviderError("unavailable", "temporary provider failure", retryable=True)
        return PreviewProvider().execute(request)


def finish_evidence_run(store: ConsciousnessStore, state_id: str, payload) -> object:
    state = next(item for item in store.current_version().definition.states if item.id == state_id)
    run = store.begin_run(state, store.list_models()[0])
    output = RunOutput(
        summary=f"{state_id} evidence",
        confidence=0.9,
        next_transition_recommendation="publish",
        payload=payload,
    )
    return store.finish_run(
        run.id,
        status=RunStatus.succeeded,
        context_used=1,
        final_thoughts="test evidence",
        changes=[],
        output=output,
    )


def test_runner_exposes_only_allowed_state_tools_and_records_execution(
    tmp_path: Path, monkeypatch
) -> None:
    database_path = tmp_path / "tools.db"
    store = prepare_synthesize_store(database_path)
    provider = ToolCallingProvider()
    monkeypatch.setenv("ONLY_MEMORIES_URL", "")
    monkeypatch.setenv("CONSCIOUSNESS_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setattr("consciousness.runner.build_provider", lambda *args, **kwargs: provider)

    result = run_once(database_path)

    assert [tool.name for tool in provider.requests[0].tools] == ["artifact.write"]
    schema = provider.requests[0].tools[0].input_schema
    assert schema == {
        "type": "object",
        "properties": {
            "filename": {"type": "string"},
            "content": {"type": "string"},
            "label": {"type": "string"},
        },
        "required": ["filename", "content", "label"],
        "additionalProperties": False,
    }
    calls = store.list_tool_calls(result.run.id)
    assert len(calls) == 1
    assert calls[0].status == "succeeded"
    assert calls[0].arguments["run_id"] == result.run.id
    assert provider.tool_results[0]["status"] == "succeeded"
    assert {event.event_type for event in store.list_events(run_id=result.run.id)} >= {
        "tool.requested",
        "tool.finished",
    }


def test_runner_provider_retry_reuses_deterministic_tool_step_keys(
    tmp_path: Path, monkeypatch
) -> None:
    database_path = tmp_path / "retry-tools.db"
    store = prepare_synthesize_store(database_path)
    provider = ToolCallingProvider(fail_first=True)
    monkeypatch.setenv("ONLY_MEMORIES_URL", "")
    monkeypatch.setenv("CONSCIOUSNESS_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setattr("consciousness.runner.build_provider", lambda *args, **kwargs: provider)
    monkeypatch.setattr("consciousness.runner.time.sleep", lambda _seconds: None)

    result = run_once(database_path)

    assert len(provider.requests) == 2
    assert provider.tool_results[0] == provider.tool_results[1]
    assert len(store.list_tool_calls(result.run.id)) == 1
    events = store.list_events(run_id=result.run.id)
    assert sum(event.event_type == "tool.requested" for event in events) == 1
    assert sum(event.event_type == "provider.retry" for event in events) == 1


def test_runner_provider_tool_preserves_risky_action_approval(
    tmp_path: Path, monkeypatch
) -> None:
    database_path = tmp_path / "approval-tools.db"
    store = ConsciousnessStore(database_path)
    store.setup()
    store.set_current_state("publish")

    class FakeMemoryClient:
        def health(self):
            return {"status": "ok"}

        def forget(self, *_args, **_kwargs):
            raise AssertionError("approval-gated tool must not execute immediately")

    class ApprovalProvider:
        tool_result: dict[str, object] | None = None

        def execute(self, request: ProviderRequest):
            assert request.execute_tool is not None
            assert "only_memories.forget" in {tool.name for tool in request.tools}
            self.tool_result = request.execute_tool("only_memories.forget", {"memory_id": "memory-1"})
            return PreviewProvider().execute(request)

    provider = ApprovalProvider()
    monkeypatch.setenv("ONLY_MEMORIES_URL", "http://memory.test")
    monkeypatch.setenv("CONSCIOUSNESS_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setattr("consciousness.runner.OnlyMemoriesClient", lambda _url: FakeMemoryClient())
    monkeypatch.setattr("consciousness.runner.build_provider", lambda *args, **kwargs: provider)

    result = run_once(database_path)

    assert provider.tool_result is not None
    assert provider.tool_result["status"] == "pending_approval"
    approval_id = provider.tool_result["approval_id"]
    assert approval_id
    call = store.list_tool_calls(result.run.id)[0]
    assert call.status == "pending_approval"
    assert call.approval_id == approval_id
    assert store.get_approval(str(approval_id)).status == "pending"


def test_publish_applies_only_validated_changes_maps_lifecycle_and_prevents_replay(tmp_path: Path) -> None:
    store = ConsciousnessStore(tmp_path / "publish-lifecycle.db")
    store.setup()
    proposal = finish_evidence_run(
        store,
        "curate",
        MemoryChangeProposal(
            changes=[
                MemoryChange(action="remember", content="base", reason="new evidence"),
                MemoryChange(
                    action="supersede",
                    memory_id="memory-old",
                    content="replacement",
                    reason="newer evidence",
                ),
                MemoryChange(
                    action="reinforce",
                    source_id="memory-new",
                    target_id="memory-old",
                    amount=0.2,
                    reason="confirmed relationship",
                ),
                MemoryChange(action="forget", memory_id="memory-stale", reason="stale evidence"),
                MemoryChange(action="restore", memory_id="memory-restorable", reason="rejected restore"),
            ]
        ),
    )
    validation = finish_evidence_run(
        store,
        "validate",
        ValidationReport(
            sufficient_evidence=True,
            findings=[
                ValidationFinding(change_index=index, accepted=index < 4, reason="checked")
                for index in range(5)
            ],
        ),
    )

    class FakeMemoryClient:
        def __init__(self) -> None:
            self.remembered: list[dict[str, object]] = []
            self.forgotten: list[tuple[str, str | None]] = []
            self.restored: list[str] = []
            self.reinforced: list[tuple[str, str, float, str]] = []

        def remember(self, payload, _idempotency_key=None):
            self.remembered.append(payload)
            return {"id": f"memory-{len(self.remembered)}"}

        def supersede(self, memory_id, payload, _idempotency_key=None):
            self.remembered.append({"memory_id": memory_id, **payload})
            return {"id": f"memory-{len(self.remembered)}"}

        def forget(self, memory_id, reason=None, _idempotency_key=None):
            self.forgotten.append((memory_id, reason))
            return {"id": memory_id, "is_forgotten": True}

        def restore(self, memory_id, _idempotency_key=None):
            self.restored.append(memory_id)
            return {"id": memory_id, "is_forgotten": False}

        def reinforce(self, source_id, target_id, amount, reason, _idempotency_key=None):
            self.reinforced.append((source_id, target_id, amount, reason))
            return {"status": "ok"}

    client = FakeMemoryClient()
    registry = build_tool_registry(
        store,
        only_memories=client,
        artifacts=ArtifactStore(tmp_path / "artifacts", store),
    )
    publish_state = next(item for item in store.current_version().definition.states if item.id == "publish")
    publish_run = store.begin_run(publish_state, store.list_models()[0])
    receipt = _publish_validated_changes(
        store,
        registry,
        publish_run.id,
        default_guardrails().capability_policies,
    )

    assert receipt.proposal_run_id == proposal.id
    assert receipt.validation_run_id == validation.id
    assert receipt.applied == ["memory-1", "only_memories.reinforce"]
    assert len(receipt.pending_approval_ids) == 2
    assert client.remembered == [
        {
            "type": "artifact",
            "content": "base",
            "source": "consciousness",
            "metadata": {"origin_run_id": proposal.id, "reason": "new evidence"},
        }
    ]
    assert client.reinforced == [
        ("memory-new", "memory-old", 0.2, "confirmed relationship")
    ]
    assert client.forgotten == []
    assert client.restored == []

    for approval_id in receipt.pending_approval_ids:
        store.decide_approval(approval_id, True, "controlled lifecycle test")
        registry.execute_approved(store.get_tool_call_by_approval(approval_id))
    assert client.remembered[1] == {
        "memory_id": "memory-old",
        "type": "artifact",
        "content": "replacement",
        "source": "consciousness",
        "metadata": {"origin_run_id": proposal.id, "reason": "newer evidence"},
    }
    assert client.forgotten == [("memory-stale", "stale evidence")]
    assert all(store.get_approval(item).status == "executed" for item in receipt.pending_approval_ids)

    finished_output = RunOutput(
        summary="published",
        confidence=0.9,
        next_transition_recommendation="audit",
        payload=receipt,
    )
    store.finish_run(
        publish_run.id,
        status=RunStatus.succeeded,
        context_used=1,
        final_thoughts="published once",
        changes=[],
        output=finished_output,
    )
    replay_run = store.begin_run(publish_state, store.list_models()[0])
    replay = _publish_validated_changes(
        store,
        registry,
        replay_run.id,
        default_guardrails().capability_policies,
    )

    assert replay.proposal_run_id == proposal.id
    assert replay.skipped_reason == "memory proposal was already published"
    assert len(client.remembered) == 2
    assert len(client.reinforced) == 1
    assert len(client.forgotten) == 1


def test_publish_does_not_reuse_a_proposal_from_before_the_latest_audit(tmp_path: Path) -> None:
    store = ConsciousnessStore(tmp_path / "cycle-local-publish.db")
    store.setup()
    finish_evidence_run(
        store,
        "curate",
        MemoryChangeProposal(
            changes=[MemoryChange(action="remember", content="stale", reason="old cycle")]
        ),
    )
    finish_evidence_run(
        store,
        "validate",
        ValidationReport(
            sufficient_evidence=True,
            findings=[ValidationFinding(change_index=0, accepted=True, reason="old validation")],
        ),
    )
    finish_evidence_run(store, "audit", AuditDecision(decision="continue"))
    publish_state = next(
        item for item in store.current_version().definition.states if item.id == "publish"
    )
    publish_run = store.begin_run(publish_state, store.list_models()[0])
    registry = build_tool_registry(
        store,
        only_memories=None,
        artifacts=ArtifactStore(tmp_path / "artifacts", store),
    )

    receipt = _publish_validated_changes(
        store,
        registry,
        publish_run.id,
        default_guardrails().capability_policies,
    )

    assert receipt.proposal_run_id is None
    assert receipt.skipped_reason == "no cycle-local validated memory proposal is available"


def test_gather_records_degraded_memory_search_and_unresolved_risk(tmp_path: Path, monkeypatch) -> None:
    class SearchFailureClient:
        def health(self):
            return {"status": "ok"}

        def search(self, *_args, **_kwargs):
            raise RuntimeError("search unavailable")

    monkeypatch.setenv("ONLY_MEMORIES_URL", "http://memory.test")
    monkeypatch.setattr("consciousness.runner.OnlyMemoriesClient", lambda _url: SearchFailureClient())

    result = run_once(tmp_path / "degraded-context.db")
    store = ConsciousnessStore(tmp_path / "degraded-context.db")

    assert result.run.output is not None
    assert any("Gather proceeded without memory evidence" in risk for risk in result.run.output.unresolved_risks)
    integration = next(item for item in store.list_integrations() if item.name == "only-memories")
    assert integration.status == "degraded"
    assert any(event.event_type == "integration.degraded" for event in store.list_events())
