from __future__ import annotations

from typing import Any

import httpx
import pytest

from consciousness.only_memories import OnlyMemoriesClient, RemoteWriteUncertain


class FakeResponse:
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload or {"status": "ok"}
        self.raise_called = False

    def raise_for_status(self) -> None:
        self.raise_called = True

    def json(self) -> dict[str, Any]:
        assert self.raise_called
        return self.payload


def test_read_contracts_use_exact_paths_and_parameters(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def get(url: str, **kwargs: Any) -> FakeResponse:
        calls.append(("GET", url, kwargs))
        return FakeResponse()

    def post(url: str, **kwargs: Any) -> FakeResponse:
        calls.append(("POST", url, kwargs))
        return FakeResponse()

    monkeypatch.setattr("consciousness.only_memories.httpx.get", get)
    monkeypatch.setattr("consciousness.only_memories.httpx.post", post)
    client = OnlyMemoriesClient("http://memory.test/", timeout=2.5)

    client.health()
    client.search(
        "durable agents",
        12,
        memory_type="project",
        scope="remembering",
        include_forgotten=True,
        include_expired=True,
    )
    client.navigate("memory-1", 4)
    client.versions("memory-1")

    assert calls == [
        ("GET", "http://memory.test/health", {"timeout": 2.5}),
        (
            "POST",
            "http://memory.test/search",
            {
                "json": {
                    "query": "durable agents",
                    "limit": 12,
                    "type": "project",
                    "scope": "remembering",
                    "include_forgotten": True,
                        "include_expired": True,
                        "intent": "answer",
                        "space_ids": [],
                        "planes": ["knowledge"],
                        "types": [],
                        "exclude_types": [],
                        "provenance_classes": [],
                        "verification_statuses": [],
                        "include_generated": False,
                },
                "timeout": 2.5,
            },
        ),
        (
            "GET",
            "http://memory.test/memories/memory-1/navigate",
            {"params": {"limit": 4}, "timeout": 2.5},
        ),
        ("GET", "http://memory.test/memories/memory-1/versions", {"timeout": 2.5}),
    ]


def test_mutation_contracts_use_exact_paths_and_payloads(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def post(url: str, **kwargs: Any) -> FakeResponse:
        calls.append((url, kwargs))
        return FakeResponse({"id": "memory-new"})

    monkeypatch.setattr("consciousness.only_memories.httpx.post", post)
    client = OnlyMemoriesClient("http://memory.test", timeout=3)

    client.remember({"content": "new fact", "type": "note"})
    client.supersede("memory-old", {"content": "corrected fact", "source": "consciousness"})
    client.forget("memory-old", "stale")
    client.restore("memory-old")
    client.reinforce("memory-a", "memory-b", 0.25, "validated")

    assert calls == [
        (
            "http://memory.test/memories",
            {"json": {"content": "new fact", "type": "note"}, "timeout": 3},
        ),
        (
            "http://memory.test/memories",
            {
                "json": {
                    "content": "corrected fact",
                    "source": "consciousness",
                    "supersedes_id": "memory-old",
                },
                "timeout": 3,
            },
        ),
        (
            "http://memory.test/memories/memory-old/forget",
            {"json": {"reason": "stale"}, "timeout": 3},
        ),
        ("http://memory.test/memories/memory-old/restore", {"timeout": 3}),
        (
            "http://memory.test/connections/reinforce",
            {
                "json": {
                    "source_id": "memory-a",
                    "target_id": "memory-b",
                    "amount": 0.25,
                    "reason": "validated",
                },
                "timeout": 3,
            },
        ),
    ]


def test_write_propagates_idempotency_key_and_classifies_ambiguous_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def post(_url: str, **kwargs: Any) -> FakeResponse:
        calls.append(kwargs)
        if len(calls) == 2:
            raise httpx.ReadTimeout("response timed out after commit")
        return FakeResponse({"id": "memory-new"})

    monkeypatch.setattr("consciousness.only_memories.httpx.post", post)
    client = OnlyMemoriesClient("http://memory.test")

    client.remember({"content": "first"}, "stable-key")
    with pytest.raises(RemoteWriteUncertain, match="response timed out"):
        client.remember({"content": "second"}, "stable-key")

    assert calls[0]["headers"] == {"Idempotency-Key": "stable-key"}
    assert calls[1]["headers"] == {"Idempotency-Key": "stable-key"}
