from __future__ import annotations

from copy import deepcopy
from typing import Any

from .models import ProcedureDefinition


def apply_procedure_patch(definition: ProcedureDefinition, operations: list[dict[str, Any]]) -> ProcedureDefinition:
    """Apply a deliberately small JSON-Patch subset to a procedure definition."""
    document = deepcopy(definition.model_dump(mode="json"))
    for operation in operations:
        op = operation.get("op")
        path = operation.get("path")
        if op not in {"add", "replace", "remove"} or not isinstance(path, str) or not path.startswith("/"):
            raise ValueError("procedure mutation supports only add, replace, and remove with JSON-pointer paths")
        tokens = [_decode(token) for token in path.removeprefix("/").split("/") if token]
        if not tokens or tokens[0] not in {"states", "transitions", "models", "guardrails", "name"}:
            raise ValueError("procedure mutation path is outside the versioned definition")
        parent, key = _resolve_parent(document, tokens)
        if isinstance(parent, list):
            if key == "-" and op == "add":
                parent.append(operation.get("value"))
                continue
            index = int(key)
            if op == "remove":
                parent.pop(index)
            elif op == "add":
                parent.insert(index, operation.get("value"))
            else:
                parent[index] = operation.get("value")
        elif isinstance(parent, dict):
            if op == "remove":
                parent.pop(key)
            else:
                parent[key] = operation.get("value")
        else:
            raise ValueError("procedure mutation path does not resolve to a container")
    return ProcedureDefinition.model_validate(document)


def _resolve_parent(document: Any, tokens: list[str]) -> tuple[Any, str]:
    current = document
    for token in tokens[:-1]:
        current = current[int(token)] if isinstance(current, list) else current[token]
    return current, tokens[-1]


def _decode(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")
