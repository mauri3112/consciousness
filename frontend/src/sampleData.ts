import type { ProcedureSnapshot } from "./api";

const now = new Date().toISOString();

export const sampleSnapshot: ProcedureSnapshot = {
  states: [
    {
      id: "gather",
      name: "Gather",
      kind: "gather",
      domain: "Context intake",
      goal_template: "Retrieve high-signal memories, recent artifacts, and source links for the current memory stewardship question.",
      prompt_contract: "Prefer primary sources and summarize only what the next state needs.",
      output_contract: "A compact context bundle with source ids, confidence, unresolved gaps, and token estimate.",
      tools: ["only_memories.search", "only_memories.navigate", "filesystem.read"],
      skills: ["source_triangulation", "context_compression"],
      context_minimum: 65536,
      model_policy: "cheap-capable",
      x: 50,
      y: 40,
      is_current: true
    },
    {
      id: "curate",
      name: "Curate",
      kind: "curate",
      domain: "Memory maintenance",
      goal_template: "Merge duplicates, mark stale candidates, and propose bridge memories without deleting provenance.",
      prompt_contract: "Treat forgetting as reversible and explain every proposed lifecycle change.",
      output_contract: "A structured memory-change proposal with source ids and rollback notes.",
      tools: ["only_memories.remember", "only_memories.forget", "only_memories.reinforce_connection"],
      skills: ["deduplication", "graph_stewardship"],
      context_minimum: 65536,
      model_policy: "maintenance",
      x: 77,
      y: 52,
      is_current: false
    },
    {
      id: "synthesize",
      name: "Synthesize",
      kind: "synthesize",
      domain: "Bridge memory creation",
      goal_template: "Create concise synthesis artifacts that help future agents navigate the memory graph.",
      prompt_contract: "Prefer durable insight over broad summaries.",
      output_contract: "A synthesis artifact with dependencies, expiry, and suggested graph connections.",
      tools: ["only_memories.remember", "filesystem.write"],
      skills: ["abstraction", "artifact_design"],
      context_minimum: 128000,
      model_policy: "balanced",
      x: 75,
      y: 68,
      is_current: false
    },
    {
      id: "validate",
      name: "Validate",
      kind: "validate",
      domain: "Evidence and contradiction checks",
      goal_template: "Check whether proposed memory changes are supported, contradicted, expired, or too uncertain.",
      prompt_contract: "Flag contradictions explicitly and return to gather if evidence is insufficient.",
      output_contract: "A validation report with accepted changes, rejected changes, risks, and next transition.",
      tools: ["only_memories.search", "only_memories.versions", "web.search"],
      skills: ["source_evaluation", "contradiction_detection"],
      context_minimum: 128000,
      model_policy: "balanced",
      x: 50,
      y: 87,
      is_current: false
    },
    {
      id: "publish",
      name: "Publish",
      kind: "publish",
      domain: "Durable outputs",
      goal_template: "Commit accepted memory changes, procedure recaps, and inspectable output artifacts.",
      prompt_contract: "Make the final state visible to later agents and operators.",
      output_contract: "Committed memory writes, recap entries, and links to changed resources.",
      tools: ["only_memories.remember", "filesystem.write", "git.diff"],
      skills: ["provenance_writing", "operator_handoff"],
      context_minimum: 32768,
      model_policy: "cheap-capable",
      x: 25,
      y: 66,
      is_current: false
    },
    {
      id: "audit",
      name: "Audit",
      kind: "audit",
      domain: "Procedure governance",
      goal_template: "Evaluate run quality, model fit, budget pressure, and whether the procedure should change.",
      prompt_contract: "Use a stronger model only when the cheaper loop is failing or changing the procedure.",
      output_contract: "Auditor recap plus optional procedure mutation proposal.",
      tools: ["consciousness.procedure.read", "consciousness.procedure.mutate", "git.diff"],
      skills: ["procedure_design", "model_governance", "budget_control"],
      context_minimum: 200000,
      model_policy: "auditor",
      x: 23,
      y: 36,
      is_current: false
    }
  ],
  transitions: [
    { id: "gather_to_curate", source_id: "gather", target_id: "curate", weight: 1, guard: "always", rationale: "Context bundle is fresh enough for memory maintenance.", active: true },
    { id: "curate_to_synthesize", source_id: "curate", target_id: "synthesize", weight: 1, guard: "always", rationale: "Curated candidates need durable bridge artifacts.", active: true },
    { id: "synthesize_to_validate", source_id: "synthesize", target_id: "validate", weight: 1, guard: "always", rationale: "Synthesized artifacts require evidence checks.", active: true },
    { id: "validate_to_publish", source_id: "validate", target_id: "publish", weight: 1, guard: "always", rationale: "Accepted changes can be made visible.", active: true },
    { id: "publish_to_audit", source_id: "publish", target_id: "audit", weight: 1, guard: "always", rationale: "Every publish cycle should be evaluated.", active: true },
    { id: "audit_to_gather", source_id: "audit", target_id: "gather", weight: 1, guard: "always", rationale: "The loop returns to intake.", active: true },
    { id: "validate_to_gather", source_id: "validate", target_id: "gather", weight: 0.35, guard: "always", rationale: "Insufficient evidence reopens intake.", active: true }
  ],
  models: [
    { id: "local/llama-3.1-8b-instruct", provider: "ollama", model: "llama-3.1-8b-instruct", context_window: 32768, relative_cost: 0, max_run_budget: 0, quality_tier: 1, strengths: ["classification", "offline"], open_weights: true, enabled: true },
    { id: "local/qwen2.5-14b-instruct", provider: "ollama", model: "qwen2.5-14b-instruct", context_window: 65536, relative_cost: 0, max_run_budget: 0, quality_tier: 2, strengths: ["curation", "structured-output"], open_weights: true, enabled: true },
    { id: "openai/gpt-4.1-mini", provider: "openai", model: "gpt-4.1-mini", context_window: 128000, relative_cost: 1, max_run_budget: 0.15, quality_tier: 3, strengths: ["balanced", "tool-use", "synthesis"], open_weights: false, enabled: true },
    { id: "frontier/auditor-large", provider: "configurable", model: "auditor-large", context_window: 200000, relative_cost: 4, max_run_budget: 1, quality_tier: 5, strengths: ["procedure-design", "evaluation"], open_weights: false, enabled: true }
  ],
  runs: [
    {
      id: "run_preview_01",
      state_id: "publish",
      goal: "Commit accepted memory changes, procedure recaps, and inspectable output artifacts.",
      status: "succeeded",
      model_id: "local/qwen2.5-14b-instruct",
      context_window: 65536,
      context_used: 42110,
      started_at: now,
      finished_at: now,
      final_thoughts: "Published recap artifacts and left validation risks visible to the auditor.",
      changes: [{ kind: "memory-recap", visible_to_next_agent: true }],
      output: {
        summary: "Committed memory writes, recap entries, and links to changed resources.",
        confidence: 0.78,
        changed_resources: [{ label: "publish run record", kind: "sqlite-row", uri: "sqlite://runs/run_preview_01", content_hash: null }],
        source_links: [{ label: "Publish state contract", kind: "procedure-state", uri: "consciousness://states/publish" }],
        unresolved_risks: ["Provider adapters are not connected in preview mode."],
        next_transition_recommendation: "audit"
      }
    }
  ],
  recaps: [
    {
      id: "recap_preview_01",
      run_id: "run_preview_01",
      auditor_model_id: "frontier/auditor-large",
      summary: "Starter ceremony is coherent. The next improvement is gating procedure mutations behind versioned diffs.",
      decision: "continue",
      procedure_changes: [],
      created_at: now
    }
  ],
  integrations: [
    {
      name: "only-memories",
      status: "optional",
      endpoint: "http://localhost:8765",
      last_checked_at: now,
      details: { mode: "read/write recaps when enabled" }
    }
  ],
  guardrails: {
    capability_policies: [
      {
        state_id: "gather",
        allowed_tool_patterns: ["only_memories.search", "only_memories.navigate", "filesystem.read"],
        mutation_level: "read_only",
        requires_approval: false,
        rationale: "Gather can inspect context but cannot mutate memory or procedure state."
      },
      {
        state_id: "publish",
        allowed_tool_patterns: ["only_memories.remember", "filesystem.write", "git.diff"],
        mutation_level: "accepted_write",
        requires_approval: true,
        rationale: "Publish makes accepted changes visible and should record rollback links."
      },
      {
        state_id: "audit",
        allowed_tool_patterns: ["consciousness.procedure.read", "consciousness.procedure.mutate", "git.diff"],
        mutation_level: "procedure_proposal",
        requires_approval: true,
        rationale: "Audit can propose procedure mutations, but applied changes must be diffed and versioned."
      }
    ],
    loop_control: {
      manual_pause_enabled: true,
      sleep_window: "operator-defined",
      base_backoff_seconds: 60,
      max_backoff_seconds: 3600,
      max_consecutive_failures: 3,
      daily_budget_cap: 5,
      degraded_mode: "local_only"
    },
    evidence_policy: {
      structured_output_required: true,
      changed_resources_required: true,
      confidence_required: true,
      unresolved_risks_required: true,
      source_links_required: true,
      artifact_pointer_required: true
    }
  }
};
