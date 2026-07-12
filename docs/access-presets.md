# Agent Access Presets

Access presets are reusable, versioned capability envelopes for procedure states. They answer three separate questions:

1. Which permissions may the agent exercise?
2. Which tools may the provider see and invoke?
3. Which reusable skills shape the state prompt?

Presets live inside `ProcedureDefinition.access_presets`, so draft diff, activation, rollback, export, and import preserve the exact catalog used by a run. A state selects `access_preset_id` and may add or remove tools, skills, or allowed tool patterns through `access_overrides`. States without a preset retain the legacy state-local tool, skill, and capability policy contract.

The worker resolves the selected preset before context assembly. It uses the resolved tools for provider exposure, derives the execution policy from the same resolved object, includes the resolved skills in the prompt, and stores the complete resolved access snapshot on the run. This prevents a later preset edit from rewriting the authority recorded for past work.

## Permission vocabulary

Each preset has structured boundaries for:

- `filesystem`: `none`, `read_only`, `workspace_write`, or `unrestricted`;
- `shell`: `none`, `read_only`, `workspace_write`, or `unrestricted`;
- `network`: `none`, `restricted`, or `unrestricted`;
- `external_writes`: `deny`, `ask`, or `allow`;
- `secrets`: `deny`, `ask`, or `allow`.

Tool configuration and runtime availability are intentionally distinct. `GET /api/v1/access/catalog` reports the materialized presets, registered tool descriptors, configured tools without adapters, the skill catalog, and every state's server-resolved access. A configured-but-unavailable tool is never sent to a provider. The Studio marks it as unavailable instead of implying that configuration alone installed an adapter.

## Bundled presets

- `coding-agent`: the safe portable common denominator for a coding harness—workspace edits, local commands/tests, local Git inspection, and bounded technical web lookup. Publishing, deployment, credential access, and other external writes are not part of the default grant.
- `coding-reviewer`: repository reads, safe command execution, Git evidence, and review skills without workspace mutation.
- `researcher`: live source retrieval and durable report artifacts without an ambient shell.
- `browser-operator`: navigation and visual QA, with approval required for form submissions and other external effects.
- `data-analyst`: read source data, run bounded analysis, and write derived artifacts without mutating source databases.
- `memory-steward`: only-memories curation with provenance; destructive lifecycle changes remain approval-gated.
- `procedure-auditor`: read and diff the active procedure and propose mutations; activation remains versioned and operator-controlled.

There is no exact universal default shared by Codex, Claude Code, and Pi. The coding preset intentionally combines the useful common workflow with a workspace boundary. Codex supports named permission profiles and granular approvals; Claude Code separates permission modes and deny/ask/allow rules; Pi exposes a compact built-in coding tool set and recommends isolation or extension-provided gates when stronger safety is needed. See the current primary documentation: [Codex configuration reference](https://developers.openai.com/codex/config-reference), [Claude Code permissions](https://code.claude.com/docs/en/permissions), [Claude Code tools](https://code.claude.com/docs/en/tools-reference), and [Pi coding agent](https://github.com/badlogic/pi-mono/tree/main/packages/coding-agent).

## Defining a custom preset

Custom presets use the same schema and are stored in the procedure draft:

```json
{
  "id": "incident-operator",
  "name": "Incident operator",
  "description": "Inspect health and logs; ask before restarts or configuration changes.",
  "agent_type": "incident-responder",
  "permissions": { "filesystem": "read_only", "shell": "read_only", "network": "restricted", "external_writes": "ask", "secrets": "deny" },
  "tools": ["service.health", "service.logs", "service.restart"],
  "skills": ["incident-triage", "runbook-execution", "rollback-planning"],
  "allowed_tool_patterns": ["service.*"],
  "mutation_level": "external_write",
  "requires_approval": true,
  "rationale": "Inspection is automatic; service mutations require operator review.",
  "built_in": false
}
```

Create or import the preset in a draft, assign it to states, review the semantic diff, validate, and activate. Preset edits are procedure mutations and therefore inherit the normal immutable history and rollback path.
