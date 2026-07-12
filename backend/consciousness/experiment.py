from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_now() -> str:
    return utc_now().isoformat()


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")
    temporary.replace(path)


def append_jsonl(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        stream.write(json.dumps(value, sort_keys=True, default=str) + "\n")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sqlite_backup(source: Path, destination: Path) -> None:
    if not source.exists():
        raise RuntimeError(f"database source is missing: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as source_db:
        with sqlite3.connect(destination) as destination_db:
            source_db.backup(destination_db)
            destination_db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            destination_db.execute("PRAGMA journal_mode = DELETE")
            integrity = destination_db.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise RuntimeError(f"backup integrity failed for {destination}: {integrity}")


class Api:
    def __init__(self, base_url: str, token: str | None = None, timeout: float = 30) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.client = httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=timeout)

    def get(self, path: str, **params: object) -> Any:
        response = self.client.get(path, params=params or None)
        response.raise_for_status()
        return response.json()

    def post(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        response = self.client.post(path, json=payload) if payload is not None else self.client.post(path)
        response.raise_for_status()
        return response.json()


class MemoryExperiment:
    def __init__(self) -> None:
        self.experiment_id = os.getenv("EXPERIMENT_ID", "memory-stewardship-v1")
        self.output_root = Path(os.getenv("EXPERIMENT_OUTPUT_ROOT", "./data/experiments")) / self.experiment_id
        self.fixture_path = Path(os.getenv("EXPERIMENT_FIXTURE_PATH", "./experiments/memory-stewardship-v1/fixtures.json"))
        self.consciousness_db = Path(os.getenv("EXPERIMENT_CONSCIOUSNESS_DB", "./data/consciousness.db"))
        self.only_memories_db = Path(os.getenv("EXPERIMENT_ONLY_MEMORIES_DB", "../only-memories/backend/data/only-memories.sqlite3"))
        self.required_model = os.getenv("EXPERIMENT_REQUIRED_MODEL", "qwen3.5:9b")
        self.agent_interval = int(os.getenv("EXPERIMENT_AGENT_INTERVAL_SECONDS", "300"))
        self.duration_seconds = int(os.getenv("EXPERIMENT_DURATION_SECONDS", "28800"))
        self.backup_interval = int(os.getenv("EXPERIMENT_BACKUP_INTERVAL_SECONDS", "1800"))
        self.snapshot_interval = int(os.getenv("EXPERIMENT_SNAPSHOT_INTERVAL_SECONDS", "900"))
        self.poll_seconds = int(os.getenv("EXPERIMENT_POLL_SECONDS", "15"))
        token = os.getenv("CONSCIOUSNESS_API_TOKEN") or None
        self.consciousness = Api(os.getenv("CONSCIOUSNESS_API_URL", "http://localhost:8770"), token)
        self.memories = Api(os.getenv("ONLY_MEMORIES_URL", "http://localhost:8765"))
        self.ollama = Api(os.getenv("OLLAMA_URL", "http://localhost:11434"), timeout=120)
        self.state_path = self.output_root / "state.json"
        self.status_path = self.output_root / "status.json"
        self.timeline_path = self.output_root / "timeline.jsonl"
        self.fixtures = json.loads(self.fixture_path.read_text())
        self.state = self._load_state()

    def _load_state(self) -> dict[str, Any]:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text())
        return {
            "experiment_id": self.experiment_id,
            "started_at": iso_now(),
            "completed_phases": [],
            "memory_ids": {},
            "backup_sequence": 0,
            "snapshot_sequence": 0,
            "status": "initializing",
        }

    def _save_state(self) -> None:
        atomic_json(self.state_path, self.state)

    def event(self, kind: str, **details: object) -> None:
        append_jsonl(self.timeline_path, {"at": iso_now(), "kind": kind, **details})

    def elapsed_seconds(self) -> float:
        started = datetime.fromisoformat(self.state["started_at"])
        return max(0.0, (utc_now() - started).total_seconds())

    def validate_environment(self) -> dict[str, Any]:
        ready = self.consciousness.get("/api/v1/ready")
        runtime = self.consciousness.get("/api/v1/runtime")
        procedure = self.consciousness.get("/api/v1/procedure")
        memory_health = self.memories.get("/health")
        tags = self.ollama.get("/api/tags")
        resident = self.ollama.get("/api/ps")
        installed = {item["name"] for item in tags.get("models", [])}
        running = [item["name"] for item in resident.get("models", [])]
        states = procedure.get("version", {}).get("definition", {}).get("states", [])
        configured_local = {
            item["model"]
            for item in self.consciousness.get("/api/v1/models")
            if item.get("enabled") and item.get("provider") in {"ollama", "local"}
        }
        problems = []
        if runtime.get("execution_mode") != "live":
            problems.append("Consciousness is not in live execution mode")
        if runtime.get("interval_seconds") != self.agent_interval:
            problems.append(
                f"agent interval must be {self.agent_interval} seconds, got {runtime.get('interval_seconds')}"
            )
        if len(states) < 3:
            problems.append("The active graph does not contain multiple agent states")
        if self.required_model not in installed:
            problems.append(f"required Ollama model is not installed: {self.required_model}")
        if configured_local != {self.required_model}:
            problems.append(f"enabled local profiles must select only {self.required_model}: {sorted(configured_local)}")
        if len(running) > 1 or any(name != self.required_model for name in running):
            problems.append(f"Ollama must have at most one resident model and it must be {self.required_model}: {running}")
        if problems:
            raise RuntimeError("; ".join(problems))
        return {
            "ready": ready,
            "runtime": runtime,
            "only_memories": memory_health,
            "installed_models": sorted(installed),
            "resident_models": running,
            "configured_local_models": sorted(configured_local),
            "agent_states": [{"id": item["id"], "name": item["name"], "kind": item["kind"]} for item in states],
        }

    def initialize(self) -> None:
        self.output_root.mkdir(parents=True, exist_ok=True)
        if not (self.output_root / "fixtures.json").exists():
            shutil.copy2(self.fixture_path, self.output_root / "fixtures.json")
        response = self.consciousness.client.put(
            "/api/v1/runtime/interval", json={"interval_seconds": self.agent_interval}
        )
        response.raise_for_status()
        environment = self.validate_environment()
        if not (self.output_root / "manifest.json").exists():
            atomic_json(
                self.output_root / "manifest.json",
                {
                    "experiment_id": self.experiment_id,
                    "created_at": iso_now(),
                    "purpose": self.fixtures["purpose"],
                    "configuration": {
                        "duration_seconds": self.duration_seconds,
                        "agent_interval_seconds": self.agent_interval,
                        "backup_interval_seconds": self.backup_interval,
                        "snapshot_interval_seconds": self.snapshot_interval,
                        "required_model": self.required_model,
                    },
                    "environment": environment,
                    "fixture_sha256": sha256(self.output_root / "fixtures.json"),
                },
            )
            self.backup("before-seeding")
            self.event("experiment.initialized", environment=environment)
        self.state["status"] = "running"
        self._save_state()

    def inject_due_phases(self) -> list[str]:
        injected: list[str] = []
        elapsed_minutes = self.elapsed_seconds() / 60
        completed = set(self.state["completed_phases"])
        for phase in self.fixtures["phases"]:
            if phase["id"] in completed or float(phase["after_minutes"]) > elapsed_minutes:
                continue
            for fixture in phase["memories"]:
                self._create_memory(phase["id"], fixture)
            self.state["completed_phases"].append(phase["id"])
            self._save_state()
            self.event("memory.phase_injected", phase_id=phase["id"], memory_count=len(phase["memories"]))
            injected.append(phase["id"])
        return injected

    def _create_memory(self, phase_id: str, fixture: dict[str, Any]) -> None:
        key = fixture["key"]
        if key in self.state["memory_ids"]:
            return
        payload = {name: value for name, value in fixture.items() if name not in {"key", "supersedes_key", "expires_after_minutes"}}
        metadata = dict(payload.get("metadata", {}))
        metadata.update({"experiment_id": self.experiment_id, "fixture_key": key, "injection_phase": phase_id})
        payload["metadata"] = metadata
        payload.setdefault("source", f"consciousness-experiment:{self.experiment_id}")
        payload.setdefault("source_links", []).append(
            {"label": f"Experiment fixture {key}", "kind": "fixture", "uri": f"experiment://{self.experiment_id}/{key}"}
        )
        if fixture.get("supersedes_key"):
            payload["supersedes_id"] = self.state["memory_ids"][fixture["supersedes_key"]]
        if fixture.get("expires_after_minutes") is not None:
            payload["expires_at"] = (utc_now() + timedelta(minutes=float(fixture["expires_after_minutes"]))).isoformat()
        for connection in payload.get("connections", []):
            target_key = connection.pop("target_key", None)
            if target_key:
                connection["target_id"] = self.state["memory_ids"][target_key]
        result = self.memories.post("/memories", payload)
        self.state["memory_ids"][key] = result["id"]
        self._save_state()
        self.event("memory.created", phase_id=phase_id, fixture_key=key, memory_id=result["id"])

    def backup(self, reason: str) -> Path:
        sequence = int(self.state["backup_sequence"]) + 1
        stamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
        directory = self.output_root / "backups" / f"{sequence:04d}-{stamp}-{reason}"
        consciousness_path = directory / "consciousness.db"
        memories_path = directory / "only-memories.sqlite3"
        sqlite_backup(self.consciousness_db, consciousness_path)
        sqlite_backup(self.only_memories_db, memories_path)
        atomic_json(
            directory / "manifest.json",
            {
                "created_at": iso_now(),
                "reason": reason,
                "files": {
                    consciousness_path.name: {"sha256": sha256(consciousness_path), "bytes": consciousness_path.stat().st_size},
                    memories_path.name: {"sha256": sha256(memories_path), "bytes": memories_path.stat().st_size},
                },
                "completed_phases": list(self.state["completed_phases"]),
                "memory_ids": dict(self.state["memory_ids"]),
            },
        )
        self.state["backup_sequence"] = sequence
        self.state["last_backup_at"] = iso_now()
        self._save_state()
        self.event("backup.created", sequence=sequence, reason=reason, path=str(directory))
        return directory

    def snapshot(self, reason: str) -> Path:
        environment = self.validate_environment()
        searches = {}
        for probe in self.fixtures["search_probes"]:
            searches[probe] = self.memories.post("/search", {"query": probe, "limit": 10, "scope": "remembering"})
        memories = self.memories.get(
            "/memories", limit=200, include_versions="true", include_forgotten="true", include_expired="true"
        )
        runs = self.consciousness.get("/api/v1/runs", limit=200)
        payload = {
            "captured_at": iso_now(),
            "reason": reason,
            "elapsed_seconds": self.elapsed_seconds(),
            "environment": environment,
            "metrics": self.consciousness.get("/api/v1/metrics"),
            "integrations": self.consciousness.get("/api/v1/integrations"),
            "approvals": self.consciousness.get("/api/v1/approvals", limit=200),
            "runs": runs,
            "run_summary": {
                "total_captured": len(runs),
                "succeeded": sum(item["status"] == "succeeded" for item in runs),
                "failed": sum(item["status"] in {"failed", "interrupted", "blocked"} for item in runs),
                "by_state": {state: sum(item["state_id"] == state for item in runs) for state in sorted({item["state_id"] for item in runs})},
                "by_model": {model: sum(item["model_id"] == model for item in runs) for model in sorted({item["model_id"] for item in runs})},
            },
            "memory_summary": {
                "total_captured": len(memories),
                "current": sum(item["is_current"] for item in memories),
                "forgotten": sum(item["is_forgotten"] for item in memories),
                "experiment_memories": sum(item.get("metadata", {}).get("experiment_id") == self.experiment_id for item in memories),
            },
            "memories": memories,
            "search_probes": searches,
            "completed_phases": list(self.state["completed_phases"]),
        }
        sequence = int(self.state["snapshot_sequence"]) + 1
        stamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
        path = self.output_root / "snapshots" / f"{sequence:04d}-{stamp}-{reason}.json"
        atomic_json(path, payload)
        self.state["snapshot_sequence"] = sequence
        self.state["last_snapshot_at"] = payload["captured_at"]
        self._save_state()
        self.event("snapshot.created", sequence=sequence, reason=reason, path=str(path))
        return path

    def update_status(self, error: str | None = None) -> None:
        next_phases = [
            {"id": phase["id"], "after_minutes": phase["after_minutes"]}
            for phase in self.fixtures["phases"]
            if phase["id"] not in self.state["completed_phases"]
        ]
        status = {
            "experiment_id": self.experiment_id,
            "status": self.state["status"],
            "heartbeat_at": iso_now(),
            "started_at": self.state["started_at"],
            "elapsed_seconds": self.elapsed_seconds(),
            "duration_seconds": self.duration_seconds,
            "completed_phases": self.state["completed_phases"],
            "next_phase": next_phases[0] if next_phases else None,
            "backup_sequence": self.state["backup_sequence"],
            "snapshot_sequence": self.state["snapshot_sequence"],
            "last_backup_at": self.state.get("last_backup_at"),
            "last_snapshot_at": self.state.get("last_snapshot_at"),
            "error": error,
        }
        atomic_json(self.status_path, status)

    def run(self) -> None:
        try:
            self.initialize()
            injected = self.inject_due_phases()
            self.snapshot("after-" + "-".join(injected) if injected else "resume")
            self.backup("after-initial-seed" if injected else "resume")
            self.consciousness.post("/api/v1/control/resume")
            self.event("consciousness.resumed")
            self.update_status()
            last_backup = time.monotonic()
            last_snapshot = time.monotonic()
            while self.elapsed_seconds() < self.duration_seconds:
                time.sleep(self.poll_seconds)
                injected = self.inject_due_phases()
                if injected or time.monotonic() - last_snapshot >= self.snapshot_interval:
                    self.snapshot("after-" + "-".join(injected) if injected else "periodic")
                    last_snapshot = time.monotonic()
                if injected or time.monotonic() - last_backup >= self.backup_interval:
                    self.backup("after-" + "-".join(injected) if injected else "periodic")
                    last_backup = time.monotonic()
                self.update_status()
            self.consciousness.post("/api/v1/control/pause")
            self.state["status"] = "completed"
            self.state["completed_at"] = iso_now()
            self._save_state()
            self.snapshot("final")
            self.backup("final")
            self.update_status()
            self.event("experiment.completed")
        except Exception as exc:
            self.state["status"] = "failed"
            self.state["failed_at"] = iso_now()
            self.state["error"] = str(exc)
            self._save_state()
            self.update_status(str(exc))
            self.event("experiment.failed", error=str(exc))
            try:
                self.consciousness.post("/api/v1/control/pause")
            except Exception:
                pass
            raise


def health(output_root: Path | None = None, experiment_id: str | None = None) -> None:
    root = output_root or Path(os.getenv("EXPERIMENT_OUTPUT_ROOT", "./data/experiments"))
    path = root / (experiment_id or os.getenv("EXPERIMENT_ID", "memory-stewardship-v1")) / "status.json"
    if not path.exists():
        raise SystemExit("experiment status does not exist")
    status = json.loads(path.read_text())
    heartbeat = datetime.fromisoformat(status["heartbeat_at"])
    if status["status"] == "failed":
        raise SystemExit(status.get("error") or "experiment failed")
    if status["status"] == "running" and (utc_now() - heartbeat).total_seconds() > 120:
        raise SystemExit("experiment heartbeat is stale")
    print(json.dumps(status, indent=2))


def experiment_cli() -> None:
    parser = argparse.ArgumentParser(description="Run and inspect the durable local memory-stewardship experiment.")
    parser.add_argument("command", choices=["run", "health", "snapshot", "backup"])
    args = parser.parse_args()
    if args.command == "health":
        health()
        return
    experiment = MemoryExperiment()
    if args.command == "run":
        experiment.run()
    elif args.command == "snapshot":
        print(experiment.snapshot("manual"))
    elif args.command == "backup":
        print(experiment.backup("manual"))
