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
- `tools`: capabilities available to this state.
- `skills`: reusable behavior packs available to this state.
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
- next transition recommendation.

## Final Thoughts

Final thoughts are not hidden chain of thought. They are concise operational reflections intended for later agents and the smart auditor:

- what was accomplished,
- what changed,
- what remains uncertain,
- what the next agent should inspect.
