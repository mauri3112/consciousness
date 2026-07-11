from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Annotated, Literal

from pydantic import BaseModel, Field


class RunStatus(StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    interrupted = "interrupted"
    blocked = "blocked"


class StateKind(StrEnum):
    gather = "gather"
    curate = "curate"
    synthesize = "synthesize"
    validate = "validate"
    publish = "publish"
    audit = "audit"
    custom = "custom"


class RuntimeStatus(StrEnum):
    stopped = "stopped"
    paused = "paused"
    running = "running"
    degraded = "degraded"
    budget_blocked = "budget_blocked"


class CommandKind(StrEnum):
    step = "step"
    run = "run"
    pause = "pause"
    resume = "resume"
    stop = "stop"


class ApprovalStatus(StrEnum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    executed = "executed"


class ProcedureState(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    name: str = Field(min_length=1)
    kind: StateKind
    domain: str = Field(min_length=1)
    goal_template: str = Field(min_length=1)
    prompt_contract: str = Field(min_length=1)
    output_contract: str = Field(min_length=1)
    tools: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    context_minimum: int = Field(default=32_768, ge=1)
    output_reserve: int = Field(default=4_096, ge=256)
    model_policy: str = "cheap-capable"
    max_attempts: int = Field(default=2, ge=1, le=10)
    max_run_budget: float | None = Field(default=None, ge=0)
    x: float = Field(default=50, ge=0, le=100)
    y: float = Field(default=50, ge=0, le=100)
    is_current: bool = False


class Transition(BaseModel):
    id: str
    source_id: str
    target_id: str
    weight: float = Field(default=1, ge=0, le=2)
    guard: str = "always"
    rationale: str = Field(min_length=1)
    active: bool = True


class ModelProfile(BaseModel):
    id: str
    provider: str
    model: str
    context_window: int = Field(ge=1)
    relative_cost: float = Field(ge=0)
    max_run_budget: float = Field(ge=0)
    quality_tier: int = Field(ge=1, le=5)
    strengths: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=lambda: ["structured-output"])
    input_cost_per_million: float = Field(default=0, ge=0)
    output_cost_per_million: float = Field(default=0, ge=0)
    open_weights: bool = False
    enabled: bool = True


class CapabilityPolicy(BaseModel):
    state_id: str
    allowed_tool_patterns: list[str]
    mutation_level: str
    requires_approval: bool
    rationale: str


class LoopControlPolicy(BaseModel):
    manual_pause_enabled: bool = True
    sleep_window: str = "operator-defined"
    base_backoff_seconds: int = Field(default=60, ge=1)
    max_backoff_seconds: int = Field(default=3600, ge=1)
    max_consecutive_failures: int = Field(default=3, ge=1)
    daily_budget_cap: float = Field(default=5.0, ge=0)
    degraded_mode: str = "local_only"


class OutputEvidencePolicy(BaseModel):
    structured_output_required: bool = True
    changed_resources_required: bool = True
    confidence_required: bool = True
    unresolved_risks_required: bool = True
    source_links_required: bool = True
    artifact_pointer_required: bool = True


class GuardrailSnapshot(BaseModel):
    capability_policies: list[CapabilityPolicy]
    loop_control: LoopControlPolicy
    evidence_policy: OutputEvidencePolicy


class ProcedureDefinition(BaseModel):
    name: str = "Research Loop"
    states: list[ProcedureState]
    transitions: list[Transition]
    models: list[ModelProfile]
    guardrails: GuardrailSnapshot


class ProcedureVersion(BaseModel):
    id: str
    version: int
    status: Literal["draft", "active", "superseded"]
    digest: str
    parent_id: str | None = None
    revision: int = 1
    definition: ProcedureDefinition
    created_by_run_id: str | None = None
    created_at: datetime
    activated_at: datetime | None = None


class SourceLink(BaseModel):
    label: str
    kind: str
    uri: str


class ArtifactPointer(BaseModel):
    label: str
    kind: str
    uri: str
    content_hash: str | None = None


class ContextItem(BaseModel):
    id: str
    label: str
    content: str
    source_uri: str | None = None
    content_hash: str | None = None
    token_estimate: int = 0
    score: float = 0


class ContextBundle(BaseModel):
    kind: Literal["context_bundle"] = "context_bundle"
    query: str
    items: list[ContextItem] = Field(default_factory=list)
    omitted_items: int = 0


class MemoryChange(BaseModel):
    action: Literal["remember", "forget", "restore", "supersede", "reinforce"]
    memory_id: str | None = None
    content: str | None = None
    reason: str
    source_ids: list[str] = Field(default_factory=list)
    requires_approval: bool = False


class MemoryChangeProposal(BaseModel):
    kind: Literal["memory_change_proposal"] = "memory_change_proposal"
    changes: list[MemoryChange] = Field(default_factory=list)


class SynthesisArtifact(BaseModel):
    kind: Literal["synthesis_artifact"] = "synthesis_artifact"
    title: str
    body: str
    dependencies: list[str] = Field(default_factory=list)
    suggested_connections: list[str] = Field(default_factory=list)


class ValidationFinding(BaseModel):
    change_index: int
    accepted: bool
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)


class ValidationReport(BaseModel):
    kind: Literal["validation_report"] = "validation_report"
    sufficient_evidence: bool
    findings: list[ValidationFinding] = Field(default_factory=list)


class PublishReceipt(BaseModel):
    kind: Literal["publish_receipt"] = "publish_receipt"
    applied: list[str] = Field(default_factory=list)
    pending_approval_ids: list[str] = Field(default_factory=list)


class AuditDecision(BaseModel):
    kind: Literal["audit_decision"] = "audit_decision"
    decision: Literal["continue", "degrade", "pause", "propose_mutation"] = "continue"
    model_recommendation: str | None = None
    mutation_summary: str | None = None
    mutation_patch: list[dict[str, Any]] = Field(default_factory=list)


StatePayload = Annotated[
    ContextBundle
    | MemoryChangeProposal
    | SynthesisArtifact
    | ValidationReport
    | PublishReceipt
    | AuditDecision,
    Field(discriminator="kind"),
]


class RunOutput(BaseModel):
    summary: str
    confidence: float = Field(ge=0, le=1)
    changed_resources: list[ArtifactPointer] = Field(default_factory=list)
    source_links: list[SourceLink] = Field(default_factory=list)
    unresolved_risks: list[str] = Field(default_factory=list)
    next_transition_recommendation: str
    payload: StatePayload | None = None


class ContextManifest(BaseModel):
    items: list[ContextItem] = Field(default_factory=list)
    total_estimated_tokens: int = 0
    reserved_output_tokens: int = 0
    truncated: bool = False


class RunRecord(BaseModel):
    id: str
    state_id: str
    procedure_version_id: str
    goal: str
    status: RunStatus
    attempt: int = 1
    model_id: str
    provider: str
    provider_request_id: str | None = None
    context_window: int
    context_used: int
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost: float = 0
    context_manifest: ContextManifest = Field(default_factory=ContextManifest)
    started_at: datetime
    heartbeat_at: datetime | None = None
    finished_at: datetime | None = None
    final_thoughts: str | None = None
    changes: list[dict[str, Any]] = Field(default_factory=list)
    output: RunOutput | None = None
    error_category: str | None = None
    error_message: str | None = None


class RunEvent(BaseModel):
    id: int
    run_id: str | None = None
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class RuntimeState(BaseModel):
    active_version_id: str
    current_state_id: str
    status: RuntimeStatus
    interval_seconds: int
    worker_id: str | None = None
    lease_expires_at: datetime | None = None
    heartbeat_at: datetime | None = None
    failure_count: int = 0
    backoff_until: datetime | None = None
    daily_budget_cap: float = 5
    execution_mode: Literal["preview", "live"] = "preview"
    updated_at: datetime


class RuntimeCommand(BaseModel):
    id: int
    kind: CommandKind
    status: Literal["pending", "claimed", "completed", "failed"]
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    claimed_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None


class ArtifactRecord(BaseModel):
    id: str
    run_id: str
    label: str
    kind: str
    uri: str
    path: str
    content_hash: str
    mime_type: str
    size_bytes: int
    created_at: datetime


class ToolCallRecord(BaseModel):
    id: str
    run_id: str
    tool_name: str
    status: str
    mutation_level: str
    idempotency_key: str
    arguments: dict[str, Any]
    result: dict[str, Any] | None = None
    approval_id: str | None = None
    started_at: datetime
    finished_at: datetime | None = None


class ApprovalRecord(BaseModel):
    id: str
    run_id: str | None = None
    kind: str
    status: ApprovalStatus
    risk: str
    proposed_action: dict[str, Any]
    evidence: list[SourceLink] = Field(default_factory=list)
    requested_at: datetime
    decided_at: datetime | None = None
    decision_note: str | None = None


class ProcedureMutation(BaseModel):
    id: str
    base_version_id: str
    proposed_version_id: str
    proposer_run_id: str | None = None
    status: ApprovalStatus
    diff: str
    rationale: str
    budget_impact: dict[str, Any] = Field(default_factory=dict)
    rollback_version_id: str
    created_at: datetime
    decided_at: datetime | None = None


class AuditorRecap(BaseModel):
    id: str
    run_id: str | None = None
    auditor_model_id: str
    summary: str
    decision: str
    procedure_changes: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime


class IntegrationStatus(BaseModel):
    name: str
    status: str
    endpoint: str | None = None
    last_checked_at: datetime | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ProcedureSnapshot(BaseModel):
    version: ProcedureVersion
    runtime: RuntimeState
    states: list[ProcedureState]
    transitions: list[Transition]
    models: list[ModelProfile]
    runs: list[RunRecord]
    recaps: list[AuditorRecap]
    integrations: list[IntegrationStatus]
    guardrails: GuardrailSnapshot
    approvals: list[ApprovalRecord] = Field(default_factory=list)
    mutations: list[ProcedureMutation] = Field(default_factory=list)


class TickResult(BaseModel):
    run: RunRecord
    previous_state: ProcedureState
    next_state: ProcedureState
    recap: AuditorRecap | None = None
