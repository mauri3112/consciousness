# Guardrails

Consciousness treats guardrails as part of the procedure, not as external policy prose.

## Capability Boundaries

Every state has a capability policy:

- allowed tool patterns,
- mutation level,
- approval requirement,
- rationale.

The important distinction is between proposing a change and applying a change. A smart auditor may propose procedure mutation, but applied mutations should be versioned, diffed, budget-limited, and reversible.

## Resilient Closed Loop

"Always running" means restartable and self-throttling:

- manual pause,
- sleep windows,
- base and max backoff,
- max consecutive failures,
- daily budget cap,
- degraded local-only mode.

The loop should continue from durable state after restarts, but it should not spend or write endlessly when evidence says it is failing.

## Structured Evidence

Final thoughts are a human-friendly recap, not the evidence contract. Every completed run should also emit:

- structured summary,
- changed resources,
- confidence,
- unresolved risks,
- source links,
- artifact pointers,
- next-transition recommendation.

This makes auditor evaluation possible even when the original agent context is gone.

## Artifact And Source Links

SQLite stores the state of the loop. It should not be the only place large artifacts live. Generated files, code diffs, long recaps, screenshots, and memory writes need stable pointers:

- `sqlite://runs/<id>` for run rows,
- `consciousness://states/<id>` for procedure state contracts,
- file paths or content-addressed URIs for generated artifacts,
- only-memories ids for memory writes.

This is how the next agent can inherit useful state without pretending it still has the previous context window.
