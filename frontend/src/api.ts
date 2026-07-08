import { sampleSnapshot } from "./sampleData";

export type ProcedureState = {
  id: string;
  name: string;
  kind: string;
  domain: string;
  goal_template: string;
  prompt_contract: string;
  output_contract: string;
  tools: string[];
  skills: string[];
  context_minimum: number;
  model_policy: string;
  x: number;
  y: number;
  is_current: boolean;
};

export type Transition = {
  id: string;
  source_id: string;
  target_id: string;
  weight: number;
  guard: string;
  rationale: string;
  active: boolean;
};

export type ModelProfile = {
  id: string;
  provider: string;
  model: string;
  context_window: number;
  relative_cost: number;
  max_run_budget: number;
  quality_tier: number;
  strengths: string[];
  open_weights: boolean;
  enabled: boolean;
};

export type RunRecord = {
  id: string;
  state_id: string;
  goal: string;
  status: string;
  model_id: string;
  context_window: number;
  context_used: number;
  started_at: string;
  finished_at: string | null;
  final_thoughts: string | null;
  changes: Array<Record<string, unknown>>;
};

export type AuditorRecap = {
  id: string;
  run_id: string | null;
  auditor_model_id: string;
  summary: string;
  decision: string;
  procedure_changes: Array<Record<string, unknown>>;
  created_at: string;
};

export type IntegrationStatus = {
  name: string;
  status: string;
  endpoint: string | null;
  last_checked_at: string | null;
  details: Record<string, unknown>;
};

export type ProcedureSnapshot = {
  states: ProcedureState[];
  transitions: Transition[];
  models: ModelProfile[];
  runs: RunRecord[];
  recaps: AuditorRecap[];
  integrations: IntegrationStatus[];
};

const API_URL = import.meta.env.VITE_CONSCIOUSNESS_API_URL ?? "http://localhost:8770";

export async function fetchProcedure(): Promise<ProcedureSnapshot> {
  try {
    const response = await fetch(`${API_URL}/procedure`);
    if (!response.ok) {
      throw new Error(`API responded ${response.status}`);
    }
    return await response.json();
  } catch {
    return sampleSnapshot;
  }
}

export async function tickProcedure(): Promise<ProcedureSnapshot> {
  const response = await fetch(`${API_URL}/tick`, { method: "POST" });
  if (!response.ok) {
    throw new Error(`Tick failed with ${response.status}`);
  }
  return fetchProcedure();
}
