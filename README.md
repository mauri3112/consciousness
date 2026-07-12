# Consciousness

Consciousness is an always-running procedural loop for LLM agents.

The active production roadmap and resumable task ledger live in [`docs/implementation-plan.md`](docs/implementation-plan.md). Installation, upgrades, recovery, and the release checklist are in [`docs/operator-runbook.md`](docs/operator-runbook.md). Reusable coding, research, browser, data, memory, and governance capability envelopes are documented in [`docs/access-presets.md`](docs/access-presets.md).

The project defines a strongly connected directed graph of agent states. One agent runs at a time. Each state owns a domain, goal, prompt contract, tools, skills, context budget, model policy, and output contract. When a state finishes, it writes a visible durable result so the next state, an auditor, or a restarted process can continue from the last known point.

This is an agent harness, not a claim about machine sentience. The name intentionally teases a different angle on intelligence: not a single brilliant answer, but a durable loop that can notice its own state, conserve context, choose cheaper or stronger models, change procedure, and leave evidence for the next mind-state to inherit.

## Why This Exists

`only-memories` stores and ranks local-first memories. Consciousness is the loop that can tend that memory system continuously:

- gather high-signal context from only-memories and other tools,
- curate or merge memory candidates,
- synthesize bridge memories and recap artifacts,
- validate contradictions, provenance, and context pressure,
- publish durable state outputs,
- let a smarter auditor adapt the procedure when cheaper agents are failing.

The two projects are designed to work together, but neither one should require the other to boot. Consciousness integrates with only-memories through HTTP/MCP contracts and can run with a local SQLite store by itself.

## What Is Gold

- Durable agent state is the right primitive. Agent runs crash, context fills, and models change. The loop should survive all of that through DB-backed runs, recaps, procedure versions, and output artifacts.
- A graph ceremony is stronger than a linear cron job. Memory care needs feedback loops: audit can return to gather, validation can reopen synthesis, and budget pressure can choose simpler paths.
- One active agent at a time is a good default. It prevents write races and makes causality understandable before the project adds safe parallelism.
- Context window size must be first-class. Every state and run records the model context limit, used tokens, reserved output budget, and compression pressure.
- A smart auditor should optimize the procedure, not just judge outputs. It can downgrade models, remove useless states, add tools, tighten prompts, or escalate to a stronger model when evidence says the current loop is failing.
- Model choice should be a budgeted policy decision. The simplest model that can reliably finish the state should win.

## What Needs Guardrails

- "Full control" for the smart model is too broad without capability boundaries. Consciousness now has explicit capability policies per state and treats procedure mutation as a versioned, diffed, budget-limited proposal before it becomes applied control.
- "Never stop" should mean resilient, not reckless. The loop control policy includes manual pause, backoff, sleep-window intent, consecutive-failure limits, daily spend caps, health checks, and degraded local-only mode.
- Final thoughts are useful but not sufficient evidence. Runs now carry structured outputs: changed resources, source links, confidence, unresolved risks, and next-transition recommendations.
- A DB is necessary but not enough. Runs can point to stable artifact/source links so large files, code diffs, generated assets, and only-memories writes can stay inspectable without stuffing everything into one row.
- The name is a provocation, and that is fine. The project should use it to discuss intelligence as stateful self-governance, memory stewardship, budgeted model choice, and procedural adaptation rather than pretending the harness is sentient.

## Current Local v1 Foundation

- Python FastAPI backend.
- Ordered SQLite migrations, immutable procedure versions, durable runtime commands, a renewable worker lease, run events, approvals, artifacts, usage, and rollback records.
- Provider-neutral execution with explicit preview mode plus live OpenAI Responses and Ollama adapters.
- Guardrail-enforced tools and bounded automatic publishing for validated additive memory writes.
- Versioned agent access presets with structured filesystem, shell, network, external-write, and secret permissions; every run pins its resolved tools and skills.
- Optional only-memories HTTP adapter covering search, navigation, versions, writes, forgetting, restore, and connection reinforcement.
- React + Vite operator Studio with live controls, run evidence, approvals, mutation history, and visual procedure drafting.
- Example starter procedure and model registry.
- Docker Compose, CI, and tests.

## Quick Start

Backend:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
consciousness-api
```

The API runs at `http://localhost:8770`.

In a second backend shell, start the only process allowed to execute states:

```bash
consciousness-worker
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

The studio runs at `http://localhost:5173`.

Queue one durable step through the API:

```bash
curl -X POST http://localhost:8770/api/v1/control/step
```

Run continuously:

```bash
curl -X POST http://localhost:8770/api/v1/control/run
```

`CONSCIOUSNESS_EXECUTION_MODE=preview` is explicit and requires no provider. For the bundled local profile, run `ollama pull qwen3.5:9b`, set the mode to `live`, and make sure `OLLAMA_URL` is reachable. OpenAI execution additionally requires `OPENAI_API_KEY` and operator-confirmed model rows.

Docker:

```bash
docker compose up --build
```

Docker serves the studio at `http://localhost:5174` and the API at `http://localhost:8770`.
Compose reaches Ollama and only-memories on the host through `host.docker.internal`; local-process
settings in `.env.example` intentionally do not override those container URLs. Use
`.env.compose.example` as the template when Compose-specific URL overrides are needed.
When `CONSCIOUSNESS_API_TOKEN` is set, the Studio's same-origin proxy adds it server-side for normal
requests, live SSE updates, and procedure export. The credential is not embedded in the browser bundle.

For a repeatable live Ollama cycle against the normal persistent Compose volume:

```bash
CONSCIOUSNESS_EXECUTION_MODE=live docker compose up --build --force-recreate -d
python3 scripts/verify-live-cycle.py
```

The verifier pauses continuous execution, starts from the volume's current state, polls each durable
step command, proves all six canonical states succeed with `local/qwen3.5-9b`, and confirms the marker
returns to its starting state. See [`docs/operator-runbook.md`](docs/operator-runbook.md) for preflight
and safety details.

## Project Layout

```text
consciousness/
  backend/
    consciousness/
      api.py
      config.py
      guardrails.py
      llm.py
      models.py
      only_memories.py
      runner.py
      seed.py
      store.py
  frontend/
    src/
      App.tsx
      api.ts
      styles.css
  docs/
    architecture.md
    idea-review.md
    guardrails.md
    model-registry.md
    only-memories-integration.md
    open-source-success.md
    security-and-control.md
    state-contract.md
  examples/
    procedure.starter.json
    model-registry.json
```

## API Sketch

```bash
curl http://localhost:8770/health
curl http://localhost:8770/api/v1/procedure
curl http://localhost:8770/api/v1/runtime
curl -X POST http://localhost:8770/api/v1/control/step
```

Maintenance commands: `consciousness-backup`, `consciousness-diagnostics`, and `consciousness-vacuum`. API and worker entrypoints emit JSON logs with request/run identifiers and recursive credential redaction; diagnostics use the same redaction policy while preserving token-usage metrics.

For a restartable local memory-curation soak test with timed fixtures, ranking snapshots, single-model
Ollama enforcement, and paired SQLite backups, follow
[`docs/memory-stewardship-experiment.md`](docs/memory-stewardship-experiment.md).

## Open Source Success Criteria

Consciousness becomes useful when a new operator can install it locally, see the current loop, understand why each agent ran, inspect every procedure mutation, plug in open or closed models, enforce budgets, and connect it to only-memories without reading the source code first.

The project should make the invisible parts of agent orchestration visible: what state ran, what model was chosen, what context it saw, what it changed, how the auditor judged it, and why the next state follows.
