from __future__ import annotations

from datetime import datetime, timezone

from .config import get_settings
from .store import ConsciousnessStore


def worker_health() -> None:
    settings = get_settings()
    store = ConsciousnessStore(settings.database_path, execution_mode=settings.execution_mode)
    store.setup()
    runtime = store.runtime()
    if not runtime.worker_id or not runtime.heartbeat_at:
        raise SystemExit("worker lease is absent")
    age = (datetime.now(timezone.utc) - runtime.heartbeat_at).total_seconds()
    if age > settings.worker_lease_seconds * 2:
        raise SystemExit(f"worker heartbeat is stale ({age:.1f}s)")

