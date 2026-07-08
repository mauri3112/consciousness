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

The smart auditor uses this table to choose the simplest model that can succeed.

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
| `local/llama-3.1-8b-instruct` | Ollama | 32,768 | 0.0x | 1 | offline classification |
| `local/qwen2.5-14b-instruct` | Ollama | 65,536 | 0.0x | 2 | curation and structured output |
| `openai/gpt-4.1-mini` | OpenAI | 128,000 | 1.0x | 3 | balanced synthesis and tool use |
| `frontier/auditor-large` | configurable | 200,000 | 4.0x | 5 | procedure design and audits |

Operators should update actual provider pricing in their own registry. The scaffold uses relative costs to avoid baking stale pricing into the project.
