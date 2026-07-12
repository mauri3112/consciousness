#!/usr/bin/env python3
"""Run an explicit, auditable only-memories lifecycle acceptance check."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from consciousness.artifacts import ArtifactStore  # noqa: E402
from consciousness.guardrails import default_guardrails  # noqa: E402
from consciousness.only_memories import OnlyMemoriesClient  # noqa: E402
from consciousness.store import ConsciousnessStore  # noqa: E402
from consciousness.tools import ToolRegistry, build_tool_registry  # noqa: E402


def execute(
    registry: ToolRegistry,
    store: ConsciousnessStore,
    run_id: str,
    policy,
    name: str,
    arguments: dict[str, object],
    step: str,
) -> dict[str, object]:
    result = registry.execute(
        run_id=run_id,
        tool_name=name,
        arguments=arguments,
        policy=policy,
        step_key=step,
    )
    if result.approval_id:
        store.decide_approval(result.approval_id, True, "Explicit lifecycle acceptance approval")
        result = registry.execute_approved(store.get_tool_call_by_approval(result.approval_id))
    if result.status != "succeeded" or result.result is None:
        raise RuntimeError(f"{name} did not succeed: {result.status}")
    return result.result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.getenv("ONLY_MEMORIES_URL", "http://localhost:8765"))
    parser.add_argument("--execute", action="store_true", help="Required because this creates and mutates tagged test memories.")
    parser.add_argument("--allow-remote", action="store_true", help="Allow a non-loopback only-memories URL.")
    args = parser.parse_args()

    host = (urlparse(args.url).hostname or "").lower()
    if not args.allow_remote and host not in {"localhost", "127.0.0.1", "::1"}:
        parser.error("refusing a non-loopback URL without --allow-remote")
    if not args.execute:
        parser.error("--execute is required")

    verification_id = f"consciousness-lifecycle-{uuid.uuid4().hex}"
    client = OnlyMemoriesClient(args.url)
    health = client.health()
    with tempfile.TemporaryDirectory(prefix="consciousness-memory-acceptance-") as temp:
        temp_path = Path(temp)
        store = ConsciousnessStore(temp_path / "acceptance.db")
        store.setup()
        publish = store.get_state("publish")
        run = store.begin_run(publish, store.list_models()[0])
        registry = build_tool_registry(
            store,
            only_memories=client,
            artifacts=ArtifactStore(temp_path / "artifacts", store),
        )
        policy = next(
            item for item in default_guardrails().capability_policies if item.state_id == "publish"
        )
        common = {
            "type": "artifact",
            "source": "consciousness-lifecycle-verifier",
            "metadata": {"verification_id": verification_id, "temporary": True},
        }
        base = execute(
            registry,
            store,
            run.id,
            policy,
            "only_memories.remember",
            {**common, "content": f"Base lifecycle fixture {verification_id}"},
            "create-base",
        )
        replacement = execute(
            registry,
            store,
            run.id,
            policy,
            "only_memories.supersede",
            {
                **common,
                "memory_id": str(base["id"]),
                "content": f"Replacement lifecycle fixture {verification_id}",
            },
            "supersede-base",
        )
        reinforcement = execute(
            registry,
            store,
            run.id,
            policy,
            "only_memories.reinforce",
            {
                "source_id": str(replacement["id"]),
                "target_id": str(base["id"]),
                "amount": 0.1,
                "reason": verification_id,
            },
            "reinforce-chain",
        )
        forgotten = execute(
            registry,
            store,
            run.id,
            policy,
            "only_memories.forget",
            {"memory_id": str(replacement["id"]), "reason": verification_id},
            "forget-replacement",
        )
        if not forgotten.get("is_forgotten"):
            raise RuntimeError("forget did not mark the replacement forgotten")
        restored = execute(
            registry,
            store,
            run.id,
            policy,
            "only_memories.restore",
            {"memory_id": str(replacement["id"])},
            "restore-replacement",
        )
        if restored.get("is_forgotten"):
            raise RuntimeError("restore left the replacement forgotten")

        versions = client.versions(str(replacement["id"]))
        version_ids = {str(item["id"]) for item in versions.get("versions", [])}
        if {str(base["id"]), str(replacement["id"])} - version_ids:
            raise RuntimeError("version history does not contain both lifecycle fixtures")
        navigation = client.navigate(str(replacement["id"]), limit=10)
        search = client.search(verification_id, limit=10, include_forgotten=True)
        approvals = [item.model_dump(mode="json") for item in store.list_approvals(limit=10)]
        if len(approvals) != 2 or any(item["status"] != "executed" for item in approvals):
            raise RuntimeError("supersede and forget were not both approved and executed")

        print(
            json.dumps(
                {
                    "status": "ok",
                    "verification_id": verification_id,
                    "health": health,
                    "base_memory_id": base["id"],
                    "replacement_memory_id": replacement["id"],
                    "reinforcement": reinforcement,
                    "approvals": approvals,
                    "version_ids": sorted(version_ids),
                    "navigation_connection_count": len(navigation.get("connections", [])),
                    "search_result_count": len(search.get("results", [])),
                    "note": "Tagged fixtures remain restored because only-memories has no hard-delete contract.",
                },
                indent=2,
                default=str,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
