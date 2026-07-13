# Model Registry

The model registry is a policy table. It is not a claim that bundled example prices are current.

Each row should define:

- provider,
- model id,
- protocol and base URL,
- API-key environment name or write-only credential reference,
- billing mode (`local`, `metered`, or `subscription`),
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
| `local/ornith-1.0-9b-q4` | Ollama | 32,768 operating limit | 0.0x | 3 | routine Gather through Publish work |
| `minimax/MiniMax-M3` | MiniMax Chat Completions | 200,000 configured minimum | subscription | 5 | Audit, graph supervision, and procedure governance |
| `openai/gpt-5.6-luna` | OpenAI | operator-confirmed | configured | 3 | efficient structured work |
| `openai/gpt-5.6-sol` | OpenAI | operator-confirmed | configured | 5 | procedure design and audits |

Operators must confirm model availability, context limits, capabilities, and pricing in their registry. Pricing and model IDs are data rather than selection code so examples cannot silently become current facts.

Install the official GGUF with
`ollama pull hf.co/deepreinforce-ai/Ornith-1.0-9B-GGUF:Q4_K_M`. MiniMax uses the
OpenAI-compatible Chat Completions protocol at `https://api.minimax.io/v1` and resolves
`MINIMAX_API_KEY`; its profile enables `reasoning_split` so reasoning remains separate from the
structured result, while the adapter also tolerates the provider's native `<think>` prefix. It does
not reuse the OpenAI Responses adapter. UI-entered secrets are never
returned by the API and require the encrypted vault master key. Hermes OAuth is not imported or
silently reused; provider subscription OAuth remains an explicit future auth strategy.
