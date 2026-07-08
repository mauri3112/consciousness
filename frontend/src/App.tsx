import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  ArrowRight,
  Box,
  CircleDot,
  Database,
  GitBranch,
  LayoutDashboard,
  Network,
  Pause,
  Play,
  RefreshCw,
  Settings,
  ShieldCheck,
  Square,
  Wrench
} from "lucide-react";
import { fetchProcedure, tickProcedure, type ProcedureSnapshot, type ProcedureState } from "./api";
import { sampleSnapshot } from "./sampleData";

const numberFormat = new Intl.NumberFormat();
const moneyFormat = new Intl.NumberFormat(undefined, { style: "currency", currency: "USD" });

export default function App() {
  const [snapshot, setSnapshot] = useState<ProcedureSnapshot>(sampleSnapshot);
  const [selectedStateId, setSelectedStateId] = useState<string>("gather");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let active = true;
    fetchProcedure().then((data) => {
      if (!active) {
        return;
      }
      setSnapshot(data);
      setSelectedStateId(data.states.find((state) => state.is_current)?.id ?? data.states[0]?.id ?? "gather");
    });
    return () => {
      active = false;
    };
  }, []);

  const currentState = useMemo(
    () => snapshot.states.find((state) => state.is_current) ?? snapshot.states[0],
    [snapshot.states]
  );
  const selectedState = useMemo(
    () => snapshot.states.find((state) => state.id === selectedStateId) ?? currentState,
    [currentState, selectedStateId, snapshot.states]
  );
  const latestRun = snapshot.runs[0];
  const currentModel = snapshot.models.find((model) => model.id === latestRun?.model_id) ?? snapshot.models[0];

  async function handleTick() {
    setLoading(true);
    try {
      const next = await tickProcedure();
      setSnapshot(next);
      setSelectedStateId(next.states.find((state) => state.is_current)?.id ?? selectedStateId);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="app-shell">
      <Sidebar integrationStatus={snapshot.integrations[0]?.status ?? "unknown"} />
      <main className="workspace">
        <TopBar currentState={currentState} loading={loading} onTick={handleTick} />
        <section className="main-grid">
          <GraphCanvas
            states={snapshot.states}
            transitions={snapshot.transitions}
            selectedStateId={selectedState.id}
            onSelect={setSelectedStateId}
          />
          <Inspector
            state={selectedState}
            run={latestRun}
            modelId={currentModel?.id ?? "unassigned"}
            guardrails={snapshot.guardrails}
          />
        </section>
        <section className="lower-grid">
          <ActivityStream recaps={snapshot.recaps} />
          <ModelBudget models={snapshot.models} run={latestRun} />
          <IntegrationPanel integration={snapshot.integrations[0]} />
        </section>
      </main>
    </div>
  );
}

function Sidebar({ integrationStatus }: { integrationStatus: string }) {
  const navGroups = [
    { label: "Procedure", items: [["Graph", Network], ["States", CircleDot], ["Transitions", GitBranch], ["Mutations", ShieldCheck]] },
    { label: "Execution", items: [["Runs", Activity], ["Tools", Wrench], ["Skills", Box], ["Models", Settings]] },
    { label: "Integrations", items: [["only-memories", Database]] }
  ] as const;

  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark" aria-hidden="true">
          <Network size={22} />
        </div>
        <div>
          <strong>Consciousness</strong>
          <span>agent harness</span>
        </div>
      </div>
      <nav>
        <a className="nav-overview" href="#overview">
          <LayoutDashboard size={18} />
          Overview
        </a>
        {navGroups.map((group) => (
          <div className="nav-group" key={group.label}>
            <p>{group.label}</p>
            {group.items.map(([label, Icon]) => (
              <a className={label === "Graph" ? "active" : ""} href={`#${label.toLowerCase()}`} key={label}>
                <Icon size={17} />
                <span>{label}</span>
                {label === "only-memories" ? <i className={`status-dot ${integrationStatus}`} /> : null}
              </a>
            ))}
          </div>
        ))}
      </nav>
      <div className="sidebar-footer">
        <Settings size={17} />
        <span>v0.1.0</span>
      </div>
    </aside>
  );
}

function TopBar({
  currentState,
  loading,
  onTick
}: {
  currentState: ProcedureState;
  loading: boolean;
  onTick: () => void;
}) {
  return (
    <header className="topbar">
      <div>
        <span className="top-label">Procedure</span>
        <strong>Research Loop</strong>
      </div>
      <div className="run-status">
        <span className="pulse" />
        <span>Current state</span>
        <strong>{currentState.name}</strong>
      </div>
      <div className="toolbar" aria-label="Loop controls">
        <button type="button" title="Pause loop">
          <Pause size={16} />
        </button>
        <button type="button" className="primary" title="Advance one state" onClick={onTick} disabled={loading}>
          {loading ? <RefreshCw size={16} className="spin" /> : <Play size={16} />}
          <span>Step</span>
        </button>
        <button type="button" title="Stop loop">
          <Square size={15} />
        </button>
      </div>
    </header>
  );
}

function GraphCanvas({
  states,
  transitions,
  selectedStateId,
  onSelect
}: {
  states: ProcedureState[];
  transitions: ProcedureSnapshot["transitions"];
  selectedStateId: string;
  onSelect: (stateId: string) => void;
}) {
  const stateMap = useMemo(() => new Map(states.map((state) => [state.id, state])), [states]);

  return (
    <section className="graph-panel" aria-label="Procedure graph">
      <div className="graph-toolbar">
        <div>
          <button type="button">Auto</button>
          <button type="button">100%</button>
        </div>
        <button type="button">
          <GitBranch size={16} />
          Legend
        </button>
      </div>
      <div className="graph-canvas">
        <svg viewBox="0 0 100 100" role="img" aria-label="Strongly connected procedure graph">
          <defs>
            <marker id="arrow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto" markerUnits="strokeWidth">
              <path d="M0,0 L0,6 L6,3 z" fill="currentColor" />
            </marker>
          </defs>
          {transitions.map((transition) => {
            const source = stateMap.get(transition.source_id);
            const target = stateMap.get(transition.target_id);
            if (!source || !target) {
              return null;
            }
            const curve = Math.abs(source.x - target.x) > 28 ? 10 : -8;
            const midX = (source.x + target.x) / 2;
            const midY = (source.y + target.y) / 2 + curve;
            return (
              <path
                className="edge"
                d={`M ${source.x} ${source.y} Q ${midX} ${midY} ${target.x} ${target.y}`}
                key={transition.id}
                markerEnd="url(#arrow)"
              />
            );
          })}
        </svg>
        {states.map((state) => (
          <button
            type="button"
            className={`state-node ${state.is_current ? "current" : ""} ${state.id === selectedStateId ? "selected" : ""}`}
            style={{ left: `${state.x}%`, top: `${state.y}%` }}
            key={state.id}
            onClick={() => onSelect(state.id)}
          >
            <span className="state-kind">{state.kind}</span>
            <strong>{state.name}</strong>
            <ContextMeter value={Math.min(100, Math.round((state.context_minimum / 200000) * 100))} />
          </button>
        ))}
        <div className="canvas-hint">Drag to pan · Scroll to zoom · Current state glows green</div>
      </div>
    </section>
  );
}

function ContextMeter({ value }: { value: number }) {
  return (
    <div className="context-meter" aria-label={`${value}% context pressure`}>
      <span style={{ width: `${value}%` }} />
      <em>{value}%</em>
    </div>
  );
}

function Inspector({
  state,
  run,
  modelId,
  guardrails
}: {
  state: ProcedureState;
  run: ProcedureSnapshot["runs"][number] | undefined;
  modelId: string;
  guardrails: ProcedureSnapshot["guardrails"];
}) {
  const contextPercent = run ? Math.round((run.context_used / run.context_window) * 100) : 0;

  return (
    <aside className="inspector">
      <div className="panel-header">
        <strong>Inspector</strong>
        <span>{state.is_current ? "Current state" : "Selected state"}</span>
      </div>
      <div className="state-title">
        <CircleDot size={24} />
        <div>
          <strong>{state.name}</strong>
          <span>{state.domain}</span>
        </div>
        <b>{state.kind}</b>
      </div>
      <InspectorBlock title="Agent Goal">{state.goal_template}</InspectorBlock>
      <InspectorBlock title="Tools">
        <TagList values={state.tools} />
      </InspectorBlock>
      <InspectorBlock title="Skills">
        <TagList values={state.skills} tone="blue" />
      </InspectorBlock>
      <InspectorBlock title="Model">
        <div className="model-line">
          <span>{modelId}</span>
          <em>{numberFormat.format(state.context_minimum)} min ctx</em>
        </div>
      </InspectorBlock>
      <InspectorBlock title="Context Window Budget">
        <div className="budget-bar">
          <span style={{ width: `${contextPercent}%` }} />
        </div>
        <div className="budget-row">
          <span>Used</span>
          <strong>{contextPercent}%</strong>
        </div>
      </InspectorBlock>
      <GuardrailsBlock state={state} run={run} guardrails={guardrails} />
      <InspectorBlock title="Final Thoughts">
        <p className="final-thoughts">{run?.final_thoughts ?? "No run has completed yet."}</p>
      </InspectorBlock>
    </aside>
  );
}

function GuardrailsBlock({
  state,
  run,
  guardrails
}: {
  state: ProcedureState;
  run: ProcedureSnapshot["runs"][number] | undefined;
  guardrails: ProcedureSnapshot["guardrails"];
}) {
  const policy = guardrails.capability_policies.find((item) => item.state_id === state.id);
  const confidence = run?.output?.confidence;

  return (
    <InspectorBlock title="Guardrails">
      <div className="guardrail-grid">
        <span>Mutation</span>
        <strong>{policy?.mutation_level ?? "bounded"}</strong>
        <span>Approval</span>
        <strong>{policy?.requires_approval ? "required" : "not required"}</strong>
        <span>Confidence</span>
        <strong>{confidence ? `${Math.round(confidence * 100)}%` : "pending"}</strong>
        <span>Backoff</span>
        <strong>{guardrails.loop_control.base_backoff_seconds}s</strong>
      </div>
      <p className="guardrail-note">{policy?.rationale ?? "State tools are constrained by procedure policy."}</p>
    </InspectorBlock>
  );
}

function InspectorBlock({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="inspector-block">
      <h2>{title}</h2>
      <div>{children}</div>
    </section>
  );
}

function TagList({ values, tone = "green" }: { values: string[]; tone?: "green" | "blue" }) {
  return (
    <div className={`tag-list ${tone}`}>
      {values.map((value) => (
        <span key={value}>{value}</span>
      ))}
    </div>
  );
}

function ActivityStream({ recaps }: { recaps: ProcedureSnapshot["recaps"] }) {
  return (
    <section className="data-panel activity-panel">
      <div className="panel-header">
        <strong>Auditor Recaps</strong>
        <span>{recaps.length} visible</span>
      </div>
      <div className="activity-list">
        {recaps.slice(0, 4).map((recap) => (
          <article key={recap.id}>
            <time>{new Date(recap.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time>
            <div>
              <strong>{recap.decision}</strong>
              <p>{recap.summary}</p>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

function ModelBudget({
  models,
  run
}: {
  models: ProcedureSnapshot["models"];
  run: ProcedureSnapshot["runs"][number] | undefined;
}) {
  return (
    <section className="data-panel model-panel">
      <div className="panel-header">
        <strong>Model Budget</strong>
        <span>relative cost table</span>
      </div>
      <table>
        <thead>
          <tr>
            <th>Model</th>
            <th>Context</th>
            <th>Cost</th>
            <th>Cap</th>
          </tr>
        </thead>
        <tbody>
          {models.slice(0, 5).map((model) => (
            <tr key={model.id} className={run?.model_id === model.id ? "active-row" : ""}>
              <td>{model.id}</td>
              <td>{numberFormat.format(model.context_window)}</td>
              <td>{model.relative_cost.toFixed(1)}x</td>
              <td>{moneyFormat.format(model.max_run_budget)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function IntegrationPanel({ integration }: { integration: ProcedureSnapshot["integrations"][number] | undefined }) {
  return (
    <section className="data-panel integration-panel">
      <div className="panel-header">
        <strong>only-memories</strong>
        <span>{integration?.status ?? "unknown"}</span>
      </div>
      <dl>
        <div>
          <dt>Endpoint</dt>
          <dd>{integration?.endpoint ?? "not configured"}</dd>
        </div>
        <div>
          <dt>Last check</dt>
          <dd>{integration?.last_checked_at ? new Date(integration.last_checked_at).toLocaleTimeString() : "never"}</dd>
        </div>
        <div>
          <dt>Mode</dt>
          <dd>{String(integration?.details?.mode ?? "read/write optional")}</dd>
        </div>
      </dl>
      <div className="integration-ok">
        <ArrowRight size={16} />
        Recaps can become artifact memories when enabled.
      </div>
    </section>
  );
}
