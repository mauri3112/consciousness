from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from consciousness.experiment import MemoryExperiment, sqlite_backup


def test_sqlite_backup_is_consistent(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    destination = tmp_path / "backups" / "copy.db"
    with sqlite3.connect(source) as database:
        database.execute("CREATE TABLE evidence(value TEXT NOT NULL)")
        database.execute("INSERT INTO evidence VALUES ('durable')")

    sqlite_backup(source, destination)

    with sqlite3.connect(destination) as database:
        assert database.execute("SELECT value FROM evidence").fetchone()[0] == "durable"
        assert database.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert database.execute("PRAGMA journal_mode").fetchone()[0] == "delete"


def test_fixture_references_require_prior_memory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fixture = {
        "purpose": "test",
        "search_probes": [],
        "phases": [{"id": "phase", "after_minutes": 0, "memories": []}],
    }
    fixture_path = tmp_path / "fixtures.json"
    fixture_path.write_text(json.dumps(fixture))
    monkeypatch.setenv("EXPERIMENT_FIXTURE_PATH", str(fixture_path))
    monkeypatch.setenv("EXPERIMENT_OUTPUT_ROOT", str(tmp_path / "output"))
    experiment = MemoryExperiment()
    experiment.memories = type("FakeMemories", (), {"post": lambda self, path, payload: {"id": "new-id"}})()

    with pytest.raises(KeyError):
        experiment._create_memory(
            "phase",
            {"key": "replacement", "supersedes_key": "missing", "type": "project", "content": "new"},
        )


def test_create_memory_resolves_connection_and_preserves_experiment_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fixture_path = tmp_path / "fixtures.json"
    fixture_path.write_text(json.dumps({"purpose": "test", "search_probes": [], "phases": []}))
    monkeypatch.setenv("EXPERIMENT_FIXTURE_PATH", str(fixture_path))
    monkeypatch.setenv("EXPERIMENT_OUTPUT_ROOT", str(tmp_path / "output"))
    monkeypatch.setenv("EXPERIMENT_ID", "experiment-test")
    experiment = MemoryExperiment()
    experiment.state["memory_ids"]["root"] = "memory-root"
    captured = {}

    class FakeMemories:
        def post(self, path, payload):
            captured.update(payload)
            return {"id": "memory-child"}

    experiment.memories = FakeMemories()
    experiment._create_memory(
        "phase",
        {
            "key": "child",
            "type": "note",
            "content": "child",
            "connections": [{"target_key": "root", "weight": 0.8, "relation": "supports"}],
        },
    )

    assert captured["connections"][0]["target_id"] == "memory-root"
    assert captured["metadata"] == {
        "experiment_id": "experiment-test",
        "fixture_key": "child",
        "injection_phase": "phase",
    }
    assert experiment.state["memory_ids"]["child"] == "memory-child"
