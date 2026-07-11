from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import get_settings
from .operations import redact
from .store import ConsciousnessStore


def backup_cli() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Create a consistent Consciousness SQLite backup.")
    parser.add_argument("destination", type=Path, nargs="?")
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = (args.destination or settings.database_path.with_name(f"consciousness-{stamp}.backup.db")).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    store = ConsciousnessStore(settings.database_path, execution_mode=settings.execution_mode)
    store.setup()
    with store.connect() as source, sqlite3.connect(destination) as target:
        source.backup(target)
    print(destination)


def diagnostics_cli() -> None:
    settings = get_settings()
    store = ConsciousnessStore(settings.database_path, execution_mode=settings.execution_mode)
    store.setup()
    snapshot = store.snapshot()
    print(
        json.dumps(
            redact({
                "integrity": store.integrity_check(),
                "runtime": snapshot.runtime.model_dump(mode="json"),
                "active_version": snapshot.version.model_dump(mode="json", exclude={"definition"}),
                "recent_runs": [run.model_dump(mode="json") for run in snapshot.runs[:5]],
                "integrations": [item.model_dump(mode="json") for item in snapshot.integrations],
                "pending_approvals": [item.model_dump(mode="json") for item in snapshot.approvals if item.status == "pending"],
            }),
            indent=2,
            default=str,
        )
    )


def vacuum_cli() -> None:
    settings = get_settings()
    store = ConsciousnessStore(settings.database_path, execution_mode=settings.execution_mode)
    store.setup()
    with store.connect() as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("VACUUM")
