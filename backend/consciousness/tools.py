from __future__ import annotations

import fnmatch
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable

from .artifacts import ArtifactStore
from .models import CapabilityPolicy, SourceLink, ToolCallRecord
from .only_memories import OnlyMemoriesClient
from .store import ConsciousnessStore


ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    mutation_level: str
    idempotent: bool
    handler: ToolHandler


@dataclass(slots=True)
class ToolExecution:
    status: str
    result: dict[str, Any] | None = None
    approval_id: str | None = None


class ToolRegistry:
    def __init__(self, store: ConsciousnessStore) -> None:
        self.store = store
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool: {tool.name}")
        self._tools[tool.name] = tool

    def definitions_for(self, policy: CapabilityPolicy) -> list[ToolDefinition]:
        return [tool for tool in self._tools.values() if _allowed(tool.name, policy.allowed_tool_patterns)]

    def execute(
        self,
        *,
        run_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        policy: CapabilityPolicy,
        step_key: str,
    ) -> ToolExecution:
        tool = self._tools.get(tool_name)
        if not tool or not _allowed(tool_name, policy.allowed_tool_patterns):
            raise PermissionError(f"tool {tool_name!r} is not allowed for state {policy.state_id}")
        key_payload = json.dumps([run_id, tool_name, arguments, step_key], sort_keys=True)
        idempotency_key = hashlib.sha256(key_payload.encode()).hexdigest()
        existing = self.store.get_tool_call_by_idempotency_key(idempotency_key)
        if existing:
            return ToolExecution(status=existing.status, result=existing.result, approval_id=existing.approval_id)
        requires_approval = policy.requires_approval and tool.mutation_level in {
            "destructive_memory",
            "external_write",
            "procedure_mutation",
        }
        if requires_approval:
            approval = self.store.request_approval(
                kind="tool_call",
                risk=tool.mutation_level,
                proposed_action={"tool_name": tool_name, "arguments": arguments, "idempotency_key": idempotency_key},
                run_id=run_id,
                evidence=[SourceLink(label="Originating run", kind="run", uri=f"sqlite://runs/{run_id}")],
            )
            self.store.record_tool_call(
                run_id,
                tool_name,
                tool.mutation_level,
                arguments,
                idempotency_key,
                status="pending_approval",
                approval_id=approval.id,
            )
            return ToolExecution(status="pending_approval", approval_id=approval.id)
        call = self.store.record_tool_call(
            run_id,
            tool_name,
            tool.mutation_level,
            arguments,
            idempotency_key,
        )
        if call.status == "succeeded":
            return ToolExecution(status="succeeded", result=call.result)
        if call.status == "uncertain":
            return ToolExecution(status="uncertain", result=call.result)
        self.store.start_tool_call(call.id)
        try:
            result = tool.handler(arguments)
        except Exception as exc:
            self.store.finish_tool_call(call.id, "failed", {"error": str(exc)})
            raise
        self.store.finish_tool_call(call.id, "succeeded", result)
        return ToolExecution(status="succeeded", result=result)

    def execute_approved(self, call: ToolCallRecord) -> ToolExecution:
        tool = self._tools.get(call.tool_name)
        if not tool:
            raise KeyError(call.tool_name)
        if call.status == "succeeded":
            return ToolExecution(status="succeeded", result=call.result, approval_id=call.approval_id)
        if call.status == "uncertain":
            return ToolExecution(status="uncertain", result=call.result, approval_id=call.approval_id)
        self.store.start_tool_call(call.id)
        try:
            result = tool.handler(call.arguments)
        except Exception as exc:
            self.store.finish_tool_call(call.id, "failed", {"error": str(exc)})
            raise
        self.store.finish_tool_call(call.id, "succeeded", result)
        if call.approval_id:
            self.store.mark_approval_executed(call.approval_id, "Approved tool action executed by the worker.")
        return ToolExecution(status="succeeded", result=result, approval_id=call.approval_id)


def build_tool_registry(
    store: ConsciousnessStore,
    *,
    only_memories: OnlyMemoriesClient | None,
    artifacts: ArtifactStore,
) -> ToolRegistry:
    registry = ToolRegistry(store)

    if only_memories:
        registry.register(ToolDefinition("only_memories.health", "Check memory service health.", _object_schema({}), "read_only", True, lambda _: only_memories.health()))
        registry.register(ToolDefinition("only_memories.search", "Search ranked memories.", _object_schema({"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1}}, required=["query", "limit"]), "read_only", True, lambda args: only_memories.search(str(args["query"]), int(args.get("limit", 8)))))
        registry.register(ToolDefinition("only_memories.navigate", "Navigate memory connections.", _object_schema({"memory_id": {"type": "string"}, "limit": {"type": "integer", "minimum": 1}}, required=["memory_id", "limit"]), "read_only", True, lambda args: only_memories.navigate(str(args["memory_id"]), int(args.get("limit", 8)))))
        registry.register(ToolDefinition("only_memories.versions", "Read memory versions.", _object_schema({"memory_id": {"type": "string"}}), "read_only", True, lambda args: only_memories.versions(str(args["memory_id"]))))
        registry.register(ToolDefinition("only_memories.remember", "Create an additive memory.", _object_schema({"content": {"type": "string"}}), "additive_memory", True, lambda args: only_memories.remember(args)))
        registry.register(ToolDefinition("only_memories.forget", "Soft-forget a memory.", _object_schema({"memory_id": {"type": "string"}}), "destructive_memory", True, lambda args: only_memories.forget(str(args["memory_id"]), args.get("reason"))))
        registry.register(ToolDefinition("only_memories.restore", "Restore a memory.", _object_schema({"memory_id": {"type": "string"}}), "additive_memory", True, lambda args: only_memories.restore(str(args["memory_id"]))))
        registry.register(ToolDefinition("only_memories.reinforce", "Reinforce a memory edge.", _object_schema({"source_id": {"type": "string"}, "target_id": {"type": "string"}}), "additive_memory", True, lambda args: only_memories.reinforce(str(args["source_id"]), str(args["target_id"]), float(args.get("amount", 0.1)), str(args.get("reason", "consciousness validation")))))

    registry.register(
        ToolDefinition(
            "artifact.write",
            "Write a durable artifact inside the configured artifact root.",
            _object_schema(
                {
                    "filename": {"type": "string"},
                    "content": {"type": "string"},
                    "label": {"type": "string"},
                }
            ),
            "artifact_write",
            True,
            lambda args: artifacts.write_text(
                str(args["run_id"]),
                str(args["filename"]),
                str(args["content"]),
                label=str(args["label"]),
            ).model_dump(mode="json"),
        )
    )
    return registry


def _allowed(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


def _object_schema(
    properties: dict[str, dict[str, Any]], *, required: list[str] | None = None
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": False,
    }
