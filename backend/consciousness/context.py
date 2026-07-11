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
) -> ContextManifest:
    items: list[ContextItem] = []
    if state.kind == "gather" and only_memories:
        try:
            payload = only_memories.search(state.goal_template, limit=8)
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
                    )
                )
        except Exception:
            pass

    for run in previous_runs[:6]:
        if not run.output:
            continue
        content = run.output.summary
        items.append(
            ContextItem(
                id=run.id,
                label=f"{run.state_id} run",
                content=content,
                source_uri=f"sqlite://runs/{run.id}",
                content_hash=hashlib.sha256(content.encode()).hexdigest(),
                token_estimate=_estimate_tokens(content),
                score=run.output.confidence,
            )
        )

    items.sort(key=lambda item: (-item.score, item.id))
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
            "Return only the requested structured result. Final thoughts must be concise operational evidence, never hidden reasoning.",
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
        "context": context,
        "previous_output": previous_run.output.model_dump(mode="json") if previous_run and previous_run.output else None,
    }
    return instructions, json.dumps(payload, ensure_ascii=False)


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)
