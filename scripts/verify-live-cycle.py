#!/usr/bin/env python3
"""Verify one deterministic six-state cycle through the normal HTTP control plane."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any


CANONICAL_STATES = ["gather", "curate", "synthesize", "validate", "publish", "audit"]
LOCAL_MODEL_ID = "local/ornith-1.0-9b-q4"
LOCAL_MODEL_NAME = "hf.co/deepreinforce-ai/Ornith-1.0-9B-GGUF:Q4_K_M"
AUDIT_MODEL_ID = "minimax/MiniMax-M3"


class VerificationError(RuntimeError):
    pass


def request_json(base_url: str, path: str, *, method: str = "GET") -> Any:
    request = urllib.request.Request(f"{base_url.rstrip('/')}{path}", method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise VerificationError(f"{method} {path} returned HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise VerificationError(f"{method} {path} failed: {exc.reason}") from exc


def wait_for_command(base_url: str, command_id: int, timeout_seconds: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        command = request_json(base_url, f"/api/v1/commands/{command_id}")
        if command["status"] == "completed":
            return command
        if command["status"] == "failed":
            raise VerificationError(f"command {command_id} failed: {command.get('error') or 'unknown error'}")
        time.sleep(2)
    raise VerificationError(f"command {command_id} did not finish within {timeout_seconds:g} seconds")


def submit_and_wait(base_url: str, kind: str, timeout_seconds: float) -> dict[str, Any]:
    command = request_json(base_url, f"/api/v1/control/{kind}", method="POST")
    return wait_for_command(base_url, int(command["id"]), timeout_seconds)


def expected_cycle(start_state: str) -> list[str]:
    try:
        index = CANONICAL_STATES.index(start_state)
    except ValueError as exc:
        raise VerificationError(f"current state {start_state!r} is not in the canonical six-state procedure") from exc
    return CANONICAL_STATES[index:] + CANONICAL_STATES[:index]


def verify(args: argparse.Namespace) -> dict[str, Any]:
    ready = request_json(args.api_url, "/api/v1/ready")
    if ready.get("status") != "ready" or ready.get("database") != "ok":
        raise VerificationError(f"API is not ready with an intact database: {ready}")

    integration = request_json(args.api_url, "/api/v1/integrations/only-memories/test", method="POST")
    if integration.get("status") != "healthy":
        raise VerificationError(f"only-memories is not healthy: {integration}")

    model_health = request_json(args.api_url, f"/api/v1/models/{LOCAL_MODEL_ID}/test", method="POST")
    if model_health.get("status") not in {"ok", "healthy"}:
        raise VerificationError(f"{LOCAL_MODEL_ID} is not healthy: {model_health}")
    if "models" in model_health and LOCAL_MODEL_NAME not in model_health["models"]:
        raise VerificationError(f"Ollama is reachable but Ornith is not installed: {model_health}")
    audit_health = request_json(args.api_url, f"/api/v1/models/{AUDIT_MODEL_ID}/test", method="POST")
    if audit_health.get("status") not in {"configured", "healthy"}:
        raise VerificationError(f"{AUDIT_MODEL_ID} is not healthy: {audit_health}")

    submit_and_wait(args.api_url, "pause", args.command_timeout)
    runtime = request_json(args.api_url, "/api/v1/runtime")
    if runtime.get("execution_mode") != "live":
        raise VerificationError(
            f"runtime execution_mode is {runtime.get('execution_mode')!r}; recreate API and worker with live mode"
        )
    if not runtime.get("worker_id"):
        raise VerificationError("no worker lease is attached")

    start_state = runtime["current_state_id"]
    expected_states = expected_cycle(start_state)
    known_run_ids = {run["id"] for run in request_json(args.api_url, "/api/v1/runs?limit=200")}
    cycle_runs: list[dict[str, Any]] = []

    for expected_state in expected_states:
        submit_and_wait(args.api_url, "step", args.command_timeout)
        runs = request_json(args.api_url, "/api/v1/runs?limit=200")
        new_runs = [run for run in runs if run["id"] not in known_run_ids]
        if len(new_runs) != 1:
            raise VerificationError(
                f"expected exactly one new run for {expected_state}, found {len(new_runs)}; stop other operators and retry"
            )
        run = new_runs[0]
        if run["state_id"] != expected_state:
            raise VerificationError(f"expected state {expected_state}, got {run['state_id']}")
        if run["status"] != "succeeded":
            raise VerificationError(f"run {run['id']} ended with {run['status']}: {run.get('error_message')}")
        expected_model = AUDIT_MODEL_ID if expected_state == "audit" else LOCAL_MODEL_ID
        if run["model_id"] != expected_model:
            raise VerificationError(f"run {run['id']} used {run['model_id']} instead of {expected_model}")
        if not run.get("provider_request_id") or not run.get("output") or run.get("finished_at") is None:
            raise VerificationError(f"run {run['id']} is missing durable provider/output evidence")
        cycle_runs.append(run)
        known_run_ids.add(run["id"])
        print(f"PASS {expected_state}: {run['id']}", flush=True)

    final_runtime = request_json(args.api_url, "/api/v1/runtime")
    if final_runtime["current_state_id"] != start_state:
        raise VerificationError(
            f"cycle started at {start_state} but ended at {final_runtime['current_state_id']}"
        )

    tool_calls: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    for run in cycle_runs:
        tool_calls.extend(request_json(args.api_url, f"/api/v1/runs/{run['id']}/tools"))
        artifacts.extend(request_json(args.api_url, f"/api/v1/runs/{run['id']}/artifacts"))
    memory_tool_calls = [item for item in tool_calls if item.get("tool_name", "").startswith("only_memories.")]
    if not memory_tool_calls:
        raise VerificationError("cycle completed without a recorded only-memories tool call")
    if not artifacts:
        raise VerificationError("cycle completed without a recorded artifact")

    approvals = request_json(args.api_url, "/api/v1/approvals?limit=200")
    return {
        "status": "passed",
        "start_and_end_state": start_state,
        "states": [run["state_id"] for run in cycle_runs],
        "run_ids": [run["id"] for run in cycle_runs],
        "only_memories_tool_calls": len(memory_tool_calls),
        "artifacts": len(artifacts),
        "pending_approvals": sum(item.get("status") == "pending" for item in approvals),
        "database": ready["database"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://localhost:8770")
    parser.add_argument("--command-timeout", type=float, default=900)
    args = parser.parse_args()
    try:
        result = verify(args)
    except (VerificationError, KeyError, TypeError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
