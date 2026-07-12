# Operator Runbook

This runbook covers clean installation, upgrades, recovery, live-provider acceptance, and the v1 release gate. Run commands from the repository root unless a step says otherwise.

## Prerequisites

- Docker Desktop with Compose.
- Ollama listening on `http://localhost:11434` for local live execution.
- `qwen3.5:9b` installed with `ollama pull qwen3.5:9b` for the bundled local model profile.
- The optional only-memories service on `http://localhost:8765` for the live memory vertical.
- An `OPENAI_API_KEY` plus operator-confirmed OpenAI model rows when exercising the OpenAI adapter.

Confirm the local dependencies:

```bash
ollama list
curl -fsS http://localhost:11434/api/tags
curl -fsS http://localhost:8765/health
```

## Clean Install

The default Compose mode is provider-free preview. Build and start a fresh stack, then verify every service:

```bash
docker compose config
docker compose up --build -d
docker compose ps
curl -fsS http://localhost:8770/api/v1/ready
curl -fsS http://localhost:8770/api/v1/runtime
```

The Studio is served at `http://localhost:5174`. Queue one durable step with:

```bash
curl -fsS -X POST http://localhost:8770/api/v1/control/step
```

For a token-authenticated stack, set the same `CONSCIOUSNESS_API_TOKEN` for Compose. Direct API calls
must send `Authorization: Bearer <token>`. The Studio continues to use same-origin `/api` URLs; Nginx
adds the token only on the server-side hop, including SSE and export, so it is absent from the bundle.

To intentionally erase the Compose database and rehearse a truly clean install, first create and export a backup. `docker compose down --volumes` permanently removes the current Compose data volume.

## Live Ollama Mode

The Compose services reach host Ollama and only-memories through `host.docker.internal` by default.
`.env.example` is for processes launched directly on the host and uses `localhost`; Compose uses the
separate `COMPOSE_OLLAMA_URL` and `COMPOSE_ONLY_MEMORIES_URL` interpolation names so those local values
cannot accidentally point a container back at itself. Use `.env.compose.example` as the template for
container-specific overrides.

Recreate the API and worker so an environment-only mode change is applied, then inspect the rendered
configuration and service health:

```bash
CONSCIOUSNESS_EXECUTION_MODE=live docker compose config
CONSCIOUSNESS_EXECUTION_MODE=live docker compose up --build --force-recreate -d
docker compose ps
curl -fsS http://localhost:8770/api/v1/runtime
```

For a persistent volume created before the bundled memory-safety, agent access presets, and `qwen3.5:9b` profile, apply the
upgrade as a new immutable procedure version with a mutation record and recap:

```bash
docker compose exec api consciousness-upgrade-bundled-profile --apply
```

The command is idempotent and preserves the current graph marker.

The rendered API and worker environment must show `CONSCIOUSNESS_EXECUTION_MODE: live`,
`OLLAMA_URL: http://host.docker.internal:11434`, and
`ONLY_MEMORIES_URL: http://host.docker.internal:8765`. The runtime endpoint is reconciled with the
configured execution mode when an existing persistent database is reopened.

Run the deterministic acceptance check:

```bash
python3 scripts/verify-live-cycle.py
```

Verify the complete memory lifecycle separately with explicitly tagged fixtures. This command
creates a base memory, approval-gates its replacement, reinforces the version chain, approval-gates
a soft forget, restores the replacement, and checks search/navigation/version evidence:

```bash
backend/.venv/bin/python scripts/verify-only-memories-lifecycle.py --execute
```

The verifier refuses non-loopback endpoints unless `--allow-remote` is supplied. Because
only-memories has no hard-delete contract, the restored fixtures remain tagged with their printed
`verification_id` for auditability.

The verifier first checks database readiness, only-memories health, the configured Ollama model, and
the worker lease. It pauses continuous execution, captures the volume's current state, queues six
steps one at a time, and polls each durable command. It then verifies that all six canonical states
ran exactly once in rotated order, every run succeeded with `local/qwen3.5-9b`, the marker returned to
its starting state, and the cycle recorded only-memories tool and artifact evidence. A slow machine can
use `--command-timeout 1800`; the default is 900 seconds per command.

Use the Studio or API to inspect the run output, changed resources, source links, context usage, provider request id, usage ledger, approvals, events, and transition after each step. Destructive memory operations must remain pending until an operator approves them.

## Agent Access Presets

Open **Access presets** in the Studio to inspect the bundled coding, review, research, browser, data, memory, and procedure-governance profiles. The catalog distinguishes configured tools from adapters registered in the current runtime. Before assigning a preset, treat any `unavailable` tool as non-executable.

Apply a preset in a procedure draft's State contract, optionally add or remove inherited tools, save, review the activation diff, validate, and activate. Activation saves current local form edits before server validation. Existing state-local contracts appear as `Custom (legacy)` and remain supported.

For API inspection:

```bash
curl -fsS http://localhost:8770/api/v1/access/catalog
```

Every subsequent run records `agent_access` with the resolved preset id, permissions, tools, skills, patterns, mutation level, approval requirement, and rationale. See [`access-presets.md`](access-presets.md) for the schema and custom-preset example.

## Memory Stewardship Soak Experiment

The bundled experiment runs Gather, Curate, Synthesize, Validate, Publish, and Audit as six sequential
agent roles using only `qwen3.5:9b`. It injects a controlled corpus over eight hours and captures both
databases, retrieval rankings, run evidence, Ollama residency, approvals, and integration health.

```bash
cp .env.experiment.example .env.experiment
docker compose --env-file .env.experiment --profile experiment up --build -d
docker compose --env-file .env.experiment --profile experiment ps
docker compose --env-file .env.experiment logs -f experiment
```

The experiment runner refuses to start unless live mode is active, the required model is installed,
the graph has multiple states, the only enabled local profile is the required model, and Ollama has at
most that one model resident. It resumes from its durable state after a failure. Full operating and
assessment instructions are in [`memory-stewardship-experiment.md`](memory-stewardship-experiment.md).

## Backup And Upgrade

Pause new work and create a consistent SQLite backup before changing images or code:

```bash
curl -fsS -X POST http://localhost:8770/api/v1/control/pause
mkdir -p backups
docker compose exec api consciousness-backup /data/pre-upgrade.backup.db
docker compose cp api:/data/pre-upgrade.backup.db backups/pre-upgrade.backup.db
```

Upgrade without removing the data volume:

```bash
git pull --ff-only
docker compose up --build -d
docker compose exec api consciousness-diagnostics
curl -fsS http://localhost:8770/api/v1/ready
```

Existing procedure versions are immutable and are not silently rewritten when bundled example models change. After an upgrade, review the active model registry in the Studio, create a draft when changes are needed, validate the diff, and activate it explicitly.
Activation compares the draft parent to the currently active version. A `stale_procedure_parent` conflict
requires creating/rebasing a draft from the new active version; stale drafts never overwrite newer work.

Only-memories writes propagate the durable tool-call idempotency key. A timeout after a write may have
committed remotely, so the call becomes `uncertain` and cannot replay until an operator reconciles it via
`POST /api/v1/tool-calls/{call_id}/reconcile` with the observed remote outcome.

## Restore Rehearsal

Restore only while the API and worker are stopped. Keep the failed database until the restored copy passes integrity and readiness checks.

```bash
docker compose stop api worker
docker compose cp backups/pre-upgrade.backup.db api:/data/restored.db
```

Promoting `restored.db` to `/data/consciousness.db` is an operator-controlled maintenance action. After promotion, restart the services, run `consciousness-diagnostics`, and confirm the active version, current state, recent run count, pending approvals, and integrity result match the backup.

## Release Checklist

- Backend: `cd backend && pytest && ruff check consciousness tests`.
- Frontend: `cd frontend && npm run build && npm run verify:operator` against a running Studio.
- Compose: config renders, all three services are healthy, and readiness succeeds.
- Providers: one configured Ollama cycle passes; one configured OpenAI cycle passes when OpenAI is enabled.
- Memory vertical: the six-state live cycle records durable output and bounded writes against only-memories.
- Recovery: interrupt/restart the worker during acceptance and confirm no lost state or duplicate tool writes.
- Upgrade: a populated pre-upgrade backup opens cleanly after rebuild and diagnostics report `integrity: ok`.
- Studio: desktop and mobile checks cover graph state, controls, approvals, rollback, disconnected/degraded behavior, and console health.
- Operations: backup, diagnostics, metrics, retention/VACUUM, and secret-redaction checks pass.
- Release: update `docs/implementation-plan.md` with dated evidence, document remaining limitations, then create the v1 tag.

## Current Limitations

- OpenAI live execution is optional but cannot be claimed as verified without a real API key and operator-confirmed model identifiers, context limits, capabilities, and pricing.
- Published model context is a capability ceiling; usable context and speed still depend on host memory and Ollama runtime configuration.
- only-memories is optional for boot but required for the full Gather-to-Audit memory acceptance path.
- A single SQLite worker owns execution. Horizontal worker concurrency is intentionally out of scope for local v1.
- The API is loopback-only by default. Non-loopback exposure requires an API token or an explicitly declared loopback-only reverse-proxy boundary.
- Procedure mutations and destructive memory actions remain approval-gated by design.
