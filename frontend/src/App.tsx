import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Background,
  BackgroundVariant,
  Controls,
  MarkerType, Position,
  ReactFlow,
  type Connection,
  type Edge,
  type Node
} from "@xyflow/react";
import {
  Activity, AlertTriangle, Archive, Box, BrainCircuit, Check, CircleDot, Database,
  FileDiff, GitBranch, LayoutDashboard, Network, Pause, Play, Plus, RefreshCw,
  RotateCcw, Save, Settings, ShieldCheck, Square, Upload, X
} from "lucide-react";
import {
  activateDraft, createDraft, decideApproval, eventStreamUrl, exportUrl, fetchDiff,
  fetchProcedure, fetchRunEvents, importProcedure, issueControl, rollbackVersion,
  saveDraft, validateDraft, type ApprovalRecord, type ProcedureDefinition,
  type ProcedureSnapshot, type ProcedureState, type ProcedureVersion, type RunRecord
} from "./api";

type View = "overview" | "editor" | "runs" | "approvals" | "mutations" | "models" | "integrations";
const numberFormat = new Intl.NumberFormat();
const moneyFormat = new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", minimumFractionDigits: 4 });

export default function App() {
  const client = useQueryClient();
  const [view, setView] = useState<View>("overview");
  const [streamConnected, setStreamConnected] = useState(true);
  const query = useQuery({ queryKey: ["procedure"], queryFn: fetchProcedure, refetchInterval: 5000 });

  useEffect(() => {
    const stream = new EventSource(eventStreamUrl());
    stream.onopen = () => setStreamConnected(true);
    stream.onerror = () => setStreamConnected(false);
    stream.onmessage = () => client.invalidateQueries({ queryKey: ["procedure"] });
    stream.addEventListener("run.finished", () => client.invalidateQueries({ queryKey: ["procedure"] }));
    stream.addEventListener("runtime.status", () => client.invalidateQueries({ queryKey: ["procedure"] }));
    return () => stream.close();
  }, [client]);

  if (query.isLoading) return <LoadingScreen />;
  if (query.isError || !query.data) return <ConnectionError error={query.error} retry={() => query.refetch()} />;
  const snapshot = query.data;

  return (
    <div className="app-shell">
      <a className="skip-link" href="#studio-content">Skip to studio content</a>
      <Sidebar view={view} onView={setView} snapshot={snapshot} />
      <main className="workspace" id="studio-content" tabIndex={-1}>
        <TopBar snapshot={snapshot} onRefresh={() => query.refetch()} />
        <RuntimeAlerts snapshot={snapshot} streamConnected={streamConnected} />
        <div className="view-frame">
          {view === "overview" ? <Overview snapshot={snapshot} /> : null}
          {view === "editor" ? <ProcedureEditor snapshot={snapshot} /> : null}
          {view === "runs" ? <RunsView runs={snapshot.runs} /> : null}
          {view === "approvals" ? <ApprovalsView approvals={snapshot.approvals} /> : null}
          {view === "mutations" ? <MutationsView snapshot={snapshot} /> : null}
          {view === "models" ? <ModelsView snapshot={snapshot} /> : null}
          {view === "integrations" ? <IntegrationsView snapshot={snapshot} /> : null}
        </div>
      </main>
    </div>
  );
}

function Sidebar({ view, onView, snapshot }: { view: View; onView: (view: View) => void; snapshot: ProcedureSnapshot }) {
  const pending = snapshot.approvals.filter((item) => item.status === "pending").length;
  const groups: Array<{ label: string; items: Array<[View, string, typeof Network]> }> = [
    { label: "Procedure", items: [["editor", "Editor", GitBranch], ["mutations", "Mutations", FileDiff]] },
    { label: "Execution", items: [["runs", "Runs", Activity], ["approvals", "Approvals", ShieldCheck], ["models", "Models", Settings]] },
    { label: "Integrations", items: [["integrations", "only-memories", Database]] }
  ];
  return (
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark"><BrainCircuit size={21} /></div><div><strong>Consciousness</strong><span>Procedure studio</span></div></div>
      <button aria-current={view === "overview" ? "page" : undefined} aria-label="Overview" className={`overview-link ${view === "overview" ? "active" : ""}`} onClick={() => onView("overview")}><LayoutDashboard aria-hidden="true" size={17} /><span>Overview</span></button>
      <nav aria-label="Studio views">
        {groups.map((group) => <div className="nav-group" key={group.label}><p>{group.label}</p>{group.items.map(([key, label, Icon]) => (
          <button aria-current={view === key ? "page" : undefined} aria-label={key === "approvals" && pending ? `${label}, ${pending} pending` : label} className={view === key ? "active" : ""} key={key} onClick={() => onView(key)}>
            <Icon aria-hidden="true" size={16} /><span>{label}</span>
            {key === "approvals" && pending ? <b aria-hidden="true">{pending}</b> : null}
            {key === "integrations" ? <i aria-hidden="true" className={`status-dot ${snapshot.integrations[0]?.status ?? "unknown"}`} /> : null}
          </button>
        ))}</div>)}
      </nav>
      <div className="sidebar-foot"><span>System health</span><strong><i />{snapshot.runtime.execution_mode}</strong><small>Consciousness v1.0<br />local operator console</small></div>
    </aside>
  );
}

function TopBar({ snapshot, onRefresh }: { snapshot: ProcedureSnapshot; onRefresh: () => void }) {
  const client = useQueryClient();
  const control = useMutation({
    mutationFn: issueControl,
    onSuccess: () => setTimeout(() => client.invalidateQueries({ queryKey: ["procedure"] }), 400)
  });
  const status = snapshot.runtime.status;
  return (
    <header className="topbar">
      <div className="procedure-name"><span>Procedure</span><strong>{snapshot.version.definition.name}</strong><em>v{snapshot.version.version}</em></div>
      <div className="runtime-summary"><span>Runtime status</span><div><i className={`pulse ${status}`} /><strong>{status}</strong></div><small>Current state · {snapshot.runtime.current_state_id}</small></div>
      <div className="toolbar">
        <button aria-label="Refresh procedure status" title="Refresh" onClick={onRefresh}><RefreshCw aria-hidden="true" size={15} /><span>Refresh</span></button>
        <button aria-label="Pause runtime" title="Pause" onClick={() => control.mutate("pause")}><Pause aria-hidden="true" size={15} /><span>Pause</span></button>
        <button className="primary" onClick={() => control.mutate("step")} disabled={control.isPending}><Play size={15} />Step</button>
        <button title={status === "running" ? "Stop" : "Run continuously"} onClick={() => control.mutate(status === "running" ? "stop" : "run")}>
          {status === "running" ? <Square size={14} /> : <Activity size={15} />}<span>{status === "running" ? "Stop" : "Continuous"}</span>
        </button>
      </div>
    </header>
  );
}

function RuntimeAlerts({ snapshot, streamConnected }: { snapshot: ProcedureSnapshot; streamConnected: boolean }) {
  const integration = snapshot.integrations[0];
  const runtimeDegraded = ["degraded", "budget_blocked", "failed"].includes(snapshot.runtime.status);
  const integrationDegraded = integration && !["healthy", "disabled"].includes(integration.status);
  if (streamConnected && !runtimeDegraded && !integrationDegraded) return null;
  return <div className="runtime-alerts" role="status" aria-live="polite">
    {!streamConnected ? <div><AlertTriangle aria-hidden="true" size={15} /><span><strong>Live updates disconnected.</strong> Displaying the last confirmed snapshot; scheduled refresh remains active.</span></div> : null}
    {runtimeDegraded ? <div><AlertTriangle aria-hidden="true" size={15} /><span><strong>Runtime {snapshot.runtime.status}.</strong> Review the latest run before issuing another control action.</span></div> : null}
    {integrationDegraded ? <div><Database aria-hidden="true" size={15} /><span><strong>{integration.name} {integration.status}.</strong> Memory-backed actions may be unavailable.</span></div> : null}
  </div>;
}

function Overview({ snapshot }: { snapshot: ProcedureSnapshot }) {
  const [selectedId, setSelectedId] = useState(snapshot.runtime.current_state_id);
  const selected = snapshot.states.find((state) => state.id === selectedId) ?? snapshot.states[0];
  const latest = snapshot.runs.find((run) => run.state_id === selected.id) ?? snapshot.runs[0];
  return (
    <div className="overview-grid">
      <section className="graph-panel"><PanelHeader title="Live procedure" meta={`${snapshot.states.length} states · ${snapshot.transitions.length} transitions`} />
        <ProcedureGraph snapshot={snapshot} selectedId={selected.id} onSelect={setSelectedId} />
      </section>
      <StateInspector state={selected} run={latest} snapshot={snapshot} />
      <section className="lower-grid">
        <ActivityPanel snapshot={snapshot} />
        <BudgetPanel snapshot={snapshot} />
        <IntegrationPanel snapshot={snapshot} />
      </section>
    </div>
  );
}

function ProcedureGraph({ snapshot, selectedId, onSelect }: { snapshot: ProcedureSnapshot; selectedId: string; onSelect: (id: string) => void }) {
  const nodes = useMemo<Node[]>(() => snapshot.states.map((state, index) => ({
    id: state.id,
    position: { x: state.x * 8, y: state.y * 5.2 },
    data: { label: <div className="node-label"><div><b>{String(index + 1).padStart(2, "0")}</b><strong>{state.name}</strong><i /></div><p>{state.goal_template}</p><small>TOOLS {state.tools.length}<em />MODEL {state.model_policy}</small></div> },
    className: `procedure-node ${state.is_current ? "current" : ""} ${state.id === selectedId ? "selected" : ""}`,
    sourcePosition: Position.Right,
    targetPosition: Position.Left
  })), [snapshot.states, selectedId]);
  const edges = useMemo<Edge[]>(() => snapshot.transitions.filter((item) => item.active).map((item) => ({
    id: item.id, source: item.source_id, target: item.target_id, animated: item.source_id === snapshot.runtime.current_state_id,
    markerEnd: { type: MarkerType.ArrowClosed }, label: item.guard === "always" ? undefined : item.guard,
    style: { strokeWidth: item.weight }
  })), [snapshot.transitions, snapshot.runtime.current_state_id]);
  return <div className="flow-wrap"><ReactFlow nodes={nodes} edges={edges} onNodeClick={(_, node) => onSelect(node.id)} fitView minZoom={0.35} maxZoom={1.7} nodesDraggable={false} nodesConnectable={false}>
    <Background variant={BackgroundVariant.Dots} gap={18} size={1} /><Controls showInteractive={false} />
  </ReactFlow></div>;
}

function StateInspector({ state, run, snapshot }: { state: ProcedureState; run?: RunRecord; snapshot: ProcedureSnapshot }) {
  const policy = snapshot.guardrails.capability_policies.find((item) => item.state_id === state.id);
  const percent = run ? Math.min(100, Math.round((run.context_used / run.context_window) * 100)) : 0;
  return <aside className="inspector"><PanelHeader title="Inspector" meta={state.is_current ? "Current state" : "Selected state"} />
    <div className="state-heading"><CircleDot size={22} /><div><strong>{state.name}</strong><span>{state.domain}</span></div><b>{state.kind}</b></div>
    <InspectorSection title="Agent goal"><p>{state.goal_template}</p></InspectorSection>
    <InspectorSection title="Tools"><TagList values={state.tools} /></InspectorSection>
    <InspectorSection title="Skills"><TagList values={state.skills} blue /></InspectorSection>
    <InspectorSection title="Model policy"><div className="kv"><span>{state.model_policy}</span><strong>{numberFormat.format(state.context_minimum)} min ctx</strong></div></InspectorSection>
    <InspectorSection title="Context budget"><div className="meter"><i style={{ width: `${percent}%` }} /></div><div className="kv"><span>Used</span><strong>{percent}%</strong></div></InspectorSection>
    <InspectorSection title="Guardrails"><div className="guardrail-list"><span>Mutation <b>{policy?.mutation_level ?? "bounded"}</b></span><span>Approval <b>{policy?.requires_approval ? "required" : "automatic"}</b></span><span>Attempts <b>{state.max_attempts}</b></span></div><p className="note">{policy?.rationale}</p></InspectorSection>
    <InspectorSection title="Final thoughts"><p className="thoughts">{run?.final_thoughts ?? "No completed run for this state."}</p></InspectorSection>
  </aside>;
}

function ProcedureEditor({ snapshot }: { snapshot: ProcedureSnapshot }) {
  const client = useQueryClient();
  const [draft, setDraft] = useState<ProcedureVersion | null>(null);
  const [definition, setDefinition] = useState<ProcedureDefinition>(snapshot.version.definition);
  const [selectedId, setSelectedId] = useState(snapshot.runtime.current_state_id);
  const [selectedEdgeId, setSelectedEdgeId] = useState("");
  const [validation, setValidation] = useState<string[]>([]);
  const [diff, setDiff] = useState("");
  const [busy, setBusy] = useState(false);
  const selected = definition.states.find((state) => state.id === selectedId);
  const selectedEdge = definition.transitions.find((edge) => edge.id === selectedEdgeId);
  const selectedPolicy = definition.guardrails.capability_policies.find((policy) => policy.state_id === selectedId);
  const nodes = useMemo<Node[]>(() => definition.states.map((state, index) => ({
    id: state.id, position: { x: state.x * 8, y: state.y * 5.2 }, data: { label: <div className="node-label"><div><b>{String(index + 1).padStart(2, "0")}</b><strong>{state.name}</strong><i /></div><p>{state.goal_template}</p><small>TOOLS {state.tools.length}<em />MODEL {state.model_policy}</small></div> },
    className: `procedure-node ${state.id === selectedId ? "selected" : ""}`
  })), [definition.states, selectedId]);
  const edges = useMemo<Edge[]>(() => definition.transitions.map((item) => ({ id: item.id, source: item.source_id, target: item.target_id, markerEnd: { type: MarkerType.ArrowClosed }, label: item.guard })), [definition.transitions]);

  async function begin() { setBusy(true); try { const next = await createDraft(); setDraft(next); setDefinition(next.definition); setValidation([]); } finally { setBusy(false); } }
  async function save() { if (!draft) return; setBusy(true); try { const next = await saveDraft(draft, definition); setDraft(next); const check = await validateDraft(next.id); setValidation(check.errors); } finally { setBusy(false); } }
  async function review() { if (!draft) return; const value = await fetchDiff(snapshot.version.id, draft.id); setDiff(value.diff || "No semantic changes."); }
  async function activate() { if (!draft) return; setBusy(true); try { const check = await validateDraft(draft.id); setValidation(check.errors); if (!check.valid) return; await activateDraft(draft.id); setDraft(null); setDiff(""); await client.invalidateQueries({ queryKey: ["procedure"] }); } finally { setBusy(false); } }
  const onConnect = useCallback((connection: Connection) => {
    if (!draft || !connection.source || !connection.target) return;
    const id = `${connection.source}_to_${connection.target}_${Date.now()}`;
    setDefinition((current) => ({ ...current, transitions: [...current.transitions, { id, source_id: connection.source!, target_id: connection.target!, weight: 1, guard: "always", rationale: "Operator-authored transition.", active: true }] }));
  }, [draft]);
  function moveNode(_: unknown, node: Node) { if (!draft) return; setDefinition((current) => ({ ...current, states: current.states.map((state) => state.id === node.id ? { ...state, x: node.position.x / 8, y: node.position.y / 5.2 } : state) })); }
  function updateState(patch: Partial<ProcedureState>) { if (!selected) return; setDefinition((current) => ({ ...current, states: current.states.map((state) => state.id === selected.id ? { ...state, ...patch } : state) })); }
  function updatePolicy(patch: Partial<ProcedureDefinition["guardrails"]["capability_policies"][number]>) { if (!selectedPolicy) return; setDefinition((current) => ({ ...current, guardrails: { ...current.guardrails, capability_policies: current.guardrails.capability_policies.map((policy) => policy.state_id === selectedId ? { ...policy, ...patch } : policy) } })); }
  function updateTransition(patch: Partial<ProcedureDefinition["transitions"][number]>) { if (!selectedEdge) return; setDefinition((current) => ({ ...current, transitions: current.transitions.map((edge) => edge.id === selectedEdge.id ? { ...edge, ...patch } : edge) })); }
  function addState() {
    if (!draft) return;
    let index = definition.states.length + 1; let id = `state_${index}`;
    while (definition.states.some((state) => state.id === id)) { index += 1; id = `state_${index}`; }
    const next: ProcedureState = { id, name: `State ${index}`, kind: "custom", domain: "New domain", goal_template: "Define the goal for this state.", prompt_contract: "Follow the state contract and preserve evidence.", output_contract: "A structured durable result.", tools: [], skills: [], context_minimum: 32768, output_reserve: 4096, model_policy: "cheap-capable", max_attempts: 2, max_run_budget: null, x: 50, y: 50, is_current: false };
    const policy = { state_id: id, allowed_tool_patterns: [], mutation_level: "read_only", requires_approval: false, rationale: "New states begin read-only." };
    setDefinition((current) => ({ ...current, states: [...current.states, next], guardrails: { ...current.guardrails, capability_policies: [...current.guardrails.capability_policies, policy] } })); setSelectedEdgeId(""); setSelectedId(id);
  }
  function removeState() { if (!draft || !selected || definition.states.length <= 1) return; setDefinition((current) => ({ ...current, states: current.states.filter((state) => state.id !== selected.id), transitions: current.transitions.filter((edge) => edge.source_id !== selected.id && edge.target_id !== selected.id), guardrails: { ...current.guardrails, capability_policies: current.guardrails.capability_policies.filter((policy) => policy.state_id !== selected.id) } })); setSelectedId(definition.states.find((state) => state.id !== selected.id)?.id ?? ""); }
  async function handleImport(file: File) { const parsed = JSON.parse(await file.text()) as ProcedureDefinition; const imported = await importProcedure(parsed); setDraft(imported); setDefinition(imported.definition); }

  return <div className="editor-layout">
    <section className="editor-canvas"><div className="editor-toolbar"><div><strong>Procedure editor</strong><span>{draft ? `Draft v${draft.version} · revision ${draft.revision}` : "Active version · read only"}</span></div><div>
      <a className="button" href={exportUrl()} target="_blank" rel="noreferrer"><Archive size={14} />Export</a>
      <label className="button"><Upload size={14} />Import<input type="file" accept="application/json" onChange={(event) => event.target.files?.[0] && handleImport(event.target.files[0])} /></label>
      {!draft ? <button className="primary" onClick={begin} disabled={busy}><GitBranch size={14} />Create draft</button> : <><button onClick={addState}><Plus size={14} />State</button><button onClick={save} disabled={busy}><Save size={14} />Save</button><button onClick={review}><FileDiff size={14} />Review</button><button className="primary" onClick={activate} disabled={busy}><Check size={14} />Activate</button></>}
    </div></div>
    {validation.length ? <div className="validation-banner"><AlertTriangle size={16} /><div><strong>Draft cannot activate</strong>{validation.map((error) => <span key={error}>{error}</span>)}</div></div> : null}
    <div className="editor-flow"><ReactFlow nodes={nodes} edges={edges} onConnect={onConnect} onNodeClick={(_, node) => { setSelectedEdgeId(""); setSelectedId(node.id); }} onEdgeClick={(_, edge) => setSelectedEdgeId(edge.id)} onNodeDragStop={moveNode} nodesDraggable={Boolean(draft)} nodesConnectable={Boolean(draft)} fitView>
      <Background variant={BackgroundVariant.Dots} gap={18} size={1} /><Controls />
    </ReactFlow></div></section>
    <aside className="editor-inspector"><PanelHeader title={selectedEdge ? "Transition contract" : "State contract"} meta={selectedEdge?.id ?? selected?.id ?? "none"} />{selectedEdge ? <div className="form-stack">
      <div className="field-pair"><Field label="Source"><input value={selectedEdge.source_id} disabled /></Field><Field label="Target"><input value={selectedEdge.target_id} disabled /></Field></div>
      <Field label="Guard"><input value={selectedEdge.guard} disabled={!draft} onChange={(e) => updateTransition({ guard: e.target.value })} /></Field>
      <Field label="Rationale"><textarea value={selectedEdge.rationale} disabled={!draft} onChange={(e) => updateTransition({ rationale: e.target.value })} /></Field>
      <div className="field-pair"><Field label="Weight"><input type="number" min="0" max="2" step="0.05" value={selectedEdge.weight} disabled={!draft} onChange={(e) => updateTransition({ weight: Number(e.target.value) })} /></Field><Field label="Active"><select value={String(selectedEdge.active)} disabled={!draft} onChange={(e) => updateTransition({ active: e.target.value === "true" })}><option value="true">active</option><option value="false">disabled</option></select></Field></div>
      {draft ? <button className="danger" onClick={() => { setDefinition((current) => ({ ...current, transitions: current.transitions.filter((edge) => edge.id !== selectedEdge.id) })); setSelectedEdgeId(""); }}><X size={14} />Remove transition</button> : null}
    </div> : selected ? <div className="form-stack">
      <Field label="Name"><input value={selected.name} disabled={!draft} onChange={(e) => updateState({ name: e.target.value })} /></Field>
      <div className="field-pair"><Field label="Kind"><select value={selected.kind} disabled={!draft} onChange={(e) => updateState({ kind: e.target.value })}>{["gather","curate","synthesize","validate","publish","audit","custom"].map((item) => <option key={item}>{item}</option>)}</select></Field><Field label="Model policy"><input value={selected.model_policy} disabled={!draft} onChange={(e) => updateState({ model_policy: e.target.value })} /></Field></div>
      <Field label="Domain"><input value={selected.domain} disabled={!draft} onChange={(e) => updateState({ domain: e.target.value })} /></Field>
      <Field label="Goal"><textarea value={selected.goal_template} disabled={!draft} onChange={(e) => updateState({ goal_template: e.target.value })} /></Field>
      <Field label="Prompt contract"><textarea value={selected.prompt_contract} disabled={!draft} onChange={(e) => updateState({ prompt_contract: e.target.value })} /></Field>
      <Field label="Output contract"><textarea value={selected.output_contract} disabled={!draft} onChange={(e) => updateState({ output_contract: e.target.value })} /></Field>
      <Field label="Tools (one per line)"><textarea value={selected.tools.join("\n")} disabled={!draft} onChange={(e) => updateState({ tools: e.target.value.split("\n").filter(Boolean) })} /></Field>
      <Field label="Skills (one per line)"><textarea value={selected.skills.join("\n")} disabled={!draft} onChange={(e) => updateState({ skills: e.target.value.split("\n").filter(Boolean) })} /></Field>
      <div className="field-pair"><Field label="Minimum context"><input type="number" value={selected.context_minimum} disabled={!draft} onChange={(e) => updateState({ context_minimum: Number(e.target.value) })} /></Field><Field label="Output reserve"><input type="number" value={selected.output_reserve} disabled={!draft} onChange={(e) => updateState({ output_reserve: Number(e.target.value) })} /></Field></div>
      {selectedPolicy ? <><div className="field-pair"><Field label="Mutation level"><input value={selectedPolicy.mutation_level} disabled={!draft} onChange={(e) => updatePolicy({ mutation_level: e.target.value })} /></Field><Field label="Approval"><select value={String(selectedPolicy.requires_approval)} disabled={!draft} onChange={(e) => updatePolicy({ requires_approval: e.target.value === "true" })}><option value="false">bounded automatic</option><option value="true">required</option></select></Field></div><Field label="Allowed tool patterns"><textarea value={selectedPolicy.allowed_tool_patterns.join("\n")} disabled={!draft} onChange={(e) => updatePolicy({ allowed_tool_patterns: e.target.value.split("\n").filter(Boolean) })} /></Field><Field label="Guardrail rationale"><textarea value={selectedPolicy.rationale} disabled={!draft} onChange={(e) => updatePolicy({ rationale: e.target.value })} /></Field></> : null}
      {draft ? <button className="danger" onClick={removeState}><X size={14} />Remove state</button> : null}
    </div> : <EmptyState label="Select a state" />}</aside>
    {diff ? <div className="diff-drawer"><div><strong>Activation diff</strong><button onClick={() => setDiff("")}><X size={15} /></button></div><pre>{diff}</pre></div> : null}
  </div>;
}

function RunsView({ runs }: { runs: RunRecord[] }) {
  const [selectedId, setSelectedId] = useState(runs[0]?.id ?? "");
  const selected = runs.find((run) => run.id === selectedId);
  const events = useQuery({ queryKey: ["run-events", selectedId], queryFn: () => fetchRunEvents(selectedId), enabled: Boolean(selectedId) });
  return <div className="split-view"><section className="list-panel"><PanelHeader title="Runs" meta={`${runs.length} recent`} />{runs.map((run) => <button className={`run-row ${run.id === selectedId ? "selected" : ""}`} key={run.id} onClick={() => setSelectedId(run.id)}><i className={`run-dot ${run.status}`} /><div><strong>{run.state_id}</strong><span>{run.model_id}</span></div><time>{new Date(run.started_at).toLocaleString()}</time><b>{run.status}</b></button>)}</section>
    <section className="detail-panel">{selected ? <><PanelHeader title={selected.id} meta={selected.status} /><div className="detail-grid"><Metric label="Model" value={selected.model_id} /><Metric label="Context" value={`${numberFormat.format(selected.context_used)} / ${numberFormat.format(selected.context_window)}`} /><Metric label="Tokens" value={`${selected.input_tokens} in · ${selected.output_tokens} out`} /><Metric label="Cost" value={moneyFormat.format(selected.cost)} /></div><InspectorSection title="Goal"><p>{selected.goal}</p></InspectorSection><InspectorSection title="Structured output"><pre>{JSON.stringify(selected.output, null, 2)}</pre></InspectorSection><InspectorSection title="Event timeline"><div className="timeline">{events.data?.map((event) => <article key={event.id}><time>{new Date(event.created_at).toLocaleTimeString()}</time><strong>{event.event_type}</strong><span>{JSON.stringify(event.payload)}</span></article>)}</div></InspectorSection></> : <EmptyState label="No run selected" />}</section></div>;
}

function ApprovalsView({ approvals }: { approvals: ApprovalRecord[] }) {
  return <section className="table-view" aria-labelledby="approval-queue-heading"><PanelHeader id="approval-queue-heading" title="Approval queue" meta={`${approvals.filter((item) => item.status === "pending").length} pending`} /><div className="approval-list">{approvals.length ? approvals.map((item) => <ApprovalCard key={item.id} item={item} />) : <EmptyState label="No approval requests" />}</div></section>;
}

function ApprovalCard({ item }: { item: ApprovalRecord }) {
  const client = useQueryClient();
  const [intent, setIntent] = useState<"approve" | "reject" | null>(null);
  const [note, setNote] = useState("");
  const noteId = useId();
  const noteRef = useRef<HTMLTextAreaElement>(null);
  const decision = useMutation({
    mutationFn: ({ approved, decisionNote }: { approved: boolean; decisionNote: string }) => decideApproval(item.id, approved, decisionNote),
    onSuccess: () => { setIntent(null); setNote(""); return client.invalidateQueries({ queryKey: ["procedure"] }); }
  });
  function review(next: "approve" | "reject") {
    setIntent(next);
    requestAnimationFrame(() => noteRef.current?.focus());
  }
  const actionLabel = intent === "approve" ? "Approve request" : "Reject request";
  return <article aria-labelledby={`approval-${item.id}`}>
    <div className="approval-icon"><ShieldCheck aria-hidden="true" size={20} /></div>
    <div><div className="row-title"><strong id={`approval-${item.id}`}>{item.kind}</strong><span className={`status-chip ${item.status}`}>{item.status}</span></div><p><strong>Risk:</strong> {item.risk}</p><pre aria-label="Proposed action">{JSON.stringify(item.proposed_action, null, 2)}</pre>{item.evidence.length ? <ul className="evidence-list" aria-label="Supporting evidence">{item.evidence.map((source) => <li key={`${source.kind}-${source.uri}`}><span>{source.label}</span><code>{source.uri}</code></li>)}</ul> : null}<small>Requested {new Date(item.requested_at).toLocaleString()}</small></div>
    {item.status === "pending" ? <div className="decision-area">{intent ? <div className="decision-confirm" role="group" aria-label={`${actionLabel} confirmation`}><label htmlFor={noteId}>Decision note <span>(recommended)</span></label><textarea ref={noteRef} id={noteId} value={note} onChange={(event) => setNote(event.target.value)} placeholder="Record why this action is safe or unsafe." /><p>This decision is recorded in the audit trail.</p>{decision.isError ? <p className="inline-error" role="alert">Decision failed: {decision.error.message}</p> : null}<div className="decision-buttons"><button onClick={() => setIntent(null)} disabled={decision.isPending}>Cancel</button><button className={intent === "approve" ? "primary" : "danger"} onClick={() => decision.mutate({ approved: intent === "approve", decisionNote: note.trim() })} disabled={decision.isPending}>{decision.isPending ? "Recording…" : `Confirm ${intent}`}</button></div></div> : <div className="decision-buttons"><button className="danger" onClick={() => review("reject")}><X aria-hidden="true" size={14} />Review rejection</button><button className="primary" onClick={() => review("approve")}><Check aria-hidden="true" size={14} />Review approval</button></div>}</div> : item.decision_note ? <p className="decision-note"><strong>Decision note</strong>{item.decision_note}</p> : null}
  </article>;
}

function MutationsView({ snapshot }: { snapshot: ProcedureSnapshot }) {
  return <section className="table-view" aria-labelledby="mutation-history-heading"><PanelHeader id="mutation-history-heading" title="Procedure mutations" meta="immutable version history" /><div className="mutation-list">{snapshot.mutations.map((item) => <MutationCard key={item.id} item={item} />)}{!snapshot.mutations.length ? <EmptyState label="No procedure mutations" /> : null}</div></section>;
}

function MutationCard({ item }: { item: ProcedureSnapshot["mutations"][number] }) {
  const client = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const rollback = useMutation({ mutationFn: () => rollbackVersion(item.rollback_version_id), onSuccess: () => client.invalidateQueries({ queryKey: ["procedure"] }) });
  return <article><div className="row-title"><strong>{item.rationale}</strong><span className={`status-chip ${item.status}`}>{item.status}</span></div><p>{item.base_version_id} → {item.proposed_version_id}</p><dl className="impact-summary"><div><dt>Budget impact</dt><dd>{Object.keys(item.budget_impact).length ? JSON.stringify(item.budget_impact) : "No recorded change"}</dd></div><div><dt>Rollback target</dt><dd>{item.rollback_version_id}</dd></div></dl><pre aria-label="Mutation diff">{item.diff || "No textual diff."}</pre>{rollback.isError ? <p className="inline-error" role="alert">Rollback failed: {rollback.error.message}</p> : null}{confirming ? <div className="rollback-confirm" role="group" aria-label="Rollback confirmation"><AlertTriangle aria-hidden="true" size={16} /><p><strong>Activate rollback version?</strong>This creates a new immutable active version; history is preserved.</p><div className="decision-buttons"><button onClick={() => setConfirming(false)} disabled={rollback.isPending}>Cancel</button><button className="danger" onClick={() => rollback.mutate()} disabled={rollback.isPending}>{rollback.isPending ? "Rolling back…" : "Confirm rollback"}</button></div></div> : <button onClick={() => setConfirming(true)}><RotateCcw aria-hidden="true" size={14} />Review rollback</button>}</article>;
}

function ModelsView({ snapshot }: { snapshot: ProcedureSnapshot }) {
  return <section className="table-view"><PanelHeader title="Model registry" meta="operator-maintained policy data" /><table className="data-table"><thead><tr><th>Model</th><th>Provider</th><th>Context</th><th>Tier</th><th>Run cap</th><th>Capabilities</th></tr></thead><tbody>{snapshot.models.map((model) => <tr key={model.id}><td><strong>{model.id}</strong></td><td>{model.provider}</td><td>{numberFormat.format(model.context_window)}</td><td>{model.quality_tier}</td><td>{moneyFormat.format(model.max_run_budget)}</td><td><TagList values={model.capabilities} /></td></tr>)}</tbody></table></section>;
}

function IntegrationsView({ snapshot }: { snapshot: ProcedureSnapshot }) {
  return <section className="table-view"><PanelHeader title="Integrations" meta="optional public contracts" /><div className="integration-cards">{snapshot.integrations.map((item) => <article key={item.name}><Database size={24} /><div><div className="row-title"><strong>{item.name}</strong><span className={`status-chip ${item.status}`}>{item.status}</span></div><p>{item.endpoint ?? "Not configured"}</p><pre>{JSON.stringify(item.details, null, 2)}</pre><small>{item.last_checked_at ? `Checked ${new Date(item.last_checked_at).toLocaleString()}` : "Never checked"}</small></div></article>)}</div></section>;
}

function ActivityPanel({ snapshot }: { snapshot: ProcedureSnapshot }) { return <section className="data-panel"><PanelHeader title="Activity" meta={`${snapshot.recaps.length} recaps`} /><div className="activity-list">{snapshot.recaps.slice(0, 4).map((recap) => <article key={recap.id}><time>{new Date(recap.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time><div><strong>{recap.decision}</strong><p>{recap.summary}</p></div></article>)}</div></section>; }
function BudgetPanel({ snapshot }: { snapshot: ProcedureSnapshot }) { const spent = snapshot.runs.reduce((sum, run) => sum + run.cost, 0); return <section className="data-panel"><PanelHeader title="Model budget" meta={`${moneyFormat.format(spent)} recorded`} /><table><thead><tr><th>Model</th><th>Context</th><th>Cost</th></tr></thead><tbody>{snapshot.models.slice(0,4).map((model) => <tr key={model.id}><td>{model.id}</td><td>{numberFormat.format(model.context_window)}</td><td>{model.relative_cost.toFixed(1)}x</td></tr>)}</tbody></table></section>; }
function IntegrationPanel({ snapshot }: { snapshot: ProcedureSnapshot }) { const item = snapshot.integrations[0]; return <section className="data-panel"><PanelHeader title="only-memories" meta={item?.status ?? "unknown"} /><dl><div><dt>Endpoint</dt><dd>{item?.endpoint ?? "disabled"}</dd></div><div><dt>Last check</dt><dd>{item?.last_checked_at ? new Date(item.last_checked_at).toLocaleTimeString() : "never"}</dd></div><div><dt>Mode</dt><dd>bounded writes</dd></div></dl></section>; }
function PanelHeader({ title, meta, id }: { title: string; meta: string; id?: string }) { return <div className="panel-header"><h2 id={id}>{title}</h2><span>{meta}</span></div>; }
function InspectorSection({ title, children }: { title: string; children: React.ReactNode }) { return <section className="inspector-section"><h2>{title}</h2>{children}</section>; }
function TagList({ values, blue = false }: { values: string[]; blue?: boolean }) { return <div className={`tag-list ${blue ? "blue" : ""}`}>{values.map((value) => <span key={value}>{value}</span>)}</div>; }
function Field({ label, children }: { label: string; children: React.ReactNode }) { return <label className="field"><span>{label}</span>{children}</label>; }
function Metric({ label, value }: { label: string; value: string }) { return <div className="metric"><span>{label}</span><strong>{value}</strong></div>; }
function EmptyState({ label }: { label: string }) { return <div className="empty-state"><Box size={24} /><strong>{label}</strong></div>; }
function LoadingScreen() { return <div className="loading-screen"><BrainCircuit size={30} /><strong>Loading durable runtime…</strong></div>; }
function ConnectionError({ error, retry }: { error: Error | null; retry: () => void }) { return <main className="loading-screen error" role="alert"><AlertTriangle aria-hidden="true" size={30} /><h1>Studio cannot reach the API</h1><p>{error?.message ?? "Unknown connection error"}</p><p>Confirm the Consciousness API is available, then retry. No operator action was submitted.</p><button onClick={retry}><RefreshCw aria-hidden="true" size={14} />Retry connection</button></main>; }
