# Consciousness Production-Ready Local v1

This is the durable implementation ledger for Consciousness. It is both a technical specification and the handoff surface for future agents. Update a task only when its acceptance checks pass and add an entry to the changelog whenever a decision or milestone status changes.

## Status protocol

- `[ ]` not started
- `[-]` in progress
- `[x]` complete and verified
- `[!]` blocked; include the blocking evidence directly beneath the task
- Dependencies name stable task IDs. Do not start a task until its dependencies are complete unless the deviation is recorded.
- Completion evidence must include the verification commands, tests, or operator flow that proved the task done.

## Locked v1 decisions

- Local, single-operator deployment with FastAPI, SQLite, React/Vite, and Docker Compose.
- Memory stewardship through the optional `only-memories` HTTP contract is the first complete workflow.
- One worker owns execution at a time through a durable renewable SQLite lease.
- OpenAI Responses and local Ollama chat are the initial provider surfaces.
- Validated additive memories and artifact-root writes may execute automatically. Forgetting, superseding, writes outside the artifact root, and procedure mutations require approval.
- The Studio is an operator console with runtime controls, evidence inspection, approvals, and visual procedure drafting/versioning.
- Hosted multi-user operation, Postgres, distributed queues, RBAC, parallel state execution, and providers beyond OpenAI/Ollama are post-v1.

## Runtime and data contracts

SQLite is authoritative. Connections enable foreign keys, WAL, and a busy timeout. Ordered migrations are recorded in `schema_migrations`; immutable procedure definitions are stored in `procedure_versions`, while the singleton `procedure_runtime` row owns the active version, current state, loop status, worker lease, heartbeat, backoff, and failure count. Draft activation is a compare-and-swap against its parent version, so stale drafts require an explicit rebase.

Every run is pinned to a procedure version and records its resolved agent access preset (permissions, tools, skills, and policy), model, provider request metadata, context manifest, input/output/cached tokens, cost, final operational thoughts, structured output, error category, timestamps, and next-state decision. Append-only events, tool calls, artifacts, approvals, mutations, recaps, usage, and integration checks preserve evidence across restarts. Rollback activates a prior immutable version and never deletes history.

Run output uses a common envelope containing summary, confidence, risks, source links, changed resources, artifacts, and next-transition recommendation. The state-specific payload is one of `ContextBundle`, `MemoryChangeProposal`, `SynthesisArtifact`, `ValidationReport`, `PublishReceipt`, or `AuditDecision`.

The API issues durable `step`, `run`, `pause`, `resume`, and `stop` commands; the worker alone executes them. Pending/claimed Step commands deduplicate, and the Studio tracks the accepted command through its terminal state. It checkpoints before and after provider calls, tools, approvals, validation, and transitions. Mutating tool calls have durable idempotency keys propagated to remote services. Ambiguous failures remain `uncertain` until explicit reconciliation; expired worker leases and unfinished runs never replay uncertain writes.

Transitions use a declarative predicate language over validated output fields. Activated procedures must have one current state, valid references, at least one active outgoing edge per state, full reachability, and strong connectivity.

## Providers, context, and tools

Providers implement a shared health, capability, execution, usage, error, and cancellation contract.

- OpenAI uses the Responses API, `store: false`, function tools, and Pydantic-derived structured outputs. Model IDs and prices remain operator-editable.
- Ollama uses `/api/chat`, JSON Schema through `format`, and a locally controlled tool loop. Installed models may be discovered, but their context and capability settings require operator confirmation.
- The runner stores concise operational final thoughts, never hidden chain of thought or provider reasoning traces.
- Selection filters by context, structured-output support, required tools, privacy, availability, and budget before scoring cost, recent success, latency, and quality.
- Context assembly reserves output space, ranks sources deterministically, stores a manifest of source IDs/hashes/token estimates, and records provider-reported usage. Failed only-memories searches mark the integration degraded and persist an unresolved missing-context risk.

The explicit tool registry initially covers only-memories health/search/navigation/version/read-write operations, artifact-root reads/writes, procedure reads/mutation proposals, and optional read-only web search. Capability policy is enforced both when exposing and when executing tools.

## Versioned API and Studio

The public API lives under `/api/v1` and includes health/readiness, runtime/control, procedure draft/version/import/export/diff/rollback, runs/events/tools/artifacts, approvals, mutations, models, integrations, guardrails, and an SSE event stream. Draft writes use revision/ETag checks.

The Studio follows the existing dense operator-console concept. It provides a live graph, state/run evidence, durable runtime controls, approval and mutation review, model/budget/integration status, and a visual draft editor. Desktop receives complete authoring; mobile provides monitoring, controls, and approvals. Production never silently falls back to sample data.

The API binds to loopback by default, restricts CORS to configured Studio origins, redacts secrets, and requires an explicit token before accepting a non-loopback bind. Production Studio requests, SSE, and exports use a same-origin server proxy that injects the token upstream without compiling it into browser assets.

## Milestone ledger

### M0 - Specification

- [x] **PLAN-001** — Create this living specification, status protocol, dependency rules, locked decisions, acceptance gates, and changelog. Dependencies: none. Evidence: this file.
- [x] **PLAN-002** — Link the ledger from `README.md` and `AGENTS.md`; keep architecture, state, guardrail, model, and integration docs aligned. Dependencies: PLAN-001. Evidence: repository documentation links and updated contracts.

### M1 - Durable core

- [x] **CORE-001** — Ordered migrations, SQLite safety settings, normalized runtime/evidence schema, and migration from the scaffold. Dependencies: PLAN-002. Acceptance: fresh and existing scaffold databases reach the same schema; migration failure is atomic. Evidence: `test_migrations_are_recorded_and_reopen_cleanly` and the Compose persistent-volume rehearsal.
- [x] **CORE-002** — Immutable procedure versions, graph validation, drafts, activation, diff, import/export, and rollback. Dependencies: CORE-001. Acceptance: invalid graphs are rejected; active runs remain pinned; rollback preserves history. Evidence: durable-core and API tests.
- [x] **CORE-003** — Durable commands, worker lease/heartbeat, checkpoints, stale-run recovery, idempotency, budgets, and backoff. Dependencies: CORE-001. Acceptance: two workers cannot run concurrently; restart does not duplicate a write. Evidence: lease fencing, stale recovery, uncertain-write, retry-idempotency, usage, budget, and backoff tests.
- [x] **CORE-004** — Typed outputs, events, artifacts, approvals, mutations, usage, backups, and diagnostics. Dependencies: CORE-001. Acceptance: all required evidence survives restart and can be queried. Evidence: typed payload tests, artifact test, API queries, backup/diagnostics CLIs.

### M2 - Real execution

- [x] **EXEC-001** — Provider interface, deterministic fake provider, prompt/context assembler, model selection, and escalation policy. Dependencies: CORE-003, CORE-004. Evidence: provider-neutral runner and selector/context tests.
- [-] **EXEC-002** — OpenAI Responses adapter with structured output, tool calls, retries, and usage. Dependencies: EXEC-001.
- [x] **EXEC-003** — Ollama discovery/chat adapter with structured output and local tool loop. Dependencies: EXEC-001. Evidence: installed `qwen3.5:9b` discovery, schema-envelope repair, three durable tool calls, six successful live runs, and recorded usage on 2026-07-11.
- [x] **EXEC-004** — Capability-aware tool registry and complete only-memories HTTP adapter. Dependencies: CORE-004. Evidence: direct HTTP request-shape tests, registry authorization/idempotency tests, approval-gated supersede/forget, and a live tagged lifecycle acceptance run.
- [x] **ACCESS-001** — Versioned agent-access preset abstraction, bundled coding/review/research/browser/data/memory/auditor profiles, runtime resolution and run pinning, catalog API, Studio selection/inspection, and synchronized docs. Dependencies: CORE-002, EXEC-004, API-001. Evidence: 73 backend tests including concurrent first-start migration, Ruff, frontend production build, operator regression, and in-app desktop plus 390×844 catalog/editor verification.

### M3 - Memory workflow

- [x] **FLOW-001** — Real Gather, Curate, Synthesize, Validate, Publish, and Audit payload contracts. Dependencies: EXEC-001, EXEC-004. Evidence: a six-run preview cycle persisted all six discriminated payloads.
- [x] **FLOW-002** — Conditional routing, bounded publishing, approval execution, and mutation proposals. Dependencies: FLOW-001. Evidence: predicate routing, tool approval, and versioned mutation implementation/tests.
- [x] **FLOW-003** — Verify a complete Gather-to-Audit cycle against a running only-memories service. Dependencies: EXEC-002 or EXEC-003, FLOW-002. Evidence: `qwen3.5:9b` completed a deterministic six-state cycle on the normal persistent Compose volume on 2026-07-11, returning from Validate to Validate with six successful runs, three only-memories tool calls, two artifacts, zero pending approvals, and database readiness `ok`; a separate tagged lifecycle run verified create, approval-gated supersede, reinforce, approval-gated forget, restore, search, navigation, and version history.

### M4 - API and Studio

- [x] **API-001** — Versioned REST/SSE API, conflict protection, pagination, stable errors, and OpenAPI contract. Dependencies: CORE-002 through CORE-004. Evidence: opaque cursor continuity, normalized error envelopes, ETags, bounded event reads, and SSE reconnect/resume tests.
- [x] **UI-001** — Runtime dashboard, run timeline, model/budget/integration, artifacts, and sources. Dependencies: API-001. Evidence: production desktop/mobile browser render and interactive Step flow.
- [x] **UI-002** — Visual procedure editor, validation, diff, activation, import/export, and rollback. Dependencies: CORE-002, API-001. Evidence: browser-created draft, saved revision, validation, activation, and production build.
- [x] **UI-003** — Approval/mutation review, responsive monitoring, accessibility, and disconnected/degraded states. Dependencies: API-001. Evidence: isolated browser flow verifies exactly-once approval/rollback decisions, focus, landmarks/control names, mobile navigation, degradation, and disconnection; desktop/mobile browser render has no console errors.

### M5 - Hardening and release

- [x] **OPS-001** — API/worker/Studio Compose topology, health checks, persistent artifacts, safe shutdown, backup/restore, and upgrade docs. Dependencies: CORE-003, API-001. Evidence: three healthy services, persistent seven-run volume, worker restart with no state/run change, and one successful post-restart step.
- [x] **OPS-002** — Structured logs, diagnostics, metrics, retention/VACUUM, and secret redaction. Dependencies: CORE-004. Evidence: JSON-log/redaction tests, metrics scrape, and populated-database diagnostics/backup/VACUUM rehearsal.
- [x] **EXP-001** — Restartable eight-hour memory-stewardship soak harness with timed fixtures, durable phase cursor, fixed retrieval probes, single-resident-model enforcement, runtime cadence control, paired database backups, ranking/run snapshots, health status, and assessment guide. Dependencies: FLOW-003, OPS-001, OPS-002. Evidence: experiment `memory-stewardship-20260712` launched live on 2026-07-12 with six agent states and only installed/configured/resident `qwen3.5:9b`; core phase injected five memories; four paired backups passed SHA-256 and SQLite integrity verification; three snapshots, healthy supervisor heartbeat, five successful live agent runs, and rendered Studio/only-memories retrieval QA recorded. The soak remains intentionally in progress for eight hours and will pause itself at completion.
- [-] **QA-001** — CI lint/type/test/build coverage plus migration, contract, recovery, API, and browser tests. Dependencies: all implementation milestones. Evidence: 77 backend tests, Ruff, frontend build, authenticated Compose contract/rebuild, normal-volume live Ollama cycle, live lifecycle acceptance, operator regression, and in-app desktop/mobile QA pass; configured OpenAI smoke remains.
- [-] **REL-001** — Clean install and upgrade rehearsal, release checklist, limitations, and operator runbook. Dependencies: FLOW-003, QA-001, OPS-001, OPS-002. Evidence: fresh live SQLite install/cycle, populated Compose backup upgrade integrity rehearsal, rebuilt healthy services, and `docs/operator-runbook.md`; v1 tag remains gated on QA-001/OpenAI disposition.

## Detailed task contracts

The milestone checkbox is authoritative. Every row below is part of the corresponding task entry and must be updated with concrete evidence before that checkbox changes to `[x]`.

| ID | Subsystem and dependencies | Required deliverables | Acceptance tests and verification | Completion evidence |
| --- | --- | --- | --- | --- |
| PLAN-001 | Planning; none | Living ledger, status/dependency protocol, decisions, release gate, changelog | Review this file against the approved v1 scope; `rg 'PLAN-001\|REL-001' docs/implementation-plan.md` | This document, created 2026-07-10 |
| PLAN-002 | Documentation; PLAN-001 | Links plus synchronized architecture, contracts, guardrails, providers, integrations, security | `rg 'implementation-plan' README.md AGENTS.md`; inspect linked docs | README, AGENTS, and seven contract documents updated |
| CORE-001 | SQLite/migrations; PLAN-002 | Transactional ordered migrations, safety PRAGMAs, normalized v1 tables, scaffold upgrade | `cd backend && pytest -q tests/test_durable_core.py`; reopen migrated DB and run integrity check | Migration tests and persistent Compose DB pass |
| CORE-002 | Procedures/repositories; CORE-001 | Immutable versions, optimistic drafts, graph validator, semantic diff, activation/import/export/rollback | `cd backend && pytest -q tests/test_durable_core.py tests/test_api.py` | Durable-core/API tests pass; rollback preserves versions |
| CORE-003 | Worker/runtime; CORE-001 | Durable commands, renewable singleton lease, checkpoints, recovery, idempotency, budgets, bounded backoff/sleep policy | Lease contention/recovery/idempotency tests; restart worker during a run; inspect runtime and writes | Lease contention/renewal, stale-run/command recovery, uncertain-write fencing, retry idempotency, budget fallback, bounded backoff, and restart evidence pass |
| CORE-004 | Evidence/storage; CORE-001 | Typed envelopes, append-only evidence, atomic artifacts, approvals/mutations/usage, backup/diagnostics | `cd backend && pytest -q`; run `consciousness-backup` and `consciousness-diagnostics` against a populated DB | Tests and queryable six-state evidence pass |
| EXEC-001 | Execution policy; CORE-003, CORE-004 | Provider contract/fake, deterministic context/prompt assembly, eligibility and escalation | Selector/context unit tests covering capability, privacy, budget, truncation, escalation evidence | Provider-neutral preview cycle passes |
| EXEC-002 | OpenAI; EXEC-001 | Responses adapter, `store:false`, Pydantic schema, tools, usage/errors/cancel, one repair path | Mock success/refusal/malformed/timeout/rate-limit/unavailable tests and configured live smoke | Partial: full mock matrix, bounded durable tool loop, usage, cancellation, and one repair path pass; configured live smoke remains |
| EXEC-003 | Ollama; EXEC-001 | Discovery, operator-confirmed capabilities, `/api/chat` schema output, local tool loop/errors | Mock adapter tests plus installed-model live full-cycle smoke | Complete: `qwen3.5:9b` (9.7B Q4_K_M, 262,144 context, tools/thinking) completed all six states; 12,352 input and 2,244 output tokens plus tool/artifact evidence recorded |
| EXEC-004 | Tools/integration; CORE-004 | Declarative registry, double authorization, idempotency, only-memories and artifact/procedure tools | Tool authorization/approval/rejection/idempotency tests; only-memories contract mocks | Direct adapter/registry contract tests and tagged lifecycle acceptance pass; sibling repository untouched |
| ACCESS-001 | Access presets; CORE-002, EXEC-004, API-001 | Versioned preset schema, structured permissions, built-in profiles, deterministic override resolution, pinned run snapshots, catalog/assignment API, Studio inspection/selection, docs | Preset schema/resolution/run/API tests; full backend suite and Ruff; frontend build/operator regression; desktop/mobile browser QA | Complete 2026-07-12: seven bundled profiles, migration 3 run snapshots, explicit persistent-profile upgrade, 73 backend tests including concurrent first-start migration, Ruff, production build, operator regression, and rendered 1440×900 plus 390×844 verification pass |
| FLOW-001 | State contracts; EXEC-001, EXEC-004 | Six goals/prompts/contracts and discriminated payloads | Run one complete preview cycle and assert payload kinds/evidence for every state | Gather through Audit preview records verified |
| FLOW-002 | Routing/autonomy; FLOW-001 | Predicate DSL, automatic additive writes, pending risky actions, approval execution, mutation proposals | Predicate rejection tests; safe/risky tool tests; approve/reject and mutation activation tests | Implementation and durable-core tests pass |
| FLOW-003 | Live memory vertical; EXEC-002 or EXEC-003, FLOW-002 | Real Gather-to-Audit cycle through only-memories public contract | Start only-memories and Ollama/OpenAI, execute six states, verify every durable handoff and write policy | Complete 2026-07-11: normal Compose volume completed Validate → Publish → Audit → Gather → Curate → Synthesize → Validate with six successful `qwen3.5:9b` runs, three memory tool calls, two artifacts, no pending approvals, and database readiness pass |
| API-001 | FastAPI; CORE-002–CORE-004 | `/api/v1` REST/SSE, ETags, stable errors, cursor pagination, OpenAPI | `cd backend && pytest -q tests/test_api.py`; SSE reconnect and pagination contract tests | REST/SSE/ETag/OpenAPI, opaque cursor continuity, stable errors, and `Last-Event-ID` resume pass |
| UI-001 | Studio monitoring; API-001 | Runtime graph/dashboard, timeline, usage/models/integrations/artifacts/sources | `cd frontend && npm run build`; desktop/mobile production browser check; console-error check | Production render and Step interaction pass |
| UI-002 | Studio authoring; CORE-002, API-001 | Full graph/state/edge editor, inline validation, diff, activate, import/export, rollback | Browser flow creates/saves/validates/activates a draft; build/typecheck | Browser flow and build pass |
| UI-003 | Studio safety/UX; API-001 | Approval/mutation review, responsive monitoring, accessibility, stale/disconnected/degraded states | Browser E2E for decisions and failure states; keyboard/a11y scan; desktop/mobile visual baselines | Operator browser flow verifies exactly-once approval, Step submission, and rollback; command failures are visible; all mobile navigation bounding boxes fit the initial 390px viewport; in-app desktop/mobile render and console checks pass |
| OPS-001 | Deployment; CORE-003, API-001 | API/worker/Studio Compose, health, volumes, shutdown, backup/restore/upgrade docs | `docker compose config`; fresh build/up; health checks; worker restart and restore rehearsal | Healthy Compose and restart evidence recorded 2026-07-10; rebuilt API/worker/Studio all healthy again on 2026-07-11 |
| OPS-002 | Operations; CORE-004 | Structured logs, metrics, diagnostics, retention/VACUUM, secret redaction | Log/redaction tests; metrics scrape; diagnostics/backup/VACUUM retention rehearsal | JSON logging and recursive credential redaction tests pass; metrics scrape and populated-db backup/diagnostics/VACUUM rehearsal pass |
| EXP-001 | Memory soak experiment; FLOW-003, OPS-001, OPS-002 | Restartable supervisor, phased corpus, one-model guard, durable 300-second cadence, snapshots/status/timeline, paired integrity-checked backups, monitoring and assessment docs | Unit tests; fixture reference validation; live Compose launch; verify interval/model/state guards, UI retrieval, supervisor health, backup hashes/integrity, and successful agent evidence | Complete setup 2026-07-12: live eight-hour run active under `memory-stewardship-20260712`; six roles, five initial memories, four paired backups, three snapshots, one resident Qwen model, healthy consoles/supervisor, and five successful Qwen agent runs across Validate, Publish, Audit, Gather, and Curate |
| QA-001 | CI/release QA; all implementation tasks | Pytest/Ruff/type/test/build/Compose CI plus migration/provider/tool/recovery/API/browser suites | `cd backend && pytest -q && ruff check consciousness tests`; `cd frontend && npm run build`; `docker compose config`; Playwright E2E | Partial: 77 backend tests, Ruff, build, authenticated Compose rebuild/health with direct API 401 plus proxied readiness/export/SSE success, operator browser regression, in-app desktop/mobile QA, normal-volume live Ollama cycle, and lifecycle acceptance pass; configured OpenAI smoke remains |
| REL-001 | Release; FLOW-003, QA-001, OPS-001, OPS-002 | Clean-install/upgrade rehearsal, checklist, limitations, operator runbook and v1 tag | Execute every release gate on a fresh volume and an upgraded scaffold backup | Partial: fresh live database and full cycle pass; populated Compose backup reopens with `integrity: ok`; runbook/checklist/limitations added; v1 tag waits on QA-001/OpenAI disposition |

## Verification and release gates

Automated coverage must exercise migrations, graph invariants, procedure activation/rollback, optimistic conflicts, provider failures/refusals/schema repair, model eligibility and budgets, tool authorization/approval/idempotency, worker contention/recovery, only-memories contracts, API/SSE behavior, and Studio editing/controls/approvals/responsiveness.

The v1 gate requires healthy API/worker/Studio services from a fresh Compose startup; a complete loop through Ollama and separately through OpenAI when configured; complete durable evidence for every state; bounded automatic writes with risky actions pending approval; restart without state loss or duplicate writes; cloud-budget fallback to local/degraded operation; and a Studio that can author, validate, diff, activate, export, and roll back a strongly connected procedure.

Standard verification:

```bash
cd backend && pytest
cd frontend && npm run build
docker compose config
```

Full local acceptance additionally starts only-memories, `consciousness-api`, `consciousness-worker`, and the Studio, completes one six-state cycle, interrupts the worker once, and verifies the graph marker, event log, artifacts, approvals, and usage ledger.

## Changelog

- 2026-07-12 — Launched the durable `memory-stewardship-20260712` experiment: six sequential agent roles use only installed `qwen3.5:9b` on a 300-second cadence while nine timed fixture phases test importance, reminders, repetition, correction, contradiction, expiry, and graph navigation. Added restart-safe supervision, single-resident-model checks, fixed retrieval probes, paired online SQLite backups with hashes, full snapshots/status/timeline, and a two-day assessment guide. Verified 77 backend tests, Ruff, Compose, isolated operator regression at `5174`, both rendered monitoring consoles, five successful live Qwen runs, four paired backup sets, and three snapshots.

- 2026-07-12 — Added versioned agent access presets with structured permissions, deterministic state overrides, runtime tool/skill resolution, pinned run evidence, explicit persistent-profile upgrade, catalog/assignment APIs, and Studio catalog/editor support. Bundled seven evidence-backed profiles, including a portable workspace-scoped coding agent informed by current Codex, Claude Code, and Pi behavior. Serialized concurrent first-start migration/setup after live QA exposed the race. Verified 73 backend tests, Ruff, production build, operator regression, and in-app 1440×900 plus 390×844 rendered QA.

- 2026-07-10 — Created the local-v1 specification and task ledger; locked OpenAI plus Ollama, bounded autonomy, a single SQLite worker, and full visual procedure authoring.
- 2026-07-10 — Implemented the durable SQLite core, provider/tool contracts, six-state preview workflow, versioned API, visual Studio, maintenance commands, and three-service Compose baseline. Verified worker restart recovery and production desktop/mobile rendering; left live provider/only-memories, failure-matrix, accessibility, structured-log, and release gates explicitly open.
- 2026-07-10 — Closed durable worker recovery, API pagination/error/SSE contracts, Studio approval/mutation safety and accessibility checks, and structured operations. Expanded the backend suite to 48 tests and CI with an isolated operator browser flow. Verified a six-state preview cycle against the live only-memories API; live provider execution remains the release blocker.
- 2026-07-11 — Installed and registered `qwen3.5:9b`, tightened Ollama runtime context and structured envelope repair, and closed EXEC-003/FLOW-003 with a six-state live only-memories cycle. Expanded the suite to 50 tests, rebuilt three healthy Compose services, repeated desktop/mobile operator QA, rehearsed a populated-backup upgrade, and added the operator/release runbook. OpenAI live smoke and the v1 tag remain open.
- 2026-07-11 — Hardened only-memories contracts and lifecycle safety: Curate is proposal-only, supersede/forget require approval, Publish consumes accepted validation findings once, direct adapter tests cover public request shapes, and persistent profile upgrades are versioned with mutation/recap evidence. Verified 62 backend tests, Ruff, frontend build, a tagged create/supersede/reinforce/forget/restore run, and a six-state `qwen3.5:9b` cycle on the normal live Compose volume.
- 2026-07-12 — Closed six review findings: ambiguous remote writes now propagate idempotency keys and require reconciliation, stale drafts are compare-and-swap fenced, authenticated Studio SSE/export use a same-origin proxy, Step commands deduplicate and expose terminal errors, Gather search failures persist degraded risk evidence, and 390px navigation is fully visible. Verified 67 backend tests, Ruff, frontend build/operator regression, authenticated three-service Compose health plus proxy readiness/export/SSE, and in-app desktop/mobile rendering with no console warnings. OpenAI live smoke remains the release gate.
