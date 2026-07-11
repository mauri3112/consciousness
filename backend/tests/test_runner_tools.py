from __future__ import annotations

from pathlib import Path

from consciousness.providers import PreviewProvider, ProviderError, ProviderRequest
from consciousness.runner import run_once
from consciousness.store import ConsciousnessStore


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
