# only-memories Integration

Consciousness and only-memories are sibling projects.

Consciousness should not import only-memories internals. It integrates through public surfaces:

- HTTP API at `http://localhost:8765`.
- MCP tools such as `remember`, `recall`, `navigate_memory`, and `reinforce_connection`.
- Source links that point back to files, run records, dashboard URLs, or local resources.

The local v1 HTTP adapter covers health, ranked search, navigation, version history, create, soft-forget, restore, and connection reinforcement. Every mutating call is recorded with an idempotency key and sends that key to only-memories. Ambiguous transport failures become `uncertain` and remain fenced until explicit remote reconciliation; destructive lifecycle actions require approval.

## Suggested Flow

1. Gather state searches only-memories for relevant memories and graph neighbors.
2. Curate state proposes lifecycle changes such as merge, supersede, soft forget, or reinforce.
3. Synthesize state writes bridge artifacts as `artifact`, `concept`, or `decision` memories.
4. Validate state checks old versions, contradictions, and sources.
5. Publish state commits accepted memories and links them to Consciousness run ids.
6. Audit state evaluates whether the procedure and model choices are improving memory quality.

## Adapter Defaults

Environment variables:

```bash
# Processes running directly on the host:
ONLY_MEMORIES_URL=http://localhost:8765
ONLY_MEMORIES_WRITE_RECAPS=false

# Compose interpolation; the container receives this as ONLY_MEMORIES_URL:
COMPOSE_ONLY_MEMORIES_URL=http://host.docker.internal:8765
```

The write flag is off by default so the scaffold can run safely against an existing memory database. When enabled, completed Consciousness runs can be written as `artifact` memories with run metadata.
Compose deliberately uses a separate interpolation variable so copying the host-oriented `.env.example`
does not replace the container's host-gateway URL with an unreachable container-local `localhost` URL.
The full normal-volume integration check is `python3 scripts/verify-live-cycle.py` after starting Compose
in live mode as documented in the operator runbook.

## Memory Metadata Shape

```json
{
  "source": "consciousness",
  "type": "artifact",
  "metadata": {
    "run_id": "run_abc123",
    "state_id": "synthesize",
    "model_id": "openai/gpt-4.1-mini",
    "context_window": 128000,
    "context_used": 92142,
    "changes": []
  }
}
```

## Independence Boundary

only-memories should remain useful as a memory graph without Consciousness. Consciousness should remain useful as a procedure harness without only-memories.

The integration is a configured collaboration, not a hard dependency.

If Gather health succeeds but its search fails, Consciousness records a degraded integration event and
persists the missing memory context as an unresolved run risk instead of silently treating it as empty evidence.

## Lifecycle Acceptance

Run `backend/.venv/bin/python scripts/verify-only-memories-lifecycle.py --execute` to exercise the
real HTTP adapter and Consciousness approval boundary with uniquely tagged local fixtures. The check
covers create, approval-gated supersede, reinforce, approval-gated soft forget, restore, search,
navigation, and version history without editing the sibling repository.
