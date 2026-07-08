# Consciousness

Consciousness is an always-running procedural loop for LLM agents.

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

## Current Scaffold

- Python FastAPI backend.
- SQLite store for procedure states, transitions, model profiles, runs, auditor recaps, mutations, and integration status.
- Deterministic single-tick runner that advances the loop and records final thoughts plus structured evidence output.
- Guardrail policies for state capabilities, loop control, and evidence requirements.
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
      sampleData.ts
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
curl http://localhost:8770/procedure
curl http://localhost:8770/guardrails
curl -X POST http://localhost:8770/tick
```

## Open Source Success Criteria

Consciousness becomes useful when a new operator can install it locally, see the current loop, understand why each agent ran, inspect every procedure mutation, plug in open or closed models, enforce budgets, and connect it to only-memories without reading the source code first.

The project should make the invisible parts of agent orchestration visible: what state ran, what model was chosen, what context it saw, what it changed, how the auditor judged it, and why the next state follows.
