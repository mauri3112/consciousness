from __future__ import annotations

import fnmatch
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable

from .artifacts import ArtifactStore
from .models import CapabilityPolicy, SourceLink, ToolCallRecord
from .only_memories import OnlyMemoriesClient, RemoteWriteUncertain
from .store import ConsciousnessStore


ToolHandler = Callable[[dict[str, Any], str], dict[str, Any]]


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
            result = tool.handler(arguments, call.idempotency_key)
        except RemoteWriteUncertain as exc:
            self.store.finish_tool_call(call.id, "uncertain", {"error": str(exc), "reconciliation_required": True})
            return ToolExecution(status="uncertain", result={"error": str(exc), "reconciliation_required": True})
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
            result = tool.handler(call.arguments, call.idempotency_key)
        except RemoteWriteUncertain as exc:
            result = {"error": str(exc), "reconciliation_required": True}
            self.store.finish_tool_call(call.id, "uncertain", result)
            return ToolExecution(status="uncertain", result=result, approval_id=call.approval_id)
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
        registry.register(ToolDefinition("only_memories.health", "Check memory service health.", _object_schema({}), "read_only", True, lambda _args, _key: only_memories.health()))
        registry.register(ToolDefinition("only_memories.search", "Search ranked memories.", _object_schema({"query": {"type": "string", "minLength": 1}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}, "type": {"type": "string", "enum": _MEMORY_TYPES}, "scope": {"type": "string", "enum": ["general", "remembering"]}, "include_forgotten": {"type": "boolean"}, "include_expired": {"type": "boolean"}}, required=["query"]), "read_only", True, lambda args, _key: only_memories.search(str(args["query"]), int(args.get("limit", 10)), memory_type=args.get("type"), scope=str(args.get("scope", "general")), include_forgotten=bool(args.get("include_forgotten", False)), include_expired=bool(args.get("include_expired", False)))))
        registry.register(ToolDefinition("only_memories.navigate", "Navigate memory connections.", _object_schema({"memory_id": {"type": "string"}, "limit": {"type": "integer", "minimum": 1}}, required=["memory_id"]), "read_only", True, lambda args, _key: only_memories.navigate(str(args["memory_id"]), int(args.get("limit", 10)))))
        registry.register(ToolDefinition("only_memories.versions", "Read memory versions.", _object_schema({"memory_id": {"type": "string"}}), "read_only", True, lambda args, _key: only_memories.versions(str(args["memory_id"]))))
        registry.register(ToolDefinition("only_memories.remember", "Create an additive memory.", _memory_create_schema(), "additive_memory", True, lambda args, key: only_memories.remember(args, key)))
        registry.register(ToolDefinition("only_memories.supersede", "Create a replacement and supersede an existing memory.", _supersede_schema(), "destructive_memory", True, lambda args, key: only_memories.supersede(str(args["memory_id"]), {name: value for name, value in args.items() if name != "memory_id"}, key)))
        registry.register(ToolDefinition("only_memories.forget", "Soft-forget a memory.", _object_schema({"memory_id": {"type": "string"}, "reason": {"type": "string"}}, required=["memory_id"]), "destructive_memory", True, lambda args, key: only_memories.forget(str(args["memory_id"]), args.get("reason"), key)))
        registry.register(ToolDefinition("only_memories.restore", "Restore a memory.", _object_schema({"memory_id": {"type": "string"}}), "additive_memory", True, lambda args, key: only_memories.restore(str(args["memory_id"]), key)))
        registry.register(ToolDefinition("only_memories.reinforce", "Reinforce a memory edge.", _object_schema({"source_id": {"type": "string"}, "target_id": {"type": "string"}, "amount": {"type": "number", "exclusiveMinimum": 0, "maximum": 1}, "reason": {"type": "string"}}, required=["source_id", "target_id"]), "additive_memory", True, lambda args, key: only_memories.reinforce(str(args["source_id"]), str(args["target_id"]), float(args.get("amount", 0.1)), str(args.get("reason", "reinforced")), key)))

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
            lambda args, _key: artifacts.write_text(
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


_MEMORY_TYPES = [
    "axiom", "preference", "project", "person", "decision", "concept", "source",
    "task", "event", "artifact", "skill", "system", "note",
]
_CADENCES = ["none", "daily", "weekly", "monthly", "seasonal"]
_MEMORY_RELATIONS = ["related", "updates", "extends", "derives", "supports"]


def _memory_create_properties() -> dict[str, dict[str, Any]]:
    source_link = {
        "type": "object",
        "properties": {
            "label": {"type": "string", "minLength": 1},
            "kind": {"type": "string", "minLength": 1},
            "uri": {"type": "string", "minLength": 1},
            "open_hint": {"type": ["string", "null"]},
            "metadata": {"type": "object"},
        },
        "required": ["label", "uri"],
        "additionalProperties": False,
    }
    connection = {
        "type": "object",
        "properties": {
            "target_id": {"type": "string"},
            "weight": {"type": "number", "minimum": 0, "maximum": 1},
            "reason": {"type": "string"},
            "relation": {"type": "string", "enum": _MEMORY_RELATIONS},
        },
        "required": ["target_id"],
        "additionalProperties": False,
    }
    return {
        "type": {"type": "string", "enum": _MEMORY_TYPES},
        "content": {"type": "string", "minLength": 1},
        "happened_at": {"type": ["string", "null"], "format": "date-time"},
        "source": {"type": "string"},
        "source_links": {"type": "array", "items": source_link},
        "cadence": {"type": "string", "enum": _CADENCES},
        "expires_at": {"type": ["string", "null"], "format": "date-time"},
        "base_importance": {"type": "number", "minimum": 0, "maximum": 1},
        "axiom_key": {"type": ["string", "null"]},
        "metadata": {"type": "object"},
        "connections": {"type": "array", "items": connection},
    }


def _memory_create_schema() -> dict[str, Any]:
    return _object_schema(_memory_create_properties(), required=["content"])


def _supersede_schema() -> dict[str, Any]:
    properties = {"memory_id": {"type": "string"}, **_memory_create_properties()}
    return _object_schema(properties, required=["memory_id", "content"])
