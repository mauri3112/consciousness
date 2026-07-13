from __future__ import annotations

import hashlib
import json

from .models import ContextItem, ContextManifest, ProcedureState, RunRecord
from .only_memories import OnlyMemoriesClient


def build_context_manifest(
    state: ProcedureState,
    *,
    only_memories: OnlyMemoriesClient | None,
    previous_runs: list[RunRecord],
    memory_space_id: str = "default",
) -> ContextManifest:
    items: list[ContextItem] = []
    if state.kind == "gather" and only_memories:
        payload = only_memories.search(
            state.goal_template,
            limit=12,
            intent="evidence",
            space_ids=[memory_space_id],
            planes=["knowledge"],
            exclude_types=["artifact"],
            include_generated=False,
        )
        for index, memory in enumerate(payload.get("results", [])):
            content = str(memory.get("content", ""))
            memory_id = str(memory.get("id", f"memory-{index}"))
            items.append(
                ContextItem(
                    id=memory_id,
                    label=f"Memory {memory_id}",
                    content=content,
                    source_uri=f"only-memories://memories/{memory_id}",
                    content_hash=hashlib.sha256(content.encode()).hexdigest(),
                    token_estimate=_estimate_tokens(content),
                    score=float(memory.get("rank") or memory.get("base_importance") or 0),
                    source_class="knowledge",
                    evidence_role="primary_retrieval",
                )
            )

    cycle_runs: list[RunRecord] = []
    for run in previous_runs:
        if run.state_id == "audit" and run.status == "succeeded":
            break
        cycle_runs.append(run)
    predecessor_states = {
        "curate": {"gather"},
        "synthesize": {"gather", "curate"},
        "validate": {"curate", "synthesize"},
        "publish": {"curate", "validate"},
    }.get(state.id, set())
    if state.kind == "audit":
        handoff_runs = cycle_runs[:16]
    elif state.kind == "gather":
        handoff_runs = []
    else:
        handoff_runs = [
            run
            for run in cycle_runs
            if run.status == "succeeded" and run.state_id in predecessor_states
        ][:4]

    for run in handoff_runs:
        if state.kind == "audit":
            content = json.dumps(
                {
                    "state": run.state_id,
                    "status": run.status,
                    "attempt": run.attempt,
                    "model_id": run.model_id,
                    "input_tokens": run.input_tokens,
                    "output_tokens": run.output_tokens,
                    "cost": run.cost,
                    "error_category": run.error_category,
                    "error_message": run.error_message,
                    "summary": run.output.summary if run.output else None,
                    "risks": run.output.unresolved_risks if run.output else [],
                },
                sort_keys=True,
            )
        else:
            if not run.output:
                continue
            content = run.output.model_dump_json()
        items.append(
            ContextItem(
                id=run.id,
                label=f"{run.state_id} run",
                content=content,
                source_uri=f"sqlite://runs/{run.id}",
                content_hash=hashlib.sha256(content.encode()).hexdigest(),
                token_estimate=_estimate_tokens(content),
                score=run.output.confidence if run.output else 0,
                source_class="audit_telemetry" if state.kind == "audit" else "run_handoff",
                evidence_role="cycle_telemetry" if state.kind == "audit" else "typed_predecessor",
                origin_run_id=run.id,
            )
        )

    items.sort(key=lambda item: (item.source_class != "knowledge", -item.score, item.id))
    available = max(1, state.context_minimum - state.output_reserve)
    selected: list[ContextItem] = []
    used = 0
    for item in items:
        if used + item.token_estimate > available:
            continue
        selected.append(item)
        used += item.token_estimate
    return ContextManifest(
        items=selected,
        total_estimated_tokens=used,
        reserved_output_tokens=state.output_reserve,
        truncated=len(selected) < len(items),
    )


def assemble_prompt(state: ProcedureState, manifest: ContextManifest, previous_run: RunRecord | None) -> tuple[str, str]:
    instructions = "\n\n".join(
        [
            "You are executing one state in a durable local agent procedure.",
            state.prompt_contract,
            f"Enabled skills: {', '.join(state.skills) if state.skills else 'none'}.",
            "Return only one JSON RunOutput envelope. Do not return the state payload at the top level. Final thoughts must be concise operational evidence, never hidden reasoning.",
        ]
    )
    context = [
        {"id": item.id, "label": item.label, "content": item.content, "source_uri": item.source_uri}
        for item in manifest.items
    ]
    payload = {
        "state": state.id,
        "goal": state.goal_template,
        "output_contract": state.output_contract,
        "available_tools": state.tools,
        "enabled_skills": state.skills,
        "required_result_envelope": _result_envelope_example(state),
        "context": context,
        "context_unresolved_risks": manifest.unresolved_risks,
        "previous_output": previous_run.output.model_dump(mode="json") if previous_run and previous_run.output else None,
    }
    return instructions, json.dumps(payload, ensure_ascii=False)


def _result_envelope_example(state: ProcedureState) -> dict[str, object]:
    payloads: dict[str, dict[str, object]] = {
        "gather": {"kind": "context_bundle", "query": state.goal_template, "items": [], "omitted_items": 0},
        "curate": {"kind": "memory_change_proposal", "changes": []},
        "synthesize": {
            "kind": "synthesis_artifact",
            "title": "Concise artifact title",
            "body": "Evidence-backed synthesis",
            "dependencies": [],
            "suggested_connections": [],
        },
        "validate": {
            "kind": "validation_report",
            "sufficient_evidence": True,
            "findings": [
                {
                    "change_index": 0,
                    "accepted": True,
                    "reason": "Evidence supports the proposed change.",
                    "evidence_ids": [],
                }
            ],
        },
        "publish": {"kind": "publish_receipt", "applied": [], "pending_approval_ids": []},
        "audit": {
            "kind": "audit_decision",
            "decision": "continue",
            "model_recommendation": None,
            "mutation_summary": None,
            "mutation_patch": [],
        },
    }
    return {
        "summary": "Concise operational summary",
        "confidence": 0.8,
        "changed_resources": [],
        "source_links": [],
        "unresolved_risks": [],
        "next_transition_recommendation": {
            "gather": "curate",
            "curate": "synthesize",
            "synthesize": "validate",
            "validate": "publish",
            "publish": "audit",
            "audit": "gather",
        }.get(state.id, "gather"),
        "payload": payloads.get(state.kind, payloads["audit"]),
    }


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)
