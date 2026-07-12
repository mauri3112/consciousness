# Model Registry

The model registry is a policy table. It is not a claim that bundled example prices are current.

Each row should define:

- provider,
- model id,
- context window,
- relative cost,
- max run budget,
- quality tier,
- strengths,
- whether the weights are open,
- whether the model is enabled.

The runner uses this table to choose the simplest model that can succeed. Context, required capabilities, privacy mode, provider availability, and budget are hard filters; cost and tier are ranking inputs.

## Selection Policy

Default preference order:

1. Satisfy the state's minimum context window.
2. Match the state's model policy and required strengths.
3. Prefer lower relative cost.
4. Prefer lower quality tier when recent success is high.
5. Escalate only when failures, procedure mutation, contradiction checks, or context requirements justify it.

## Example Rows

| id | provider | context | cost | tier | best for |
| --- | --- | ---: | ---: | ---: | --- |
| `local/qwen3.5-9b` | Ollama | 262,144 | 0.0x | 3 | full offline loop, structured output, and tool calling |
| `openai/gpt-5.6-luna` | OpenAI | operator-confirmed | configured | 3 | efficient structured work |
| `openai/gpt-5.6-sol` | OpenAI | operator-confirmed | configured | 5 | procedure design and audits |

Operators must confirm model availability, context limits, capabilities, and pricing in their registry. Pricing and model IDs are data rather than selection code so examples cannot silently become current facts.

The bundled local row is verified against Ollama's published `qwen3.5:9b` metadata. Install it with `ollama pull qwen3.5:9b` before setting `CONSCIOUSNESS_EXECUTION_MODE=live`.
