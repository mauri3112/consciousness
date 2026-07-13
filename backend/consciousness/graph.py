from __future__ import annotations

import fnmatch
import re
from typing import Any

from .models import ProcedureDefinition, RunOutput, Transition
from .presets import resolve_state_access


_GUARD_RE = re.compile(r"^payload\.([a-zA-Z_][a-zA-Z0-9_]*)\s*(==|!=)\s*(true|false|[a-zA-Z0-9_-]+)$")


def validate_procedure(definition: ProcedureDefinition) -> list[str]:
    errors: list[str] = []
    state_ids = [state.id for state in definition.states]
    state_set = set(state_ids)
    if len(state_ids) != len(state_set):
        errors.append("state ids must be unique")
    if not state_ids:
        return ["procedure must contain at least one state"]

    current = [state.id for state in definition.states if state.is_current]
    if len(current) != 1:
        errors.append("procedure must define exactly one current state")

    outgoing: dict[str, set[str]] = {state_id: set() for state_id in state_ids}
    incoming: dict[str, set[str]] = {state_id: set() for state_id in state_ids}
    transition_ids: set[str] = set()
    for transition in definition.transitions:
        if transition.id in transition_ids:
            errors.append(f"duplicate transition id: {transition.id}")
        transition_ids.add(transition.id)
        if transition.source_id not in state_set or transition.target_id not in state_set:
            errors.append(f"transition {transition.id} references an unknown state")
            continue
        if transition.guard != "always" and not _GUARD_RE.fullmatch(transition.guard):
            errors.append(f"transition {transition.id} has an unsupported guard")
        if transition.active:
            outgoing[transition.source_id].add(transition.target_id)
            incoming[transition.target_id].add(transition.source_id)

    for state_id in state_ids:
        if not outgoing[state_id]:
            errors.append(f"state {state_id} has no active outgoing transition")

    if not errors:
        start = state_ids[0]
        if _reachable(start, outgoing) != state_set or _reachable(start, incoming) != state_set:
            errors.append("active procedure graph must be strongly connected")

    policy_ids = {policy.state_id for policy in definition.guardrails.capability_policies}
    missing_policies = state_set - policy_ids
    if missing_policies:
        errors.append(f"missing capability policies: {', '.join(sorted(missing_policies))}")

    preset_ids = [preset.id for preset in definition.access_presets]
    if len(preset_ids) != len(set(preset_ids)):
        errors.append("access preset ids must be unique")
    for state in definition.states:
        try:
            access = resolve_state_access(definition, state)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        allowed = set(access.allowed_tool_patterns)
        if not access.tools:
            errors.append(f"state {state.id} resolves to no tools")
        if not access.skills:
            errors.append(f"state {state.id} resolves to no skills")
        if access.tools and not allowed:
            errors.append(f"state {state.id} has tools but no allowed tool patterns")
        if state.access_preset_id:
            for tool in access.tools:
                if allowed and not any(fnmatch.fnmatchcase(tool, pattern) for pattern in allowed):
                    errors.append(f"state {state.id} tool {tool} is outside its allowed tool patterns")

    model_ids = {model.id for model in definition.models if model.enabled}
    if not model_ids:
        errors.append("procedure must contain at least one enabled model")
    for state in definition.states:
        if state.preferred_model_id and state.preferred_model_id not in model_ids:
            errors.append(
                f"state {state.id} references unavailable model {state.preferred_model_id}"
            )
    return errors


def guard_matches(guard: str, output: RunOutput | None) -> bool:
    if guard == "always":
        return True
    if output is None or output.payload is None:
        return False
    match = _GUARD_RE.fullmatch(guard)
    if not match:
        return False
    field, operator, raw_value = match.groups()
    payload = output.payload.model_dump()
    if field not in payload:
        return False
    expected: Any
    if raw_value == "true":
        expected = True
    elif raw_value == "false":
        expected = False
    else:
        expected = raw_value
    return (payload[field] == expected) if operator == "==" else (payload[field] != expected)


def choose_transition(transitions: list[Transition], source_id: str, output: RunOutput | None = None) -> Transition:
    eligible = [
        transition
        for transition in transitions
        if transition.source_id == source_id and transition.active and guard_matches(transition.guard, output)
    ]
    if not eligible:
        raise RuntimeError(f"state {source_id!r} has no matching active transition")
    return sorted(eligible, key=lambda transition: (-transition.weight, transition.target_id))[0]


def _reachable(start: str, graph: dict[str, set[str]]) -> set[str]:
    seen: set[str] = set()
    stack = [start]
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(graph[node] - seen)
    return seen
