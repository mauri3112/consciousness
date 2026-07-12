from __future__ import annotations

from .models import (
    AgentAccessPreset,
    CapabilityPolicy,
    PermissionPolicy,
    ProcedureDefinition,
    ProcedureState,
    ResolvedStateAccess,
)


def built_in_access_presets() -> list[AgentAccessPreset]:
    """Portable access contracts; adapters may expose only a subset of configured tools."""
    rows = [
        {
            "id": "coding-agent",
            "name": "Coding agent",
            "description": "Default autonomous coding harness: inspect and edit the workspace, run commands and tests, browse technical sources, and use git without publishing.",
            "agent_type": "software-engineer",
            "permissions": {
                "filesystem": "workspace_write",
                "shell": "workspace_write",
                "network": "restricted",
                "external_writes": "ask",
                "secrets": "deny",
            },
            "tools": [
                "filesystem.read",
                "filesystem.search",
                "filesystem.write",
                "shell.run",
                "git.status",
                "git.diff",
                "web.search",
            ],
            "skills": [
                "codebase-navigation",
                "implementation",
                "testing-debugging",
                "code-review",
            ],
            "allowed_tool_patterns": [
                "filesystem.*",
                "shell.*",
                "git.status",
                "git.diff",
                "web.search",
            ],
            "mutation_level": "workspace_write",
            "requires_approval": True,
            "rationale": "Matches the useful default boundary of coding harnesses: autonomous work inside the checkout, with publication, secrets, and external side effects kept outside the default grant.",
        },
        {
            "id": "coding-reviewer",
            "name": "Coding reviewer",
            "description": "Read-only repository analysis with test execution and evidence-backed review findings.",
            "agent_type": "code-reviewer",
            "permissions": {
                "filesystem": "read_only",
                "shell": "read_only",
                "network": "restricted",
                "external_writes": "deny",
                "secrets": "deny",
            },
            "tools": [
                "filesystem.read",
                "filesystem.search",
                "shell.run",
                "git.status",
                "git.diff",
                "web.search",
            ],
            "skills": [
                "codebase-navigation",
                "code-review",
                "security-review",
                "test-analysis",
            ],
            "allowed_tool_patterns": [
                "filesystem.read",
                "filesystem.search",
                "shell.run",
                "git.status",
                "git.diff",
                "web.search",
            ],
            "mutation_level": "read_only",
            "requires_approval": False,
            "rationale": "Review agents should reproduce and inspect failures without changing the evidence under review.",
        },
        {
            "id": "researcher",
            "name": "Researcher",
            "description": "Source-led research that can browse, read local references, and write durable synthesis artifacts.",
            "agent_type": "research-analyst",
            "permissions": {
                "filesystem": "read_only",
                "shell": "none",
                "network": "unrestricted",
                "external_writes": "deny",
                "secrets": "deny",
            },
            "tools": ["web.search", "web.open", "filesystem.read", "artifact.write"],
            "skills": [
                "source-triangulation",
                "citation",
                "fact-checking",
                "context-compression",
            ],
            "allowed_tool_patterns": ["web.*", "filesystem.read", "artifact.write"],
            "mutation_level": "artifact_write",
            "requires_approval": False,
            "rationale": "Research needs broad retrieval but only a narrow durable-output path, with no ambient shell or external mutation authority.",
        },
        {
            "id": "browser-operator",
            "name": "Browser operator",
            "description": "Operate and verify web interfaces; read freely, but ask before submissions that change external state.",
            "agent_type": "browser-operator",
            "permissions": {
                "filesystem": "read_only",
                "shell": "none",
                "network": "unrestricted",
                "external_writes": "ask",
                "secrets": "ask",
            },
            "tools": [
                "browser.navigate",
                "browser.inspect",
                "browser.click",
                "browser.type",
                "browser.screenshot",
            ],
            "skills": [
                "browser-automation",
                "visual-qa",
                "accessibility-testing",
                "form-safety",
            ],
            "allowed_tool_patterns": ["browser.*"],
            "mutation_level": "external_write",
            "requires_approval": True,
            "rationale": "Navigation and inspection are routine, while form submission, purchase, publication, and account changes cross an external side-effect boundary.",
        },
        {
            "id": "data-analyst",
            "name": "Data analyst",
            "description": "Analyze local datasets and databases, execute bounded queries, and produce reports without modifying source data.",
            "agent_type": "data-analyst",
            "permissions": {
                "filesystem": "workspace_write",
                "shell": "read_only",
                "network": "restricted",
                "external_writes": "deny",
                "secrets": "deny",
            },
            "tools": [
                "filesystem.read",
                "filesystem.write",
                "shell.run",
                "database.query",
                "artifact.write",
            ],
            "skills": ["data-profiling", "sql-analysis", "statistics", "visualization"],
            "allowed_tool_patterns": [
                "filesystem.*",
                "shell.run",
                "database.query",
                "artifact.write",
            ],
            "mutation_level": "artifact_write",
            "requires_approval": False,
            "rationale": "Analysis may create derived files, but source databases and external systems remain read-only.",
        },
        {
            "id": "memory-steward",
            "name": "Memory steward",
            "description": "Curate durable memory with provenance; additive actions are automatic and destructive lifecycle changes require review.",
            "agent_type": "memory-steward",
            "permissions": {
                "filesystem": "none",
                "shell": "none",
                "network": "restricted",
                "external_writes": "ask",
                "secrets": "deny",
            },
            "tools": [
                "only_memories.search",
                "only_memories.navigate",
                "only_memories.versions",
                "only_memories.remember",
                "only_memories.supersede",
                "only_memories.forget",
                "only_memories.restore",
                "only_memories.reinforce",
                "artifact.write",
            ],
            "skills": [
                "deduplication",
                "provenance-writing",
                "graph-stewardship",
                "reversible-forgetting",
            ],
            "allowed_tool_patterns": ["only_memories.*", "artifact.write"],
            "mutation_level": "accepted_write",
            "requires_approval": True,
            "rationale": "Memory quality benefits from durable writes, but superseding and forgetting must remain reversible, attributable, and approval-gated.",
        },
        {
            "id": "procedure-auditor",
            "name": "Procedure auditor",
            "description": "Inspect execution evidence and propose versioned procedure changes without applying them directly.",
            "agent_type": "governance-auditor",
            "permissions": {
                "filesystem": "read_only",
                "shell": "none",
                "network": "none",
                "external_writes": "ask",
                "secrets": "deny",
            },
            "tools": [
                "consciousness.procedure.read",
                "consciousness.procedure.propose_mutation",
                "git.diff",
            ],
            "skills": [
                "procedure-design",
                "model-governance",
                "budget-control",
                "risk-assessment",
            ],
            "allowed_tool_patterns": [
                "consciousness.procedure.read",
                "consciousness.procedure.propose_mutation",
                "git.diff",
            ],
            "mutation_level": "procedure_proposal",
            "requires_approval": True,
            "rationale": "The auditor can recommend graph and policy changes, while immutable versions and operator approval control activation.",
        },
    ]
    return [AgentAccessPreset(**row, built_in=True) for row in rows]


def resolve_state_access(
    definition: ProcedureDefinition, state: ProcedureState
) -> ResolvedStateAccess:
    legacy = next(
        (
            item
            for item in definition.guardrails.capability_policies
            if item.state_id == state.id
        ),
        None,
    )
    if not state.access_preset_id:
        if legacy is None:
            raise ValueError(f"state {state.id!r} has no capability policy")
        return ResolvedStateAccess(
            state_id=state.id,
            permissions=_legacy_permissions(legacy),
            tools=state.tools,
            skills=state.skills,
            allowed_tool_patterns=legacy.allowed_tool_patterns,
            mutation_level=legacy.mutation_level,
            requires_approval=legacy.requires_approval,
            rationale=legacy.rationale,
        )
    preset = next(
        (
            item
            for item in definition.access_presets
            if item.id == state.access_preset_id
        ),
        None,
    )
    if preset is None:
        raise ValueError(
            f"state {state.id!r} references unknown access preset {state.access_preset_id!r}"
        )
    override = state.access_overrides
    return ResolvedStateAccess(
        state_id=state.id,
        preset_id=preset.id,
        permissions=override.permissions or preset.permissions,
        tools=_overlay(preset.tools, override.add_tools, override.remove_tools),
        skills=_overlay(preset.skills, override.add_skills, override.remove_skills),
        allowed_tool_patterns=_overlay(
            preset.allowed_tool_patterns,
            override.add_allowed_tool_patterns,
            override.remove_allowed_tool_patterns,
        ),
        mutation_level=override.mutation_level or preset.mutation_level,
        requires_approval=preset.requires_approval
        if override.requires_approval is None
        else override.requires_approval,
        rationale=override.rationale or preset.rationale,
    )


def resolved_policy(access: ResolvedStateAccess) -> CapabilityPolicy:
    return CapabilityPolicy(
        state_id=access.state_id,
        allowed_tool_patterns=access.allowed_tool_patterns,
        mutation_level=access.mutation_level,
        requires_approval=access.requires_approval,
        rationale=access.rationale,
    )


def apply_resolved_access(
    state: ProcedureState, access: ResolvedStateAccess
) -> ProcedureState:
    return state.model_copy(update={"tools": access.tools, "skills": access.skills})


def _overlay(base: list[str], additions: list[str], removals: list[str]) -> list[str]:
    removed = set(removals)
    return list(
        dict.fromkeys(item for item in [*base, *additions] if item not in removed)
    )


def _legacy_permissions(policy: CapabilityPolicy) -> PermissionPolicy:
    write = policy.mutation_level not in {"read_only", "procedure_proposal"}
    return PermissionPolicy(
        filesystem="workspace_write" if write else "read_only",
        shell="none",
        network="restricted"
        if any(
            "web" in item or "only_memories" in item
            for item in policy.allowed_tool_patterns
        )
        else "none",
        external_writes="ask"
        if policy.requires_approval
        else ("allow" if write else "deny"),
        secrets="deny",
    )
