# State Contract

A state is an agent domain.

Each state must define:

- `id`: stable machine id.
- `name`: operator label.
- `kind`: gather, curate, synthesize, validate, publish, or audit.
- `domain`: what the state owns.
- `goal_template`: what the state must achieve.
- `prompt_contract`: how the agent should behave.
- `output_contract`: what must be visible after completion.
- `access_preset_id`: optional reusable permission/tool/skill envelope.
- `access_overrides`: explicit additions, removals, or policy overrides applied to the preset.
- `tools`: legacy/custom capabilities when no preset is selected.
- `skills`: legacy/custom behavior packs when no preset is selected.
- `context_minimum`: minimum model context window.
- `model_policy`: model selection policy.

## Run Output

Every run should emit:

- status,
- model id,
- context window,
- context used,
- final thoughts,
- changed resources,
- confidence,
- unresolved risks,
- source links,
- artifact pointers,
- next transition recommendation.

The shared envelope carries one discriminated payload: `ContextBundle`, `MemoryChangeProposal`, `SynthesisArtifact`, `ValidationReport`, `PublishReceipt`, or `AuditDecision`. Every run is also pinned to a procedure version and resolved `agent_access` snapshot and records provider, attempt, context manifest, token usage, cost, heartbeat, errors, and append-only lifecycle events.

## Final Thoughts

Final thoughts are not hidden chain of thought. They are concise operational reflections intended for later agents and the smart auditor:

- what was accomplished,
- what changed,
- what remains uncertain,
- what the next agent should inspect.
