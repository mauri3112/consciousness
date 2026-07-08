from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from .models import RunRecord


class OnlyMemoriesClient:
    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def health(self) -> dict[str, Any]:
        response = httpx.get(f"{self.base_url}/health", timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def search(self, query: str, limit: int = 8) -> dict[str, Any]:
        response = httpx.post(
            f"{self.base_url}/search",
            json={"query": query, "limit": limit},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def remember_run_recap(self, run: RunRecord, state_name: str) -> dict[str, Any]:
        response = httpx.post(
            f"{self.base_url}/memories",
            json={
                "type": "artifact",
                "content": (
                    f"Consciousness run {run.id} finished state {state_name}. "
                    f"Final thoughts: {run.final_thoughts or 'not recorded'}"
                ),
                "source": "consciousness",
                "happened_at": datetime.now(timezone.utc).isoformat(),
                "base_importance": 0.55,
                "metadata": {
                    "run_id": run.id,
                    "state_id": run.state_id,
                    "model_id": run.model_id,
                    "context_window": run.context_window,
                    "context_used": run.context_used,
                    "changes": run.changes,
                },
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()
