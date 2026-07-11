import { z } from "zod";

const stateSchema = z.object({
  id: z.string(), name: z.string(), kind: z.string(), domain: z.string(),
  goal_template: z.string(), prompt_contract: z.string(), output_contract: z.string(),
  tools: z.array(z.string()), skills: z.array(z.string()), context_minimum: z.number(),
  output_reserve: z.number().default(4096), model_policy: z.string(), max_attempts: z.number().default(2),
  max_run_budget: z.number().nullable().default(null), x: z.number(), y: z.number(), is_current: z.boolean()
});

const transitionSchema = z.object({
  id: z.string(), source_id: z.string(), target_id: z.string(), weight: z.number(),
  guard: z.string(), rationale: z.string(), active: z.boolean()
});

const modelSchema = z.object({
  id: z.string(), provider: z.string(), model: z.string(), context_window: z.number(),
  relative_cost: z.number(), max_run_budget: z.number(), quality_tier: z.number(),
  strengths: z.array(z.string()), capabilities: z.array(z.string()).default(["structured-output"]),
  input_cost_per_million: z.number().default(0), output_cost_per_million: z.number().default(0),
  open_weights: z.boolean(), enabled: z.boolean()
});

const sourceSchema = z.object({ label: z.string(), kind: z.string(), uri: z.string() });
const artifactPointerSchema = z.object({ label: z.string(), kind: z.string(), uri: z.string(), content_hash: z.string().nullable().optional() });
const outputSchema = z.object({
  summary: z.string(), confidence: z.number(), changed_resources: z.array(artifactPointerSchema),
  source_links: z.array(sourceSchema), unresolved_risks: z.array(z.string()),
  next_transition_recommendation: z.string(), payload: z.record(z.string(), z.unknown()).nullable().optional()
});
const runSchema = z.object({
  id: z.string(), state_id: z.string(), procedure_version_id: z.string(), goal: z.string(), status: z.string(),
  attempt: z.number(), model_id: z.string(), provider: z.string(), provider_request_id: z.string().nullable(),
  context_window: z.number(), context_used: z.number(), input_tokens: z.number(), output_tokens: z.number(),
  cached_tokens: z.number(), cost: z.number(), context_manifest: z.record(z.string(), z.unknown()),
  started_at: z.string(), heartbeat_at: z.string().nullable(), finished_at: z.string().nullable(),
  final_thoughts: z.string().nullable(), changes: z.array(z.record(z.string(), z.unknown())),
  output: outputSchema.nullable(), error_category: z.string().nullable(), error_message: z.string().nullable()
});

const policySchema = z.object({
  state_id: z.string(), allowed_tool_patterns: z.array(z.string()), mutation_level: z.string(),
  requires_approval: z.boolean(), rationale: z.string()
});
const guardrailsSchema = z.object({
  capability_policies: z.array(policySchema),
  loop_control: z.object({
    manual_pause_enabled: z.boolean(), sleep_window: z.string(), base_backoff_seconds: z.number(),
    max_backoff_seconds: z.number(), max_consecutive_failures: z.number(), daily_budget_cap: z.number(), degraded_mode: z.string()
  }),
  evidence_policy: z.record(z.string(), z.boolean())
});
const definitionSchema = z.object({
  name: z.string(), states: z.array(stateSchema), transitions: z.array(transitionSchema), models: z.array(modelSchema), guardrails: guardrailsSchema
});
const versionSchema = z.object({
  id: z.string(), version: z.number(), status: z.string(), digest: z.string(), parent_id: z.string().nullable(),
  revision: z.number(), definition: definitionSchema, created_by_run_id: z.string().nullable(),
  created_at: z.string(), activated_at: z.string().nullable()
});
const runtimeSchema = z.object({
  active_version_id: z.string(), current_state_id: z.string(), status: z.string(), interval_seconds: z.number(),
  worker_id: z.string().nullable(), lease_expires_at: z.string().nullable(), heartbeat_at: z.string().nullable(),
  failure_count: z.number(), backoff_until: z.string().nullable(), daily_budget_cap: z.number(),
  execution_mode: z.string(), updated_at: z.string()
});
const recapSchema = z.object({
  id: z.string(), run_id: z.string().nullable(), auditor_model_id: z.string(), summary: z.string(),
  decision: z.string(), procedure_changes: z.array(z.record(z.string(), z.unknown())), created_at: z.string()
});
const integrationSchema = z.object({
  name: z.string(), status: z.string(), endpoint: z.string().nullable(), last_checked_at: z.string().nullable(),
  details: z.record(z.string(), z.unknown())
});
const approvalSchema = z.object({
  id: z.string(), run_id: z.string().nullable(), kind: z.string(), status: z.string(), risk: z.string(),
  proposed_action: z.record(z.string(), z.unknown()), evidence: z.array(sourceSchema), requested_at: z.string(),
  decided_at: z.string().nullable(), decision_note: z.string().nullable()
});
const mutationSchema = z.object({
  id: z.string(), base_version_id: z.string(), proposed_version_id: z.string(), proposer_run_id: z.string().nullable(),
  status: z.string(), diff: z.string(), rationale: z.string(), budget_impact: z.record(z.string(), z.unknown()),
  rollback_version_id: z.string(), created_at: z.string(), decided_at: z.string().nullable()
});

export const snapshotSchema = z.object({
  version: versionSchema, runtime: runtimeSchema, states: z.array(stateSchema), transitions: z.array(transitionSchema),
  models: z.array(modelSchema), runs: z.array(runSchema), recaps: z.array(recapSchema),
  integrations: z.array(integrationSchema), guardrails: guardrailsSchema,
  approvals: z.array(approvalSchema), mutations: z.array(mutationSchema)
});

export type ProcedureState = z.infer<typeof stateSchema>;
export type Transition = z.infer<typeof transitionSchema>;
export type ModelProfile = z.infer<typeof modelSchema>;
export type ProcedureDefinition = z.infer<typeof definitionSchema>;
export type ProcedureVersion = z.infer<typeof versionSchema>;
export type RunRecord = z.infer<typeof runSchema>;
export type ApprovalRecord = z.infer<typeof approvalSchema>;
export type ProcedureSnapshot = z.infer<typeof snapshotSchema>;
export type RunEvent = { id: number; run_id: string | null; event_type: string; payload: Record<string, unknown>; created_at: string };

const API_URL = import.meta.env.VITE_CONSCIOUSNESS_API_URL ?? "http://localhost:8770";
const API_TOKEN = import.meta.env.VITE_CONSCIOUSNESS_API_TOKEN as string | undefined;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}/api/v1${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(API_TOKEN ? { Authorization: `Bearer ${API_TOKEN}` } : {}),
      ...init?.headers
    }
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail));
  }
  return response.json() as Promise<T>;
}

export async function fetchProcedure(): Promise<ProcedureSnapshot> {
  return snapshotSchema.parse(await request<unknown>("/procedure"));
}

export function issueControl(kind: "step" | "run" | "pause" | "resume" | "stop") {
  return request<{ id: number; kind: string; status: string }>(`/control/${kind}`, { method: "POST" });
}

export function createDraft() {
  return request<ProcedureVersion>("/procedure/drafts", { method: "POST" });
}

export function saveDraft(draft: ProcedureVersion, definition: ProcedureDefinition) {
  return request<ProcedureVersion>(`/procedure/drafts/${draft.id}`, {
    method: "PUT",
    headers: { "If-Match": `"${draft.revision}"` },
    body: JSON.stringify({ revision: draft.revision, definition })
  });
}

export function validateDraft(id: string) {
  return request<{ valid: boolean; errors: string[] }>(`/procedure/drafts/${id}/validate`, { method: "POST" });
}

export function activateDraft(id: string) {
  return request<ProcedureVersion>(`/procedure/drafts/${id}/activate`, { method: "POST" });
}

export function fetchDiff(base: string, target: string) {
  return request<{ diff: string }>(`/procedure/diff?base=${encodeURIComponent(base)}&target=${encodeURIComponent(target)}`);
}

export function rollbackVersion(id: string) {
  return request<ProcedureVersion>(`/procedure/versions/${id}/rollback`, { method: "POST" });
}

export function decideApproval(id: string, approved: boolean, note?: string) {
  return request<ApprovalRecord>(`/approvals/${id}/decision`, {
    method: "POST",
    body: JSON.stringify({ approved, note })
  });
}

export function fetchRunEvents(runId: string) {
  return request<RunEvent[]>(`/runs/${runId}/events`);
}

export function eventStreamUrl(afterId = 0) {
  return `${API_URL}/api/v1/events?after_id=${afterId}`;
}

export function exportUrl() {
  return `${API_URL}/api/v1/procedure/export`;
}

export async function importProcedure(definition: ProcedureDefinition) {
  return request<ProcedureVersion>("/procedure/import", { method: "POST", body: JSON.stringify(definition) });
}
