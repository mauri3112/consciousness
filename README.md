# Consciousness

Consciousness is an always-running procedural loop for LLM agents.

The project defines a strongly connected directed graph of agent states. One agent runs at a time. Each state owns a domain, goal, prompt contract, tools, skills, context budget, model policy, and output contract. When a state finishes, it writes a visible durable result so the next state, an auditor, or a restarted process can continue from the last known point.

This is an agent harness, not a claim about machine sentience. The name is useful because the loop maintains an inspectable "state of mind": the active procedure, memory graph health, model choices, final agent thoughts, context pressure, and the recaps produced by the smart auditor.

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

- "Full control" for the smart model is too broad without capability boundaries. Procedure mutations should be versioned, diffed, budget-limited, and auditable.
- "Never stop" should mean resilient, not reckless. The loop needs backoff, sleep windows, spend caps, health checks, and a manual pause.
- Final thoughts are useful but not sufficient evidence. Agents should emit structured outputs, changed resources, confidence, unresolved risks, and source links.
- A DB is necessary but not enough. Large artifacts, code diffs, and generated files need stable source links or content-addressed storage.
- The project name can mislead people. The README and docs should consistently present this as an orchestration harness with memory state, not a consciousness claim.

## Current Scaffold

- Python FastAPI backend.
- SQLite store for procedure states, transitions, model profiles, runs, auditor recaps, mutations, and integration status.
- Deterministic single-tick runner that advances the loop and records final thoughts.
- Optional only-memories HTTP adapter.
- React + Vite studio that visualizes the current graph, state inspector, recaps, model budget, tools, skills, and integration health.
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

Frontend:

```bash
cd frontend
npm install
npm run dev
```

The studio runs at `http://localhost:5173`.

Advance the loop once:

```bash
cd backend
consciousness-tick
```

Run the durable loop:

```bash
cd backend
consciousness-loop
```

Docker:

```bash
docker compose up --build
```

Docker serves the studio at `http://localhost:5174` and the API at `http://localhost:8770`.

## Project Layout

```text
consciousness/
  backend/
    consciousness/
      api.py
      config.py
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
      sampleData.ts
      styles.css
  docs/
    architecture.md
    idea-review.md
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
curl http://localhost:8770/procedure
curl -X POST http://localhost:8770/tick
```

## Open Source Success Criteria

Consciousness becomes useful when a new operator can install it locally, see the current loop, understand why each agent ran, inspect every procedure mutation, plug in open or closed models, enforce budgets, and connect it to only-memories without reading the source code first.

The project should make the invisible parts of agent orchestration visible: what state ran, what model was chosen, what context it saw, what it changed, how the auditor judged it, and why the next state follows.
