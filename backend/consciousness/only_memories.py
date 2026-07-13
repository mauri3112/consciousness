from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from .models import RunRecord


class RemoteWriteUncertain(RuntimeError):
    """The remote server may have committed a write before the response failed."""


class OnlyMemoriesClient:
    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def health(self) -> dict[str, Any]:
        response = httpx.get(f"{self.base_url}/health", timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def search(
        self,
        query: str,
        limit: int = 8,
        *,
        memory_type: str | None = None,
        scope: str = "general",
        include_forgotten: bool = False,
        include_expired: bool = False,
        intent: str = "answer",
        space_ids: list[str] | None = None,
        planes: list[str] | None = None,
        memory_types: list[str] | None = None,
        exclude_types: list[str] | None = None,
        provenance_classes: list[str] | None = None,
        verification_statuses: list[str] | None = None,
        include_generated: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "query": query,
            "limit": limit,
            "scope": scope,
            "include_forgotten": include_forgotten,
            "include_expired": include_expired,
            "intent": intent,
            "space_ids": space_ids or [],
            "planes": planes or ["knowledge"],
            "types": memory_types or [],
            "exclude_types": exclude_types or [],
            "provenance_classes": provenance_classes or [],
            "verification_statuses": verification_statuses or [],
            "include_generated": include_generated,
        }
        if memory_type is not None:
            payload["type"] = memory_type
        response = httpx.post(
            f"{self.base_url}/search",
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def navigate(self, memory_id: str, limit: int = 8) -> dict[str, Any]:
        response = httpx.get(
            f"{self.base_url}/memories/{memory_id}/navigate",
            params={"limit": limit},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def versions(self, memory_id: str) -> dict[str, Any]:
        response = httpx.get(f"{self.base_url}/memories/{memory_id}/versions", timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def _write(
        self,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        request_kwargs: dict[str, Any] = {"timeout": self.timeout}
        if payload is not None:
            request_kwargs["json"] = payload
        if idempotency_key:
            request_kwargs["headers"] = {"Idempotency-Key": idempotency_key}
        try:
            response = httpx.post(f"{self.base_url}{path}", **request_kwargs)
            response.raise_for_status()
            return response.json()
        except (httpx.TimeoutException, httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError) as exc:
            raise RemoteWriteUncertain(str(exc)) from exc

    def remember(self, payload: dict[str, Any], idempotency_key: str | None = None) -> dict[str, Any]:
        return self._write("/memories", payload=payload, idempotency_key=idempotency_key)

    def supersede(self, memory_id: str, payload: dict[str, Any], idempotency_key: str | None = None) -> dict[str, Any]:
        return self.remember({**payload, "supersedes_id": memory_id}, idempotency_key)

    def forget(self, memory_id: str, reason: str | None = None, idempotency_key: str | None = None) -> dict[str, Any]:
        return self._write(
            f"/memories/{memory_id}/forget",
            payload={"reason": reason} if reason else {},
            idempotency_key=idempotency_key,
        )

    def restore(self, memory_id: str, idempotency_key: str | None = None) -> dict[str, Any]:
        return self._write(f"/memories/{memory_id}/restore", idempotency_key=idempotency_key)

    def reinforce(
        self,
        source_id: str,
        target_id: str,
        amount: float,
        reason: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self._write(
            "/connections/reinforce",
            payload={"source_id": source_id, "target_id": target_id, "amount": amount, "reason": reason},
            idempotency_key=idempotency_key,
        )

    def remember_run_recap(
        self, run: RunRecord, state_name: str, *, space_id: str = "consciousness:runs"
    ) -> dict[str, Any]:
        response = httpx.post(
            f"{self.base_url}/memories",
            json={
                "type": "artifact",
                "content": (
                    f"Consciousness run {run.id} finished state {state_name}. "
                    f"Final thoughts: {run.final_thoughts or 'not recorded'}"
                ),
                "source": "consciousness",
                "space_id": space_id,
                "plane": "activity",
                "provenance_class": "agent_recap",
                "verification_status": "unverified",
                "producer": "consciousness",
                "origin_run_id": run.id,
                "derivation_depth": 1,
                "external_key": f"consciousness:{run.id}:recap",
                "happened_at": datetime.now(timezone.utc).isoformat(),
                "base_importance": 0.55,
                "metadata": {
                    "run_id": run.id,
                    "state_id": run.state_id,
                    "model_id": run.model_id,
                    "context_window": run.context_window,
                    "context_used": run.context_used,
                    "changes": run.changes,
                    "structured_output": run.output.model_dump(mode="json") if run.output else None,
                },
            },
            timeout=self.timeout,
            headers={"Idempotency-Key": f"consciousness:{run.id}:recap"},
        )
        response.raise_for_status()
        return response.json()
