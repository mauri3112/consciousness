from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class RunStatus(StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    audited = "audited"


class StateKind(StrEnum):
    gather = "gather"
    curate = "curate"
    synthesize = "synthesize"
    validate = "validate"
    publish = "publish"
    audit = "audit"


class ProcedureState(BaseModel):
    id: str
    name: str
    kind: StateKind
    domain: str
    goal_template: str
    prompt_contract: str
    output_contract: str
    tools: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    context_minimum: int = 32_768
    model_policy: str = "cheap-capable"
    x: float = 50
    y: float = 50
    is_current: bool = False


class Transition(BaseModel):
    id: str
    source_id: str
    target_id: str
    weight: float = Field(ge=0, le=2)
    guard: str = "always"
    rationale: str
    active: bool = True


class ModelProfile(BaseModel):
    id: str
    provider: str
    model: str
    context_window: int
    relative_cost: float = Field(ge=0)
    max_run_budget: float = Field(ge=0)
    quality_tier: int = Field(ge=1, le=5)
    strengths: list[str] = Field(default_factory=list)
    open_weights: bool = False
    enabled: bool = True


class SourceLink(BaseModel):
    label: str
    kind: str
    uri: str


class ArtifactPointer(BaseModel):
    label: str
    kind: str
    uri: str
    content_hash: str | None = None


class RunOutput(BaseModel):
    summary: str
    confidence: float = Field(ge=0, le=1)
    changed_resources: list[ArtifactPointer] = Field(default_factory=list)
    source_links: list[SourceLink] = Field(default_factory=list)
    unresolved_risks: list[str] = Field(default_factory=list)
    next_transition_recommendation: str


class RunRecord(BaseModel):
    id: str
    state_id: str
    goal: str
    status: RunStatus
    model_id: str
    context_window: int
    context_used: int
    started_at: datetime
    finished_at: datetime | None = None
    final_thoughts: str | None = None
    changes: list[dict[str, Any]] = Field(default_factory=list)
    output: RunOutput | None = None


class CapabilityPolicy(BaseModel):
    state_id: str
    allowed_tool_patterns: list[str]
    mutation_level: str
    requires_approval: bool
    rationale: str


class LoopControlPolicy(BaseModel):
    manual_pause_enabled: bool = True
    sleep_window: str = "operator-defined"
    base_backoff_seconds: int = 60
    max_backoff_seconds: int = 3600
    max_consecutive_failures: int = 3
    daily_budget_cap: float = 5.0
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
    states: list[ProcedureState]
    transitions: list[Transition]
    models: list[ModelProfile]
    runs: list[RunRecord]
    recaps: list[AuditorRecap]
    integrations: list[IntegrationStatus]
    guardrails: GuardrailSnapshot


class TickResult(BaseModel):
    run: RunRecord
    previous_state: ProcedureState
    next_state: ProcedureState
    recap: AuditorRecap | None = None
