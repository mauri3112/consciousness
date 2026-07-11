from __future__ import annotations

from .models import CapabilityPolicy, GuardrailSnapshot, LoopControlPolicy, OutputEvidencePolicy


def default_guardrails() -> GuardrailSnapshot:
    return GuardrailSnapshot(
        capability_policies=[
            CapabilityPolicy(
                state_id="gather",
                allowed_tool_patterns=["only_memories.search", "only_memories.navigate", "filesystem.read"],
                mutation_level="read_only",
                requires_approval=False,
                rationale="Gather can inspect context but cannot mutate memory or procedure state.",
            ),
            CapabilityPolicy(
                state_id="curate",
                allowed_tool_patterns=[
                    "only_memories.remember",
                    "only_memories.forget",
                    "only_memories.reinforce_connection",
                ],
                mutation_level="memory_proposal",
                requires_approval=True,
                rationale="Curate can propose lifecycle changes, but reversible forgetting and merges need evidence.",
            ),
            CapabilityPolicy(
                state_id="synthesize",
                allowed_tool_patterns=["artifact.write"],
                mutation_level="artifact_write",
                requires_approval=False,
                rationale="Synthesize writes bridge artifacts and must preserve source links.",
            ),
            CapabilityPolicy(
                state_id="validate",
                allowed_tool_patterns=["only_memories.search", "only_memories.versions", "web.search"],
                mutation_level="read_only",
                requires_approval=False,
                rationale="Validate checks evidence and routes back to gather when support is weak.",
            ),
            CapabilityPolicy(
                state_id="publish",
                allowed_tool_patterns=[
                    "only_memories.remember",
                    "only_memories.forget",
                    "only_memories.restore",
                    "only_memories.reinforce",
                    "artifact.write",
                ],
                mutation_level="accepted_write",
                requires_approval=True,
                rationale="Publish makes accepted changes visible and should record rollback links.",
            ),
            CapabilityPolicy(
                state_id="audit",
                allowed_tool_patterns=["consciousness.procedure.read", "consciousness.procedure.mutate", "git.diff"],
                mutation_level="procedure_proposal",
                requires_approval=True,
                rationale="Audit can propose procedure mutations, but applied changes must be diffed and versioned.",
            ),
        ],
        loop_control=LoopControlPolicy(
            manual_pause_enabled=True,
            sleep_window="operator-defined",
            base_backoff_seconds=60,
            max_backoff_seconds=3600,
            max_consecutive_failures=3,
            daily_budget_cap=5.0,
            degraded_mode="local_only",
        ),
        evidence_policy=OutputEvidencePolicy(
            structured_output_required=True,
            changed_resources_required=True,
            confidence_required=True,
            unresolved_risks_required=True,
            source_links_required=True,
            artifact_pointer_required=True,
        ),
    )
