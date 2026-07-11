# Agent Guide

This repo is the orchestration layer for a durable, always-running agent loop.

Use `docs/implementation-plan.md` as the authoritative implementation ledger. Update task status and completion evidence there whenever a milestone changes.

## Local Rules

- Keep `only-memories` as an optional sibling integration. Do not edit `../only-memories` from this repo unless the user explicitly asks.
- Store procedural state in SQLite first. Restarts must continue from the last committed state.
- Every state must have a goal, prompt contract, tools, skills, context budget, model policy, and output contract.
- Every agent run must record the model id, context window, context used, final thoughts, changed resources, and next-state decision.
- Procedure mutations must be auditable. Add a recap and mutation record whenever a smart auditor changes the graph, model table, prompts, tools, or skills.
- Treat guardrails as code and data. Capability boundaries, loop-control policy, structured run output, and artifact/source links must stay in sync with docs and UI.
- Prefer simpler models when they satisfy the state constraints. Escalate only when failure evidence or context requirements justify it.
- Keep the frontend as an operator console, not a marketing page. Dense, readable, inspectable UI beats decorative presentation.

## Integration Contracts

- only-memories API default: `http://localhost:8765`.
- Consciousness API default: `http://localhost:8770`.
- Studio dev server default: `http://localhost:5173`.
- Docker studio default: `http://localhost:5174`.

## Verification

- Backend: `cd backend && pytest`.
- Frontend: `cd frontend && npm run build`.
- Full local run: start `consciousness-api`, start the studio, then call `consciousness-tick` and confirm the current graph marker advances.
